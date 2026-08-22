"""Run the self-hosted streaming (WebSocket realtime) stack on Modal.

Mirrors docker-compose.universal-3-5-pro.yml. Compose's four services collapse
into three Modal functions:

  streaming_api  (CPU)  -> WebSocket front door, the public entrypoint
  license_proxy  (CPU)  -> license-and-usage-proxy
  ASR (L40S)            -> a Sandbox, not a function (see below)

nginx (streaming-asr-lb) is dropped: it exists only to route X-Model-Version
across several ASR backends and to load-balance replicas. With one model,
Modal's autoscaler covers the second job and the first is unnecessary.

Compose puts the ASR on a private bridge network. Modal has no inter-container
network, so the ASR's gRPC port is published through a tunnel and its address
handed to the API through a modal.Dict.

The ASR runs in a Sandbox rather than a Function because a tunnel's lifetime is
bound to the function *call*: a @modal.web_server body returns as soon as it has
started its server, Modal tears the call's tunnel down, and the port stops
answering while the container stays up. A Sandbox's tunnel lives as long as the
Sandbox.

Deploy:
    modal run modal_app.py::start_asr     # once; boots the GPU backend
    modal deploy modal_app.py             # the API + license proxy
    modal run modal_app.py::stop_asr      # tears the backend down
"""

import os
import subprocess

import modal

REGISTRY = "344839248844.dkr.ecr.us-west-2.amazonaws.com"
TAG = "release-v1.0.0"

# Image ENTRYPOINTs. Modal prepends an image's ENTRYPOINT to its own runtime
# command, so every image clears it with .entrypoint([]) and each server is
# launched explicitly below.
API_BIN = "/opt/assemblyai/engineering/projects/realtime/api_v2/bin"
ASR_BIN = "/opt/assemblyai/engineering/projects/realtime/asr_u3pro/self_hosted_bin"
PROXY_BIN = "/opt/assemblyai/engineering/projects/realtime/license_and_usage_proxy/bin"

LICENSE_PATH = "/tmp/aai_license.jwt"
ASR_GRPC_PORT = 50051

ecr_secret = modal.Secret.from_name("aai-ecr")
license_secret = modal.Secret.from_name("aai-license")

# Publishes the ASR's tunnel address to streaming_api; Modal gives containers no
# way to address each other directly.
asr_registry = modal.Dict.from_name("aai-streaming-asr-addr", create_if_missing=True)

app = modal.App("aai-streaming-u3pro")


def _vendor_image(repo: str, add_python: str | None = None) -> modal.Image:
    """Build a Modal-compatible image from one of the AssemblyAI ECR images.

    Every vendor image needs the same three adjustments, learned from the sync
    stack: clear the ENTRYPOINT, make sure Modal can find an interpreter, and
    install the Modal client into that interpreter (Modal's runtime-mounted
    client dependencies do not land on these images' sys.path).
    """
    image = modal.Image.from_aws_ecr(
        f"{REGISTRY}/{repo}:{TAG}", secret=ecr_secret, add_python=add_python
    ).entrypoint([])
    if add_python:
        # The standalone interpreter ships pip.
        return image.pip_install(f"modal=={modal.__version__}")
    # Wolfi images expose python3 but omit pip; bootstrap it first.
    return image.run_commands(
        "/usr/bin/python3 -m ensurepip --default-pip",
        f"/usr/bin/python3 -m pip install --no-cache-dir --break-system-packages "
        f"modal=={modal.__version__}",
    )


# asr and api keep their interpreters inside Bazel trees (/opt/python), where
# Modal cannot find them, so both get a standalone one injected into the unused
# /usr/local. Only the proxy image puts python3 on PATH itself.
asr_image = _vendor_image(
    "self-hosted-streaming-asr-universal-3-5-pro", add_python="3.12"
).env(
    {
        "SERVER_PORT": str(ASR_GRPC_PORT),
        "LOGGING_LEVEL": "INFO",
        "USE_STRUCTURED_LOGGING": "False",
        "MAX_OPEN_STREAMS": "32",
        "VLLM_USE_FLASHINFER_SAMPLER": "0",
    }
)
api_image = _vendor_image("self-hosted-streaming-api", add_python="3.12")
proxy_image = _vendor_image("self-hosted-streaming-license-and-usage-proxy")


def _write_license() -> None:
    """Materialize the license from its secret; Modal has no bind mounts."""
    with open(LICENSE_PATH, "w") as fh:
        fh.write(os.environ["AAI_LICENSE_JWT"].strip())


@app.function(image=proxy_image, secrets=[license_secret], timeout=3600)
@modal.web_server(8080, startup_timeout=180)
def license_proxy():
    _write_license()
    subprocess.Popen(
        [PROXY_BIN],
        env={
            **os.environ,
            "HTTP_PORT": "8080",
            "LOGGING_LEVEL": "INFO",
            "USE_STRUCTURED_LOGGING": "False",
            "LICENSE_FILE_PATH": LICENSE_PATH,
        },
    )


@app.local_entrypoint()
def start_asr():
    """Boot the ASR backend in a Sandbox and publish its gRPC address."""
    existing = asr_registry.get("sandbox_id")
    if existing:
        # A registration only counts if the sandbox is still alive; a stale
        # entry from a crashed or terminated sandbox must not block a restart.
        if modal.Sandbox.from_id(existing).poll() is None:
            print(f"sandbox {existing} is already running; run stop_asr first")
            return
        print(f"clearing dead sandbox {existing}")

    # Attach to a looked-up (persistent) app, not this ephemeral `modal run`
    # app: sandboxes are torn down when their owning app stops.
    sandbox_app = modal.App.lookup("aai-streaming-asr", create_if_missing=True)
    sandbox = modal.Sandbox.create(
        ASR_BIN,
        app=sandbox_app,
        image=asr_image,
        gpu="L40S",
        timeout=24 * 60 * 60,
        # Raw TCP, matching compose's AAI_USE_SECURE_CHANNEL_TO_ASR_SERVICE=False.
        # Verified end to end: a plain grpc.insecure_channel to this address
        # completes a health check through the tunnel, trailers included. The
        # TLS/h2 tunnel (encrypted_ports) was not re-tested after an unrelated
        # stale-address bug was fixed, so it may also work.
        #
        # SECURITY: this port is on the public internet and carries audio in the
        # clear, where compose keeps the hop on a private bridge network. See
        # "Security" in the README before running real traffic.
        unencrypted_ports=[ASR_GRPC_PORT],
    )
    host, port = sandbox.tunnels()[ASR_GRPC_PORT].tcp_socket
    asr_registry["address"] = f"{host}:{port}"
    asr_registry["sandbox_id"] = sandbox.object_id
    print(f"ASR sandbox {sandbox.object_id} -> {host}:{port}")
    print("the model takes a few minutes to warm before it accepts streams")


@app.local_entrypoint()
def stop_asr():
    """Terminate the ASR sandbox and clear its registration."""
    sandbox_id = asr_registry.get("sandbox_id")
    if not sandbox_id:
        print("no sandbox registered")
        return
    modal.Sandbox.from_id(sandbox_id).terminate()
    del asr_registry["sandbox_id"]
    del asr_registry["address"]
    print(f"terminated {sandbox_id}")


@app.function(image=api_image, secrets=[license_secret], timeout=3600)
@modal.web_server(8080, startup_timeout=600)
def streaming_api():
    address = asr_registry.get("address")
    if not address:
        raise RuntimeError(
            "no ASR address registered; run `modal run modal_app.py::start_asr`"
        )

    proxy_url = modal.Function.from_name(
        "aai-streaming-u3pro", "license_proxy"
    ).get_web_url()
    print(f"[startup] ASR={address} proxy={proxy_url}", flush=True)

    subprocess.Popen(
        [API_BIN],
        env={
            **os.environ,
            "AAI_WSS_PORT": "8080",
            "AAI_LOG_LEVEL": "INFO",
            "AAI_USE_STRUCTURED_LOGGING": "False",
            "AAI_ASR_ENDPOINT": address,
            "AAI_USE_SECURE_CHANNEL_TO_ASR_SERVICE": "False",
            "AAI_LICENSE_AND_USAGE_PROXY_ENDPOINT": proxy_url,
        },
    )
