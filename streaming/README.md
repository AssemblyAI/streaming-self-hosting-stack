# Streaming (WebSocket) self-hosted stack

Real-time transcription over a WebSocket connection. Run all commands from this
`streaming/` directory.

> Prerequisites (license, Docker, GPU runtime, ECR auth) and the shared
> license-and-usage-proxy (usage reporting, license status endpoint, proxy
> production recommendations) are documented in the [top-level README](../README.md).

## Choosing a stack

Two stacks are shipped. Pick the one that matches the model you want to serve —
they are mutually exclusive (run one at a time):

| File | Models served | GPU requirement |
|------|--------------|-----------------|
| `docker-compose.english-multilang.yml` | Universal English + Multilingual | NVIDIA T4+ per ASR container |
| `docker-compose.universal-3-5-pro.yml` | Universal-3.5 Pro | NVIDIA L40S, RTX PRO 4500, or RTX PRO 6000 |

To switch between stacks, run `docker compose -f <file> down` before starting the other.

## Services included

Both stacks include:
- **streaming-api**: Gateway API service handling WebSocket connections.
- **streaming-asr-lb**: nginx load balancer for ASR services with header-based routing.
- **license-and-usage-proxy**: License validation and usage reporting (see [top-level README](../README.md#shared-component-license-and-usage-proxy)).

ASR backends differ by stack:
- Universal stack (`docker-compose.english-multilang.yml`): `streaming-asr-english` and `streaming-asr-multilang`.
- Universal-3.5 Pro stack (`docker-compose.universal-3-5-pro.yml`): `streaming-asr-universal-3-5-pro`.

## Connection flow

**Universal stack** (`docker-compose.english-multilang.yml`):
```
Websocket client → streaming-api:8080 (WebSocket)
                          │
                          ├─ Usage reporting     ───────→ license-and-usage-proxy:8080 [if usage-based billing] ────→ https://usage-tracker.assemblyai.com
                          │                               │
                          ├─ License validation  ─────────┘
                          │
                          └─ ASR requests        ───────→ streaming-asr-lb:80 → Header-based routing (X-Model-Version):
                                                                                ├── en-default → streaming-asr-english:50051 (gRPC)
                                                                                └── ml-default → streaming-asr-multilang:50051 (gRPC)
```

**Universal-3.5 Pro stack** (`docker-compose.universal-3-5-pro.yml`):
```
Websocket client → streaming-api:8080 (WebSocket)
                          │
                          ├─ Usage reporting     ───────→ license-and-usage-proxy:8080 [if usage-based billing] ────→ https://usage-tracker.assemblyai.com
                          │                               │
                          ├─ License validation  ─────────┘
                          │
                          └─ ASR requests        ───────→ streaming-asr-lb:80 → Header-based routing (X-Model-Version):
                                                                                └── universal-3-5-pro → streaming-asr-universal-3-5-pro:50051 (gRPC)
```

Both stacks share the same `nginx_streaming_asr.conf`, which routes by
`X-Model-Version` header. Each stack only deploys the backends it needs —
websocket clients should use a `speech_model` query parameter value that routes
to an available backend.

## Setup

Complete the [shared prerequisites](../README.md#prerequisites-all-services)
(GPU runtime, ECR authentication, license file) first.

Copy the env reference and set the image variables for the stack you plan to run:

```bash
cp .env.example .env
```

```bash
# Required for both stacks:
STREAMING_API_IMAGE=<CUSTOM_IMAGE>
LICENSE_AND_USAGE_PROXY_IMAGE=<CUSTOM_IMAGE>

# Required for the Universal stack (docker-compose.english-multilang.yml):
STREAMING_ASR_ENGLISH_IMAGE=<CUSTOM_IMAGE>
STREAMING_ASR_MULTILANG_IMAGE=<CUSTOM_IMAGE>

# Required for the Universal-3.5 Pro stack (docker-compose.universal-3-5-pro.yml):
STREAMING_ASR_UNIVERSAL_3_5_PRO_IMAGE=<CUSTOM_IMAGE>
```

Place your `license.jwt` in this directory (or repoint `LICENSE_FILE_PATH` in the compose file).

## Run

Both stacks use the same `streaming-api`, load balancer, and license proxy —
they differ only in the ASR backend. For the Universal-3.5 Pro stack, websocket clients
should set query parameter `speech_model` to `universal-3-5-pro` so the load balancer
routes to the Universal-3.5 Pro backend.

**Universal stack** (English + Multilingual):
```bash
docker compose -f docker-compose.english-multilang.yml up -d
docker compose -f docker-compose.english-multilang.yml logs -f

# Check service status
docker compose -f docker-compose.english-multilang.yml ps

# Stop services before switching stacks
docker compose -f docker-compose.english-multilang.yml down
```

**Universal-3.5 Pro stack**:
```bash
docker compose -f docker-compose.universal-3-5-pro.yml up -d
docker compose -f docker-compose.universal-3-5-pro.yml logs -f

# Check service status
docker compose -f docker-compose.universal-3-5-pro.yml ps

# Stop services before switching stacks
docker compose -f docker-compose.universal-3-5-pro.yml down
```

## Service endpoints

- **WebSocket**: `ws://localhost:8080`

## Running the streaming example

A Python example script is provided to demonstrate how to stream audio to the stack.

_Note_: You can initiate a session as soon as the relevant ASR container is
healthy. `streaming-asr-english` and `streaming-asr-multilang` log "Ready to
serve!" when ready (typically ~2 min); `streaming-asr-universal-3-5-pro` logs
"U3Pro ASR Server ready!" when warm (typically ~5 min).

Change into the `example/` directory:
```bash
cd example
```

Create and activate a fresh virtual environment:
```bash
python -m venv streaming_venv
source streaming_venv/bin/activate
```

Install the required packages:
```bash
pip install -r requirements.txt
```

The example script (`example_with_prerecorded_audio_file.py`) accepts several CLI arguments:

**Basic usage:**
- Universal stack English:
    ```bash
    python example_with_prerecorded_audio_file.py --audio-file "example_audio_file.wav" --endpoint "ws://localhost:8080" --speech-model "universal-streaming-english"
    ```
- Universal stack Multilingual:
    ```bash
    python example_with_prerecorded_audio_file.py --audio-file "example_audio_file.wav" --endpoint "ws://localhost:8080" --speech-model "universal-streaming-multilingual"
    ```
- Universal-3.5 Pro stack:
    ```bash
    python example_with_prerecorded_audio_file.py --audio-file "example_audio_file.wav" --endpoint "ws://localhost:8080" --speech-model "universal-3-5-pro"
    ```

**Command-line arguments:**

| Argument | Description                                            | Default                  |
|----------|--------------------------------------------------------|--------------------------|
| `--audio-file` | Path to the audio file to transcribe                   | `example_audio_file.wav` |
| `--endpoint` | WebSocket endpoint URL                                 | `ws://localhost:8080`     |
| `--speech-model` | Speech model to use (e.g., 'universal-streaming-multilingual') | ``               |

**View help:**
```bash
python example_with_prerecorded_audio_file.py --help
```

## Configuration

### Nginx configuration

**ASR Load Balancer** (`nginx_streaming_asr.conf`):
- gRPC proxying to ASR services.
- Routes to the English, Multilingual, or Universal-3.5 Pro backend based on the `X-Model-Version` header value.

### Usage reporting

The license-and-usage-proxy's billing modes and behavior are documented in the
[top-level README](../README.md#usage-reporting).

## Monitoring & debugging

```bash
# Container status
docker compose -f <compose-file> ps

# Resource usage
docker stats
```

### Debug commands

```bash
# Check nginx configuration
docker compose -f docker-compose.english-multilang.yml exec streaming-asr-lb nginx -t

# Restart specific service (Universal stack)
docker compose -f docker-compose.english-multilang.yml restart streaming-api
docker compose -f docker-compose.english-multilang.yml restart streaming-asr-english
docker compose -f docker-compose.english-multilang.yml restart streaming-asr-multilang

# Restart specific service (Universal-3.5 Pro stack)
docker compose -f docker-compose.universal-3-5-pro.yml restart streaming-asr-universal-3-5-pro
```

## Deploying on Modal (serverless GPU)

Each streaming stack runs on [Modal](https://modal.com) as a self-contained
Modal App: one `modal deploy` brings up every service and wires them together,
with no dependency on any other deployment.

| Stack | File | Servers |
|---|---|---|
| Universal-3.5 Pro | `modal_app_universal_3_5_pro.py` | `StreamingApi` (CPU), `Asr` (L40S), `LicenseProxy` (CPU) |
| English + Multilingual | `modal_app_english_multilang.py` | `StreamingApi` (CPU), `Lb` (CPU nginx), `AsrEnglish` (L40S), `AsrMultilang` (L40S), `LicenseProxy` (CPU) |

`StreamingApi` resolves its backend and proxy URLs from the same App at startup,
so there is no manual wiring or two-phase deploy. The Universal-3.5 Pro stack
serves one model and needs no router, so nginx is dropped. The
English + Multilingual stack serves two models, so it keeps an nginx `Lb` that
routes the `x-model-version` gRPC metadata (from the client's `speech_model`) to
the matching backend, exactly as `streaming-asr-lb` does in compose.

### Prerequisites and secrets

Identical to the [sync stack](../sync/README.md#deploying-on-modal-serverless-gpu):
create the `aai-ecr-credentials` and `aai-license` Modal secrets once; both
streaming stacks share them.

### Deploy

```bash
modal deploy modal_app_universal_3_5_pro.py     # or modal_app_english_multilang.py
```

Each GPU backend keeps one L40S warm (`min_containers=1`) and gates readiness on
`grpc_health_probe`, so the first deploy takes a few minutes to warm the model;
Modal then autoscales on concurrent sessions (`target_concurrency=32`, matching
`MAX_OPEN_STREAMS`). The endpoint URLs are printed, of the form
`https://<workspace>--<app>-streamingapi.<region>.modal.direct`.

### Verify

```bash
curl -fsS https://<workspace>--aai-streaming-u3pro-licenseproxy.<region>.modal.direct/v1/status
curl -fsS https://<workspace>--aai-streaming-u3pro-streamingapi.<region>.modal.direct/v3/ws/health

# Stream with the bundled example client (see "Running the streaming example"),
# swapping the ws://localhost:8080 endpoint for the wss:// URL:
python example_with_prerecorded_audio_file.py \
  --audio-file example_audio_file.wav \
  --endpoint wss://<workspace>--aai-streaming-u3pro-streamingapi.<region>.modal.direct \
  --speech-model universal-3-5-pro
```

For the English + Multilingual stack use `--speech-model universal-streaming-english`
or `universal-streaming-multilingual`; the API maps these to the `en-default` /
`ml-default` routing keys and the `Lb` sends each to its backend.

### Authentication and security

`StreamingApi` requires a Modal proxy-auth token by default
(`unauthenticated=False`); Modal enforces it on the WebSocket upgrade, so a
guessed URL alone gets `401`. Send the token as `Modal-Key` / `Modal-Secret`
headers, or deploy with `AAI_REQUIRE_MODAL_AUTH=0` for a throwaway public test
endpoint (any non-empty `Authorization` then connects, as behind your own
gateway).

The internal hops (`StreamingApi` → `Asr`/`Lb`, and → `LicenseProxy`) cross
Modal's TLS edge, **not** a private bridge network as in compose: Modal has no
private inter-container network by default, so these `.modal.direct` endpoints
are public. The gRPC hop is encrypted — `h2_enabled` advertises ALPN h2 so the
API's default-TLS gRPC client connects with `AAI_USE_SECURE_CHANNEL_TO_ASR_SERVICE=True`
— but the backends and proxy are `unauthenticated=True`, because the API dials
them server-side and cannot attach Modal auth headers. Their URLs are
unguessable but reachable by anyone who learns them; a determined operator can
close that gap by co-locating the API and ASR in one container (localhost hop)
or by putting the backends on Modal's `i6pn` private network (an address
handshake via `modal.Dict`, same region). Treat the shipped topology as suitable
for evaluation, not untrusted public exposure of the backends.

### Cost and teardown

Each GPU backend holds an L40S while up (Modal bills it), scaling to at most
`max_containers` and down after `scaledown_window`. Tear a stack down when done:

```bash
modal app stop aai-streaming-u3pro                 # or aai-streaming-english-multilang
```

Audio is processed on Modal's multi-tenant cloud in the configured region
(default `us-east`); pin `routing_region`/`compute_region` near your callers,
and note the data-residency difference from a self-hosted deployment.

## Production deployment recommendations

See the [top-level README](../README.md#production-recommendations-license-and-usage-proxy)
for the license-and-usage-proxy. Streaming-specific services follow.

### streaming-api service

- **Deployment Strategy**: We recommend doing Blue/Green deployments to avoid disrupting ongoing sessions. Once you fully shift the traffic to the new color, wait at least 3 hours (the max session duration) before shutting down the old color to ensure no sessions get disrupted.
- **Resource Allocation**: We recommend allocating 1 CPU per container with at least 2GB of RAM for better hardware utilization. For example, it's better to have 4 containers with 1 CPU and 2GB RAM each rather than 1 container with 4 CPU and 8GB RAM.
- **Autoscaling**: We recommend setting up autoscaling based on the number of active sessions. A container with 1 CPU can generally handle around 32 concurrent sessions.
- **Monitoring**: Always monitor the logs during deployment to catch any potential issues early.
- **Dependencies**: For successful startup, the service depends on the license-and-usage-proxy service being up and running.
- **Configuration**: You can enable features like TLS encryption and structured logging via environment variables.
- **Health Checks**: Use the healthcheck command provided in the compose file to monitor container health.
- **Usage Reporting Behavior**: After each session completes, the streaming-api reports usage to the license-and-usage-proxy with automatic retries on failure. Monitor logs for any messages at a >= warning level.

### streaming-asr-english and streaming-asr-multilang services

- **Deployment Strategy**: Do gradual rollouts to ensure stability. Both Blue/Green and rolling deployments are good strategies, as the streaming-api can reconnect to a new streaming-asr container if a persistent connection gets disrupted with minimal state loss.
- **Hardware Requirements**: The services can run on NVIDIA T4 or newer GPUs. We recommend allocating at least 4 CPU and 16GB of RAM per container.
- **Autoscaling**: You can set up autoscaling based on the number of active sessions. A container with recommended hardware can generally handle up to 28 concurrent sessions.
- **Monitoring**: Always monitor logs during deployment to catch any potential issues early.
- **Health Checks**: Use the healthcheck command provided in the compose file to monitor container health.

### streaming-asr-universal-3-5-pro service
- **Deployment Strategy**: Do gradual rollouts to ensure stability. Both Blue/Green and rolling deployments are good strategies, as the streaming-api can reconnect to a new streaming-asr-universal-3-5-pro container if a persistent connection gets disrupted with minimal state loss.
- **Hardware Requirements**: NVIDIA L40S, RTX PRO 4500, or RTX PRO 6000. The model weights use ~11 GB of VRAM; the remaining VRAM becomes vLLM KV cache and sets max concurrency (more VRAM, higher concurrency). Allow ~30 GB of disk for the ~23 GB Docker image plus working space.
- **Autoscaling**: You can set up autoscaling based on the number of active sessions. A container using L40S GPU can generally handle up to 40 concurrent sessions.
- **Monitoring**: Always monitor logs during deployment to catch any potential issues early.
- **Health Checks**: Use the healthcheck command provided in the compose file to monitor container health.
