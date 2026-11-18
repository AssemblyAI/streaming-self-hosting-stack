"""Run the self-hosted speaker diarization stack on Modal, standalone.

`modal deploy modal_app.py` brings up the whole stack in one command. Compose's
two services become two Modal services in one App, so nothing here depends on
any other deployment:

  license_proxy    (CPU) -> license-and-usage-proxy, a web-server class
  diarization_api  (L4)  -> diarization-api, a Modal Server that resolves the
                            proxy's URL at startup

Deploy:  modal deploy modal_app.py
Tear down: modal app stop aai-diarization

Prerequisites (see README "Store credentials as Modal secrets"):
  modal secret create aai-ecr-credentials AWS_ACCESS_KEY_ID=... \
      AWS_SECRET_ACCESS_KEY=... AWS_SESSION_TOKEN=... AWS_REGION=us-west-2
  modal secret create aai-license LICENSE_JWT="$(cat license.jwt)" \
      USAGE_TRACKING_API_KEY=...   # usage-based licenses only
"""

import os
import signal
import subprocess
import threading

import modal

APP_NAME = "aai-diarization"
REGISTRY = "344839248844.dkr.ecr.us-west-2.amazonaws.com"
DIARIZATION_TAG = "release-v0.2.0"
# Older proxies reject diarization usage reports with a 422.
PROXY_TAG = "release-v1.3.0"

# The API's public endpoint requires a Modal proxy-auth token by default, so a
# guessed URL alone cannot reach it. Set to False for a throwaway test endpoint
# that accepts any non-empty Authorization header (see README "Authentication").
REQUIRE_MODAL_AUTH = os.environ.get("AAI_REQUIRE_MODAL_AUTH", "1") != "0"

# Warm proxy containers. 1 (the default) keeps the proxy always on, which the
# usage-billed path needs: the diarization API POSTs one usage record per
# request, and the proxy holds queued usage in memory with no shutdown flush, so
# a cold-started or scaled-down proxy loses usage. Flat-billed licenses never
# send usage, so they can set PROXY_MIN_CONTAINERS=0. See README "Cost and
# teardown".
PROXY_MIN_CONTAINERS = int(os.environ.get("PROXY_MIN_CONTAINERS", "1"))

# Vendor image ENTRYPOINTs, launched explicitly at container start. Modal
# prepends an image's ENTRYPOINT to its own runtime command, so both images
# clear it with .entrypoint([]); otherwise the vendor binary consumes Modal's
# arguments, starts with default env, and this code never runs.
DIARIZATION_BIN = "/opt/assemblyai/engineering/projects/diarization_api/self_hosted_bin"
PROXY_BIN = "/opt/assemblyai/engineering/projects/realtime/license_and_usage_proxy/bin"

LICENSE_PATH = "/var/aai_license.jwt"
PROXY_PORT = 8080
DIARIZATION_PORT = 8080

ecr_secret = modal.Secret.from_name("aai-ecr-credentials")
license_secret = modal.Secret.from_name("aai-license")

app = modal.App(APP_NAME)


def _vendor_image(repo: str, tag: str) -> modal.Image:
    """A Modal-runnable image from an AssemblyAI ECR image.

    Every vendor image needs the same three adjustments: clear the ENTRYPOINT,
    inject an interpreter Modal can find (the images keep theirs inside Bazel
    runfiles, invisible to Modal), and install the Modal client into it (the
    runtime-mounted client deps do not land on these images' sys.path).
    """
    return (
        modal.Image.from_aws_ecr(
            f"{REGISTRY}/{repo}:{tag}", secret=ecr_secret, add_python="3.12"
        )
        .entrypoint([])
        .pip_install(f"modal=={modal.__version__}")
    )


proxy_image = _vendor_image(
    "self-hosted-streaming-license-and-usage-proxy", PROXY_TAG
).env(
    {
        "HTTP_PORT": str(PROXY_PORT),
        "LOGGING_LEVEL": "INFO",
        "USE_STRUCTURED_LOGGING": "False",
        "LICENSE_FILE_PATH": LICENSE_PATH,
    }
)
diarization_image = _vendor_image("self-hosted-diarization", DIARIZATION_TAG)


# Set by each @modal.exit stop() so the fate-share reaper can tell an intentional
# teardown from an unexpected vendor exit (one service per container).
_stopping = threading.Event()


def _launch(argv: list[str], env: dict[str, str]) -> subprocess.Popen:
    """Start a vendor binary and fate-share it with the container.

    A bare Popen leaves the container 'up' if the binary later exits, so Modal
    keeps routing to a process that is gone. The watcher exits the container on
    the binary's death, turning a silent black hole into a normal replacement.
    """
    proc = subprocess.Popen(argv, env={**os.environ, **env})

    def _reap() -> None:
        proc.wait()
        # An exit while we are not intentionally stopping means the vendor
        # process died on its own (the diarization API also stops itself when
        # the license fails); fail so Modal replaces the container.
        if not _stopping.is_set():
            os._exit(proc.returncode if (proc.returncode or 0) > 0 else 1)

    threading.Thread(target=_reap, daemon=True).start()
    return proc


def _wait_http_ok(url: str, timeout_s: int) -> None:
    import time
    import urllib.request

    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
                last = f"HTTP {resp.status}"
        except Exception as exc:  # noqa: BLE001
            last = repr(exc)
        time.sleep(3)
    raise RuntimeError(f"{url} not ready after {timeout_s}s (last: {last})")


# The proxy is a web-server class rather than a Modal Server: it is a small CPU
# service whose only callers are the diarization containers, and a web function
# can scale to zero (PROXY_MIN_CONTAINERS=0) while a Server answers 503 when
# idle. Called server-side by DiarizationApi, which cannot attach Modal auth
# headers to its requests, so the endpoint stays public (web_server's default).
# Its URL is unguessable but public; see README "Authentication".
@app.cls(
    image=proxy_image,
    min_containers=PROXY_MIN_CONTAINERS,
    scaledown_window=300,
    startup_timeout=180,
    secrets=[license_secret],
)
@modal.concurrent(max_inputs=100)  # one container serves many usage POSTs
class LicenseProxy:
    @modal.web_server(port=PROXY_PORT, startup_timeout=180)
    def start_server(self) -> None:
        # Compose bind-mounts license.jwt; Modal has no bind mounts, so the JWT
        # arrives as a secret and is written to disk at startup. Accept either
        # key name so the same secret works across tooling.
        token = os.environ.get("AAI_LICENSE_JWT") or os.environ["LICENSE_JWT"]
        with open(LICENSE_PATH, "w") as fh:
            fh.write(token.strip())
        # Usage-billed licenses: USAGE_TRACKING_API_KEY in the aai-license
        # secret reaches the proxy here through the environment.
        self.proc = _launch([PROXY_BIN], {})
        _wait_http_ok(f"http://localhost:{PROXY_PORT}/health", 60)

    @modal.exit()
    def stop(self) -> None:
        # The proxy has no shutdown hook of its own: usage still queued in
        # memory at this point is lost, which is why usage-billed deployments
        # keep PROXY_MIN_CONTAINERS >= 1.
        _stopping.set()
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=25)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@app.server(
    image=diarization_image,
    gpu="L4",  # the README minimum is a 16 GB T4; "T4" works, more slowly
    cpu=4,
    memory=16384,
    port=DIARIZATION_PORT,
    unauthenticated=not REQUIRE_MODAL_AUTH,
    # The API diarizes one request at a time (MAX_CONCURRENT_REQUESTS=1) and
    # holds one more for up to 10 s, so scale out on every in-flight request.
    target_concurrency=1,
    max_concurrency=2,
    min_containers=1,  # keep one warm (503 while cold)
    max_containers=4,
    scaledown_window=300,
    startup_timeout=600,  # model load + warmup
    exit_grace_period=60,
    secrets=[license_secret],
)
class DiarizationApi:
    @modal.enter()
    def start(self) -> None:
        # LicenseProxy is a class with a web_server method, so its URL comes from
        # the web endpoint on the bound method (Server.from_name would not
        # resolve it).
        proxy_url = (
            os.environ.get("PROXY_ENDPOINT")
            or modal.Cls.from_name(APP_NAME, "LicenseProxy")().start_server.get_web_url()
        ).rstrip("/")
        print(f"[startup] proxy={proxy_url} require_auth={REQUIRE_MODAL_AUTH}", flush=True)

        self.proc = _launch(
            [DIARIZATION_BIN],
            {
                "HTTP_PORT": str(DIARIZATION_PORT),
                "AAI_ENV": "production",
                "LOGGING_LEVEL": "INFO",
                "USE_STRUCTURED_LOGGING": "False",
                "LICENSE_AND_USAGE_PROXY_ENDPOINT": proxy_url,
                # Limits: customer-overridable via the aai-license secret (or
                # any Server env). User value wins; the compose defaults are only
                # the fallback.
                "MAX_AUDIO_DURATION_MS": os.environ.get("MAX_AUDIO_DURATION_MS", "3600000"),
                "MAX_REQUEST_BYTES": os.environ.get("MAX_REQUEST_BYTES", "1073741824"),
                "INFERENCE_TIMEOUT_SECONDS": os.environ.get("INFERENCE_TIMEOUT_SECONDS", "300"),
                # max_concurrency above assumes 1.
                "MAX_CONCURRENT_REQUESTS": "1",
            },
        )
        _wait_http_ok(f"http://localhost:{DIARIZATION_PORT}/readyz", 540)

    @modal.exit()
    def stop(self) -> None:
        _stopping.set()
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=50)
        except subprocess.TimeoutExpired:
            self.proc.kill()
