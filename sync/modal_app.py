"""Run the self-hosted sync (full-file HTTP) stack on Modal.

Mirrors docker-compose.universal-3-5-pro.yml. Compose's two services become two
Modal functions, since Modal runs one image per container with no sidecars:

  license_proxy  (CPU)  -> license-and-usage-proxy
  sync_api       (L40S) -> sync-api, reaches the proxy over its Modal URL

Deploy:  modal deploy modal_app.py
"""

import os
import subprocess

import modal

REGISTRY = "344839248844.dkr.ecr.us-west-2.amazonaws.com"
TAG = "release-v1.0.0"

# ENTRYPOINTs of the two images, started explicitly in each function body.
# Modal prepends an image's ENTRYPOINT to its own runtime command, so both
# images must clear it with .entrypoint([]) — otherwise the vendor binary
# consumes Modal's arguments, starts with default env, and our code never runs.
PROXY_BIN = "/opt/assemblyai/engineering/projects/realtime/license_and_usage_proxy/bin"
SYNC_BIN = (
    "/opt/assemblyai/engineering/projects/realtime/asr_sync_u3pro/self_hosted_bin"
)

LICENSE_PATH = "/tmp/aai_license.jwt"

ecr_secret = modal.Secret.from_name("aai-ecr")
license_secret = modal.Secret.from_name("aai-license")

app = modal.App("aai-sync-u3pro")

# The proxy image (Wolfi) already has python3 on PATH, so Modal detects it and
# add_python would only shadow a working interpreter.
proxy_image = (
    modal.Image.from_aws_ecr(
        f"{REGISTRY}/self-hosted-streaming-license-and-usage-proxy:{TAG}",
        secret=ecr_secret,
    )
    .entrypoint([])
    # Wolfi base: /usr/bin/python3 is 3.13 but ships no pip, and Modal's
    # runtime-mounted client deps do not land on its sys.path. Installing the
    # client into the image's own interpreter makes resolution deterministic.
    .run_commands(
        "/usr/bin/python3 -m ensurepip --default-pip",
        f"/usr/bin/python3 -m pip install --no-cache-dir --break-system-packages "
        f"modal=={modal.__version__}",
    )
)

# The sync image's interpreter is hermetic (bundled inside Bazel runfiles), so
# nothing named python3 is on PATH and Modal cannot detect a version. Injecting
# a standalone interpreter is safe here precisely because /usr/local is unused,
# so it shadows nothing the ASR binary depends on.
sync_image = (
    modal.Image.from_aws_ecr(
        f"{REGISTRY}/self-hosted-sync-asr-u3-pro:{TAG}",
        secret=ecr_secret,
        add_python="3.12",
    )
    .entrypoint([])
    # As with the proxy, pin Modal's client into the interpreter it will
    # actually launch; the runtime-mounted deps do not resolve in these images.
    .pip_install(f"modal=={modal.__version__}")
)


def _write_license() -> None:
    """Materialize the license from the Modal secret onto disk.

    Compose bind-mounts license.jwt into the container; Modal has no bind
    mounts, so the JWT travels as a secret and is written at startup.
    """
    token = os.environ["AAI_LICENSE_JWT"].strip()
    with open(LICENSE_PATH, "w") as fh:
        fh.write(token)
    print(
        f"[startup] wrote {LICENSE_PATH} ({os.path.getsize(LICENSE_PATH)} bytes)",
        flush=True,
    )


@app.function(
    image=proxy_image,
    secrets=[license_secret],
    timeout=3600,
)
@modal.web_server(8080, startup_timeout=180)
def license_proxy():
    _write_license()
    print(f"[startup] launching {PROXY_BIN}", flush=True)
    subprocess.Popen(
        [PROXY_BIN],
        env={
            **os.environ,
            "HTTP_PORT": "8080",
            "LOGGING_LEVEL": "INFO",
            "USE_STRUCTURED_LOGGING": "False",
            "LICENSE_FILE_PATH": LICENSE_PATH,
            # Licence is flat-billed, so no USAGE_TRACKING_API_KEY is required.
        },
    )


@app.function(
    image=sync_image,
    gpu="L40S",
    secrets=[license_secret],
    timeout=3600,
    scaledown_window=300,
)
@modal.web_server(8080, startup_timeout=900)  # weights load + engine warmup
def sync_api():
    proxy_url = (
        os.environ.get("PROXY_ENDPOINT")
        or modal.Function.from_name("aai-sync-u3pro", "license_proxy").get_web_url()
    )

    subprocess.Popen(
        [SYNC_BIN],
        env={
            **os.environ,
            "HTTP_PORT": "8080",
            "AAI_ENV": "production",
            "LOGGING_LEVEL": "INFO",
            "USE_STRUCTURED_LOGGING": "False",
            "GPU_MONITORING_ENABLED": "False",
            "LICENSE_AND_USAGE_PROXY_ENDPOINT": proxy_url,
            "MAX_AUDIO_DURATION_MS": "120000",
            "MIN_AUDIO_DURATION_MS": "80",
            "MAX_REQUEST_BYTES": "41943040",
            "INFERENCE_TIMEOUT_SECONDS": "30",
            "VLLM_USE_FLASHINFER_SAMPLER": "0",
        },
    )
