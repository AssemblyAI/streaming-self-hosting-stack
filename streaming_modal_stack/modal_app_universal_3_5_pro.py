"""Run the self-hosted streaming Universal-3.5 Pro stack on Modal, standalone.

`modal deploy modal_app_universal_3_5_pro.py` brings up the whole stack in one
command; nothing depends on any other deployment. Compose's four services become
three Modal Servers in one App:

  streaming_api  (CPU)   -> WebSocket front door, the public entrypoint
  asr            (L40S)  -> Universal-3.5 Pro gRPC backend
  license_proxy  (CPU)   -> license-and-usage-proxy

nginx (streaming-asr-lb) is dropped: it only routes X-Model-Version across
several ASR backends, and this stack serves one model. streaming_api resolves
the ASR and proxy URLs from the same App at startup, so there is no manual
wiring step.

The API dials the ASR over Modal's TLS edge (h2_enabled advertises ALPN h2, so a
standard gRPC client connects) with AAI_USE_SECURE_CHANNEL_TO_ASR_SERVICE=True,
replacing compose's private bridge network. See README "Security".

Deploy:  modal deploy modal_app_universal_3_5_pro.py
Tear down: modal app stop aai-streaming-u3pro
"""

import os
import signal
import subprocess
import threading

import modal

APP_NAME = "aai-streaming-u3pro"
REGISTRY = "344839248844.dkr.ecr.us-west-2.amazonaws.com"
# streaming-api and the u3-5-pro ASR ship on release-v1.0.1 (the API image
# carries the WARNING-not-ERROR handshake-logging fix, DeepLearning #19523);
# the license-and-usage-proxy has no v1.0.1 and stays on v1.0.0.
API_TAG = "release-v1.0.1"
ASR_TAG = "release-v1.0.1"
PROXY_TAG = "release-v1.0.0"
ASR_GRPC_PORT = 50051

# See sync_modal_stack/modal_app.py: the WebSocket API requires a Modal proxy-auth token by
# default. Set AAI_REQUIRE_MODAL_AUTH=0 for a throwaway test endpoint.
REQUIRE_MODAL_AUTH = os.environ.get("AAI_REQUIRE_MODAL_AUTH", "1") != "0"

API_BIN = "/opt/assemblyai/engineering/projects/realtime/api_v2/bin"
ASR_BIN = "/opt/assemblyai/engineering/projects/realtime/asr_u3pro/self_hosted_bin"
PROXY_BIN = "/opt/assemblyai/engineering/projects/realtime/license_and_usage_proxy/bin"

LICENSE_PATH = "/var/aai_license.jwt"

ecr_secret = modal.Secret.from_name("aai-ecr-credentials")
license_secret = modal.Secret.from_name("aai-license")

app = modal.App(APP_NAME)


def _vendor_image(repo: str, tag: str) -> modal.Image:
    """A Modal-runnable image from an AssemblyAI ECR image (see sync_modal_stack/modal_app.py)."""
    return (
        modal.Image.from_aws_ecr(
            f"{REGISTRY}/{repo}:{tag}", secret=ecr_secret, add_python="3.12"
        )
        .entrypoint([])
        .pip_install(f"modal=={modal.__version__}")
    )


asr_image = _vendor_image("self-hosted-streaming-asr-universal-3-5-pro", ASR_TAG)
api_image = _vendor_image("self-hosted-streaming-api", API_TAG)
proxy_image = _vendor_image("self-hosted-streaming-license-and-usage-proxy", PROXY_TAG)


# Set by each @modal.exit stop() so the fate-share reaper can tell an intentional
# teardown from an unexpected vendor exit (one server per container).
_stopping = threading.Event()


def _launch(argv: list[str], env: dict[str, str]) -> subprocess.Popen:
    """Start a vendor binary and fate-share it with the container (see sync_modal_stack/modal_app.py)."""
    proc = subprocess.Popen(argv, env={**os.environ, **env})

    def _reap() -> None:
        proc.wait()
        # An exit while we are not intentionally stopping means the vendor
        # process died on its own; fail so Modal replaces the container. A clean
        # @modal.exit teardown sets _stopping first, so stay quiet and let the
        # exit handler finish (the container then exits 0).
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


@app.server(
    image=proxy_image,
    port=8080,
    unauthenticated=True,  # called server-side by streaming_api; see README "Authentication"
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
        token = os.environ.get("AAI_LICENSE_JWT") or os.environ["LICENSE_JWT"]
        with open(LICENSE_PATH, "w") as fh:
            fh.write(token.strip())
        self.proc = _launch([PROXY_BIN], {})
        _wait_http_ok("http://localhost:8080/health", 60)

    @modal.exit()
    def stop(self) -> None:
        _stopping.set()
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=25)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@app.server(
    image=asr_image,
    gpu="L40S",
    cpu=4,
    memory=16384,
    port=ASR_GRPC_PORT,
    h2_enabled=True,  # gRPC needs ALPN h2 across the TLS edge
    # Called server-side by streaming_api over gRPC, which cannot attach Modal
    # auth headers, so the endpoint is unauthenticated. See README "Security".
    unauthenticated=True,
    target_concurrency=32,  # mirrors MAX_OPEN_STREAMS
    min_containers=1,  # ~5 min warm-up; never scale a realtime backend to zero
    max_containers=4,
    buffer_containers=1,  # scale-up lead time is the warm-up, so keep a warm spare
    scaledown_window=600,
    startup_timeout=900,
    exit_grace_period=600,  # let in-flight streams drain instead of dying
    env={
        "SERVER_PORT": str(ASR_GRPC_PORT),
        "LOGGING_LEVEL": "INFO",
        "USE_STRUCTURED_LOGGING": "False",
        "MAX_OPEN_STREAMS": os.environ.get("MAX_OPEN_STREAMS", "32"),
        "VLLM_USE_FLASHINFER_SAMPLER": "0",
    },
)
class Asr:
    @modal.enter()
    def start(self) -> None:
        self.proc = _launch([ASR_BIN], {})
        # Gate readiness on the same probe compose uses, so Modal never routes a
        # session to a cold engine.
        import time

        deadline = time.monotonic() + 840
        while time.monotonic() < deadline:
            if subprocess.run(
                ["grpc_health_probe", f"-addr=:{ASR_GRPC_PORT}"],
                capture_output=True,
            ).returncode == 0:
                return
            time.sleep(5)
        raise RuntimeError("ASR engine not serving after warm-up window")

    @modal.exit()
    def stop(self) -> None:
        _stopping.set()
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=570)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@app.server(
    image=api_image,
    port=8080,
    unauthenticated=not REQUIRE_MODAL_AUTH,
    cpu=1,
    memory=2048,
    target_concurrency=32,  # ~32 sessions per CPU container
    min_containers=1,
    max_containers=4,
    nonpreemptible=True,  # holds live WebSocket sessions
    scaledown_window=600,
    startup_timeout=600,
    exit_grace_period=600,
    secrets=[license_secret],
)
class StreamingApi:
    @modal.enter()
    def start(self) -> None:
        asr_host = (
            os.environ.get("ASR_ENDPOINT")
            or modal.Server.from_name(APP_NAME, "Asr").get_url().split("://", 1)[1].rstrip("/")
        )
        proxy_url = os.environ.get("PROXY_ENDPOINT") or modal.Server.from_name(
            APP_NAME, "LicenseProxy"
        ).get_url().rstrip("/")
        print(f"[startup] ASR={asr_host}:443 proxy={proxy_url} require_auth={REQUIRE_MODAL_AUTH}", flush=True)

        self.proc = _launch(
            [API_BIN],
            {
                "AAI_WSS_PORT": "8080",
                "AAI_LOG_LEVEL": "INFO",
                "AAI_USE_STRUCTURED_LOGGING": "False",
                "AAI_ASR_ENDPOINT": f"{asr_host}:443",
                "AAI_USE_SECURE_CHANNEL_TO_ASR_SERVICE": "True",
                "AAI_LICENSE_AND_USAGE_PROXY_ENDPOINT": proxy_url,
            },
        )
        _wait_http_ok("http://localhost:8080/v3/ws/health", 120)

    @modal.exit()
    def stop(self) -> None:
        _stopping.set()
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=570)
        except subprocess.TimeoutExpired:
            self.proc.kill()
