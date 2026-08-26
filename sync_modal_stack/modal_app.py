"""Run the self-hosted sync (full-file HTTP) stack on Modal as a standalone app.

`modal deploy modal_app.py` brings up the whole stack in one command. Compose's
two services become two Modal Servers in one App, so nothing here depends on any
other deployment:

  license_proxy  (CPU)  -> license-and-usage-proxy
  sync_api       (L40S) -> sync-api, which resolves the proxy's URL at startup

Deploy:  modal deploy modal_app.py
Tear down: modal app stop aai-sync-u3pro

Prerequisites (see README "Deploying on Modal"):
  modal secret create aai-ecr-credentials AWS_ACCESS_KEY_ID=... \
      AWS_SECRET_ACCESS_KEY=... AWS_SESSION_TOKEN=... AWS_REGION=us-west-2
  modal secret create aai-license LICENSE_JWT="$(cat license.jwt)"   # +USAGE_TRACKING_API_KEY if usage-billed
"""

import os
import signal
import subprocess

import modal

APP_NAME = "aai-sync-u3pro"
REGISTRY = "344839248844.dkr.ecr.us-west-2.amazonaws.com"
TAG = "release-v1.0.0"

# The API's public endpoint requires a Modal proxy-auth token by default, so a
# guessed URL alone cannot reach it. Set to False for a throwaway test endpoint
# that accepts any non-empty Authorization header (see README "Authentication").
REQUIRE_MODAL_AUTH = os.environ.get("AAI_REQUIRE_MODAL_AUTH", "1") != "0"

# Vendor image ENTRYPOINTs, launched explicitly in each Server's @modal.enter.
# Modal prepends an image's ENTRYPOINT to its own runtime command, so both
# images clear it with .entrypoint([]); otherwise the vendor binary consumes
# Modal's arguments, starts with default env, and this code never runs.
SYNC_BIN = "/opt/assemblyai/engineering/projects/realtime/asr_sync_u3pro/self_hosted_bin"
PROXY_BIN = "/opt/assemblyai/engineering/projects/realtime/license_and_usage_proxy/bin"

LICENSE_PATH = "/var/aai_license.jwt"

ecr_secret = modal.Secret.from_name("aai-ecr-credentials")
license_secret = modal.Secret.from_name("aai-license")

app = modal.App(APP_NAME)


def _vendor_image(repo: str) -> modal.Image:
    """A Modal-runnable image from an AssemblyAI ECR image.

    Every vendor image needs the same three adjustments: clear the ENTRYPOINT,
    inject an interpreter Modal can find (the images keep theirs inside Bazel
    runfiles, invisible to Modal), and install the Modal client into it (the
    runtime-mounted client deps do not land on these images' sys.path).
    """
    return (
        modal.Image.from_aws_ecr(
            f"{REGISTRY}/{repo}:{TAG}", secret=ecr_secret, add_python="3.12"
        )
        .entrypoint([])
        .pip_install(f"modal=={modal.__version__}")
    )


proxy_image = _vendor_image("self-hosted-streaming-license-and-usage-proxy")
sync_image = _vendor_image("self-hosted-sync-asr-u3-pro")


def _launch(argv: list[str], env: dict[str, str]) -> subprocess.Popen:
    """Start a vendor binary and fate-share it with the container.

    A bare Popen leaves the container 'up' if the binary later exits, so Modal
    keeps routing to a process that is gone. The watcher exits the container on
    the binary's death, turning a silent black hole into a normal replacement.
    """
    proc = subprocess.Popen(argv, env={**os.environ, **env})

    import threading

    def _reap() -> None:
        proc.wait()
        os._exit(proc.returncode or 1)

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


@app.server(
    image=proxy_image,
    port=8080,
    # Called server-side by sync_api, which cannot attach Modal auth headers to
    # its request, so this endpoint must accept unauthenticated traffic. Its URL
    # is unguessable but public; see README "Authentication".
    unauthenticated=True,
    cpu=1,
    memory=2048,
    min_containers=1,
    max_containers=1,
    startup_timeout=180,
    exit_grace_period=30,
    secrets=[license_secret],
    env={
        "HTTP_PORT": "8080",
        "LOGGING_LEVEL": "INFO",
        "USE_STRUCTURED_LOGGING": "False",
        "LICENSE_FILE_PATH": LICENSE_PATH,
    },
)
class LicenseProxy:
    @modal.enter()
    def start(self) -> None:
        # Compose bind-mounts license.jwt; Modal has no bind mounts, so the JWT
        # arrives as a secret and is written to disk at startup. Accept either
        # key name so the same secret works across tooling.
        token = os.environ.get("AAI_LICENSE_JWT") or os.environ["LICENSE_JWT"]
        with open(LICENSE_PATH, "w") as fh:
            fh.write(token.strip())
        # Usage-billed licenses: add USAGE_TRACKING_API_KEY to the aai-license
        # secret and it reaches the proxy here through the environment. Nothing
        # else to change.
        self.proc = _launch([PROXY_BIN], {})
        _wait_http_ok("http://localhost:8080/health", 60)

    @modal.exit()
    def stop(self) -> None:
        # Graceful stop lets the proxy flush queued usage before exit.
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=25)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@app.server(
    image=sync_image,
    gpu="L40S",
    cpu=4,
    memory=16384,
    port=8080,
    unauthenticated=not REQUIRE_MODAL_AUTH,
    # Scale-out signal: concurrent in-flight /transcribe requests (GPU-bound).
    # Start conservative and tune against bench/harness.py on your hardware.
    target_concurrency=8,
    min_containers=1,  # ~2-4 min cold start; keep one warm (503 while cold)
    max_containers=4,
    scaledown_window=300,
    startup_timeout=900,  # weights load + CUDA-graph capture
    exit_grace_period=60,  # requests are short (INFERENCE_TIMEOUT_SECONDS below)
    secrets=[license_secret],
)
class SyncApi:
    @modal.enter()
    def start(self) -> None:
        proxy_url = os.environ.get("PROXY_ENDPOINT") or modal.Server.from_name(
            APP_NAME, "LicenseProxy"
        ).get_url().rstrip("/")
        print(f"[startup] proxy={proxy_url} require_auth={REQUIRE_MODAL_AUTH}", flush=True)

        self.proc = _launch(
            [SYNC_BIN],
            {
                "HTTP_PORT": "8080",
                "AAI_ENV": "production",
                "LOGGING_LEVEL": "INFO",
                "USE_STRUCTURED_LOGGING": "False",
                "GPU_MONITORING_ENABLED": "False",
                "LICENSE_AND_USAGE_PROXY_ENDPOINT": proxy_url,
                # Audio limits: customer-overridable via the aai-license secret
                # (or any Server env). User value wins; the compose defaults are
                # only the fallback. Raising MAX_AUDIO_DURATION_MS usually means
                # raising MAX_REQUEST_BYTES and INFERENCE_TIMEOUT_SECONDS too.
                "MAX_AUDIO_DURATION_MS": os.environ.get("MAX_AUDIO_DURATION_MS", "120000"),
                "MIN_AUDIO_DURATION_MS": os.environ.get("MIN_AUDIO_DURATION_MS", "80"),
                "MAX_REQUEST_BYTES": os.environ.get("MAX_REQUEST_BYTES", "41943040"),
                "INFERENCE_TIMEOUT_SECONDS": os.environ.get("INFERENCE_TIMEOUT_SECONDS", "30"),
                "VLLM_USE_FLASHINFER_SAMPLER": "0",
            },
        )
        _wait_http_ok("http://localhost:8080/readyz", 840)

    @modal.exit()
    def stop(self) -> None:
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=50)
        except subprocess.TimeoutExpired:
            self.proc.kill()
