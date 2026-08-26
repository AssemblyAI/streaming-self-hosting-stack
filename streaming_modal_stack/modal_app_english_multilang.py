"""Run the self-hosted streaming English + Multilingual stack on Modal, standalone.

`modal deploy modal_app_english_multilang.py` brings up the whole stack in one
command. This stack serves TWO ASR models, so unlike the single-model
Universal-3.5 Pro stack it keeps compose's routing layer: clients pick a model
with speech_model ("en-default" or "ml-default"), the API forwards it as the
gRPC metadata x-model-version, and an nginx load balancer routes to the matching
backend. Five Modal Servers in one App:

  streaming_api  (CPU)   -> WebSocket front door, the public entrypoint
  lb             (CPU)   -> nginx, routes x-model-version to the two backends
  asr_english    (L40S)  -> English gRPC backend
  asr_multilang  (L40S)  -> Multilingual gRPC backend
  license_proxy  (CPU)   -> license-and-usage-proxy

Every backend hop crosses Modal's TLS edge (h2_enabled advertises ALPN h2 for
gRPC); the API and nginx dial with TLS. See README "Security".

Deploy:  modal deploy modal_app_english_multilang.py
Tear down: modal app stop aai-streaming-english-multilang
"""

import os
import signal
import subprocess

import modal

APP_NAME = "aai-streaming-english-multilang"
REGISTRY = "344839248844.dkr.ecr.us-west-2.amazonaws.com"
# The English/Multilingual images ship on their own release line, separate from
# the Universal-3.5 Pro images (see streaming/.env.example).
TAG = "release-v0.6.0"
PROXY_TAG = "release-v1.0.0"
ASR_GRPC_PORT = 50051

REQUIRE_MODAL_AUTH = os.environ.get("AAI_REQUIRE_MODAL_AUTH", "1") != "0"

ENGLISH_BIN = "/opt/assemblyai/engineering/projects/realtime/asr_server/asr_server_bin"
MULTILANG_BIN = "/opt/assemblyai/engineering/projects/realtime/asr_server/ml_asr_server_bin"
API_BIN = "/opt/assemblyai/engineering/projects/realtime/api_v2/bin"
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


english_image = _vendor_image("self-hosted-streaming-asr-english", TAG)
multilang_image = _vendor_image("self-hosted-streaming-asr-multilang", TAG)
api_image = _vendor_image("self-hosted-streaming-api", PROXY_TAG)
proxy_image = _vendor_image("self-hosted-streaming-license-and-usage-proxy", PROXY_TAG)
# nginx routes gRPC by x-model-version; no ECR pull needed. A Debian base gives
# Modal a detectable interpreter plus its client, alongside nginx.
lb_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("nginx")
    .pip_install(f"modal=={modal.__version__}")
)


def _launch(argv: list[str], env: dict[str, str]) -> subprocess.Popen:
    """Start a binary and fate-share it with the container (see sync_modal_stack/modal_app.py)."""
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


def _asr_ready(port: int, timeout_s: int) -> None:
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if subprocess.run(
            ["grpc_health_probe", f"-addr=:{port}"], capture_output=True
        ).returncode == 0:
            return
        time.sleep(5)
    raise RuntimeError(f"ASR not serving on :{port} after {timeout_s}s")


_ASR_KW = dict(
    gpu="L40S",
    cpu=4,
    memory=16384,
    port=ASR_GRPC_PORT,
    h2_enabled=True,
    unauthenticated=True,  # dialed server-side (via nginx) over gRPC; see README "Security"
    target_concurrency=32,
    min_containers=1,
    max_containers=4,
    buffer_containers=1,
    scaledown_window=600,
    startup_timeout=900,
    exit_grace_period=600,
)
_ASR_ENV = {
    "SERVER_PORT": str(ASR_GRPC_PORT),
    "LOGGING_LEVEL": "INFO",
    "USE_STRUCTURED_LOGGING": "False",
    "MAX_OPEN_STREAMS": os.environ.get("MAX_OPEN_STREAMS", "32"),
    "VLLM_USE_FLASHINFER_SAMPLER": "0",
}


@app.server(image=proxy_image, port=8080, unauthenticated=True, cpu=1, memory=2048,
            min_containers=1, max_containers=1, startup_timeout=180, exit_grace_period=30,
            secrets=[license_secret],
            env={"HTTP_PORT": "8080", "LOGGING_LEVEL": "INFO",
                 "USE_STRUCTURED_LOGGING": "False", "LICENSE_FILE_PATH": LICENSE_PATH})
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
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=25)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@app.server(image=english_image, env=_ASR_ENV, **_ASR_KW)
class AsrEnglish:
    @modal.enter()
    def start(self) -> None:
        self.proc = _launch([ENGLISH_BIN], {})
        _asr_ready(ASR_GRPC_PORT, 840)

    @modal.exit()
    def stop(self) -> None:
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=570)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@app.server(image=multilang_image, env=_ASR_ENV, **_ASR_KW)
class AsrMultilang:
    @modal.enter()
    def start(self) -> None:
        self.proc = _launch([MULTILANG_BIN], {})
        _asr_ready(ASR_GRPC_PORT, 840)

    @modal.exit()
    def stop(self) -> None:
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=570)
        except subprocess.TimeoutExpired:
            self.proc.kill()


# nginx config: route the gRPC metadata x-model-version to the matching backend
# over Modal's TLS edge (grpcs, ALPN h2). Backend hosts are resolved from the
# App at startup and substituted in. Public DNS resolves the .modal.direct
# hosts, so a public resolver is used.
_NGINX_CONF = """
events {{ worker_connections 1024; }}
http {{
  access_log /dev/stdout;
  error_log  /dev/stderr info;
  resolver 1.1.1.1 8.8.8.8 valid=30s;
  map $http_x_model_version $asr_backend {{
    default        {english}:443;
    en-default     {english}:443;
    ml-default     {multilang}:443;
  }}
  keepalive_timeout 10h;
  # Plain HTTP/1.1 readiness port for the startup probe; the gRPC listener below
  # is h2-only and cannot answer an HTTP/1.1 GET.
  server {{
    listen 8081;
    location = /health {{ access_log off; return 200 "OK\\n"; }}
  }}
  server {{
    listen 8080 http2;
    client_max_body_size 0;
    location / {{
      grpc_pass grpcs://$asr_backend;
      grpc_ssl_server_name on;
      grpc_connect_timeout 75s;
      grpc_read_timeout 10h;
      grpc_send_timeout 10h;
      grpc_socket_keepalive on;
    }}
  }}
}}
"""


@app.server(image=lb_image, port=8080, h2_enabled=True, unauthenticated=True,
            cpu=1, memory=1024, min_containers=1, max_containers=1,
            startup_timeout=120, exit_grace_period=30)
class Lb:
    @modal.enter()
    def start(self) -> None:
        english = modal.Server.from_name(APP_NAME, "AsrEnglish").get_url().split("://", 1)[1].rstrip("/")
        multilang = modal.Server.from_name(APP_NAME, "AsrMultilang").get_url().split("://", 1)[1].rstrip("/")
        with open("/etc/nginx/nginx.conf", "w") as fh:
            fh.write(_NGINX_CONF.format(english=english, multilang=multilang))
        print(f"[startup] lb -> en={english} ml={multilang}", flush=True)
        self.proc = _launch(["nginx", "-g", "daemon off;"], {})
        _wait_http_ok("http://localhost:8081/health", 30)

    @modal.exit()
    def stop(self) -> None:
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=25)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@app.server(image=api_image, port=8080, unauthenticated=not REQUIRE_MODAL_AUTH,
            cpu=1, memory=2048, target_concurrency=32, min_containers=1,
            max_containers=4, nonpreemptible=True, scaledown_window=600,
            startup_timeout=600, exit_grace_period=600, secrets=[license_secret])
class StreamingApi:
    @modal.enter()
    def start(self) -> None:
        lb_host = (
            os.environ.get("ASR_ENDPOINT")
            or modal.Server.from_name(APP_NAME, "Lb").get_url().split("://", 1)[1].rstrip("/")
        )
        proxy_url = os.environ.get("PROXY_ENDPOINT") or modal.Server.from_name(
            APP_NAME, "LicenseProxy"
        ).get_url().rstrip("/")
        print(f"[startup] LB={lb_host}:443 proxy={proxy_url} require_auth={REQUIRE_MODAL_AUTH}", flush=True)

        self.proc = _launch(
            [API_BIN],
            {
                "AAI_WSS_PORT": "8080",
                "AAI_LOG_LEVEL": "INFO",
                "AAI_USE_STRUCTURED_LOGGING": "False",
                "AAI_ASR_ENDPOINT": f"{lb_host}:443",
                "AAI_USE_SECURE_CHANNEL_TO_ASR_SERVICE": "True",
                "AAI_LICENSE_AND_USAGE_PROXY_ENDPOINT": proxy_url,
            },
        )
        _wait_http_ok("http://localhost:8080/v3/ws/health", 120)

    @modal.exit()
    def stop(self) -> None:
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=570)
        except subprocess.TimeoutExpired:
            self.proc.kill()
