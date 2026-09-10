# Streaming (WebSocket) self-hosted stack

Real-time transcription over a WebSocket connection. Run all commands from this
`streaming/` directory.

> Prerequisites (license, Docker, GPU runtime, ECR auth) and the shared
> license-and-usage-proxy (usage reporting, license status endpoint, proxy
> production recommendations) are documented in the [top-level README](../../README.md).

## Choosing a stack

Three stacks are shipped. Pick the one that matches the model you want to serve —
they are mutually exclusive (run one at a time):

| File | Models served | GPU requirement | Recommended for |
|------|--------------|-----------------|-----------------|
| `docker-compose.english-multilang.yml` | Universal English + Multilingual | NVIDIA T4+ per ASR container | Multi-model deployments |
| `docker-compose.universal-3-5-pro.yml` | Universal-3.5 Pro | NVIDIA L40S, RTX PRO 4500, or RTX PRO 6000 | Multi-container networking, flexible load balancing |
| `docker-compose.standalone-universal-3-5-pro.yml` | Universal-3.5 Pro | NVIDIA L40S, RTX PRO 4500, or RTX PRO 6000 | Single-model deployments, simplified setup, environments without multi-container support (Kubernetes, Podman) |

To switch between stacks, run `docker compose -f <file> down` before starting the other.

## Services included

All stacks include:
- **license-and-usage-proxy**: License validation and usage reporting (see [top-level README](../../README.md#shared-component-license-and-usage-proxy)).

Multi-container stacks (`docker-compose.english-multilang.yml` and `docker-compose.universal-3-5-pro.yml`):
- **streaming-api**: Gateway API service handling WebSocket connections.
- **streaming-asr-lb**: nginx load balancer for ASR services with header-based routing.
- **streaming-asr-english** and **streaming-asr-multilang** (English/Multilingual stack) or **streaming-asr-universal-3-5-pro** (Universal-3.5 Pro stack): ASR backends.

Standalone stack (`docker-compose.standalone-universal-3-5-pro.yml`):
- **streaming-standalone**: Single container running both the streaming API gateway and the Universal-3.5 Pro ASR model (no nginx load balancer, no separate ASR container).

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

**Standalone stack** (`docker-compose.standalone-universal-3-5-pro.yml`):
```
Websocket client → streaming-standalone:8080 (WebSocket)
                          │
                          ├─ Usage reporting     ───────→ license-and-usage-proxy:8080 [if usage-based billing] ────→ https://usage-tracker.assemblyai.com
                          │                               │
                          ├─ License validation  ─────────┘
                          │
                          └─ ASR requests        ───────→ [in-container loopback gRPC]
                                                             Universal-3.5 Pro ASR model
```

Multi-container stacks share the same `nginx_streaming_asr.conf`, which routes by
`X-Model-Version` header. The standalone stack requires no load balancer—
websocket clients should use `speech_model=universal-3-5-pro` for the standalone stack.

## Setup

Complete the [shared prerequisites](../../README.md#prerequisites-all-services)
(GPU runtime, ECR authentication, license file) first.

Copy the env reference and set the image variables for the stack you plan to run:

```bash
cp .env.example .env
```

```bash
# Required for all stacks:
STREAMING_API_IMAGE=<CUSTOM_IMAGE>
LICENSE_AND_USAGE_PROXY_IMAGE=<CUSTOM_IMAGE>

# Required for the Universal stack (docker-compose.english-multilang.yml):
STREAMING_ASR_ENGLISH_IMAGE=<CUSTOM_IMAGE>
STREAMING_ASR_MULTILANG_IMAGE=<CUSTOM_IMAGE>

# Required for the multi-container Universal-3.5 Pro stack (docker-compose.universal-3-5-pro.yml):
STREAMING_ASR_UNIVERSAL_3_5_PRO_IMAGE=<CUSTOM_IMAGE>

# Required for the standalone single-container Universal-3.5 Pro stack (docker-compose.standalone-universal-3-5-pro.yml):
STREAMING_STANDALONE_UNIVERSAL_3_5_PRO_IMAGE=<CUSTOM_IMAGE>
```

Place your `license.jwt` in this directory (or repoint `LICENSE_FILE_PATH` in the compose file).

## Run

All stacks bind the same WebSocket port (8080), so they are mutually exclusive.
For all Universal-3.5 Pro stacks (multi-container and standalone), websocket clients
should set query parameter `speech_model` to `universal-3-5-pro`.

**Universal stack** (English + Multilingual):
```bash
docker compose -f docker-compose.english-multilang.yml up -d
docker compose -f docker-compose.english-multilang.yml logs -f

# Check service status
docker compose -f docker-compose.english-multilang.yml ps

# Stop services before switching stacks
docker compose -f docker-compose.english-multilang.yml down
```

**Multi-container Universal-3.5 Pro stack**:
```bash
docker compose -f docker-compose.universal-3-5-pro.yml up -d
docker compose -f docker-compose.universal-3-5-pro.yml logs -f

# Check service status
docker compose -f docker-compose.universal-3-5-pro.yml ps

# Stop services before switching stacks
docker compose -f docker-compose.universal-3-5-pro.yml down
```

**Standalone single-container Universal-3.5 Pro stack**:
```bash
docker compose -f docker-compose.standalone-universal-3-5-pro.yml up -d
docker compose -f docker-compose.standalone-universal-3-5-pro.yml logs -f

# Check service status
docker compose -f docker-compose.standalone-universal-3-5-pro.yml ps

# Stop services before switching stacks
docker compose -f docker-compose.standalone-universal-3-5-pro.yml down
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
[top-level README](../../README.md#usage-reporting).

## Standalone stack notes

The standalone single-container stack (`docker-compose.standalone-universal-3-5-pro.yml`)
runs both the streaming API gateway and the Universal-3.5 Pro ASR model in a single container
with no nginx load balancer. It is recommended for single-model deployments and environments
where multi-container compose networking is difficult.

### Model configuration

- **`AAI_SUPPORTED_ASR_DEPLOYMENTS=universal-3-5-pro`**: Tells the API that this container
  serves only the Universal-3.5 Pro model. Clients requesting a different `speech_model` are
  rejected at handshake with close code 3006 instead of being transcribed by the wrong model.

### Health and readiness

The container's readiness depends on both services (API and ASR) being healthy:

- **Healthcheck endpoint**: `GET http://localhost:8080/v3/ws/health` returns 200 only after
  the ASR model is fully loaded and warmed.
- **Warmup time**: Model loading takes ~5 min on an RTX PRO 6000 and longer on slower GPUs.
  The compose healthcheck has `start_period: 600s` (10 minutes) to account for this; adjust if
  needed for your hardware.
- **Readiness behavior**: The WebSocket port (8080) is not open for connections until the
  health endpoint returns 200. Once the ASR is warm, clients can start sessions.

### Shutdown

The container performs an ordered shutdown (API first, then ASR) and can take up to ~60 seconds:

- The compose file specifies `stop_grace_period: 90s` to allow graceful termination.
- If either internal process (API or ASR) exits unexpectedly, the container exits with a
  non-zero code, so use `restart: unless-stopped` to restart on failure.

### Supervisor tuning (optional)

The container runs both services under a supervisor that manages startup, shutdown, and health.
Optional environment variables allow tuning timeouts:

| Variable | Default (seconds) | Purpose |
|----------|-------------------|---------|
| `STANDALONE_ASR_READY_TIMEOUT_SEC` | 900 | ASR model warmup timeout; container exits with code 3 if exceeded |
| `STANDALONE_API_DRAIN_SEC` | 30 | API shutdown grace period (for in-flight requests) |
| `STANDALONE_ASR_DRAIN_SEC` | 30 | ASR shutdown grace period (for in-flight transcriptions) |

### ASR runtime configuration

The ASR runtime flags (including `VLLM_USE_FLASHINFER_SAMPLER=0` for compatibility with newer
GPUs) are set inside the image and require no operator configuration.

### When to use multi-container vs. standalone

- **Use standalone** (`docker-compose.standalone-universal-3-5-pro.yml`): Single Universal-3.5 Pro deployment,
  simplified ops, or environments without multi-container compose support (Kubernetes, Podman).
- **Use multi-container** (`docker-compose.universal-3-5-pro.yml` or `docker-compose.english-multilang.yml`):
  Multi-model deployments (English + Multilingual), load-balanced scaling, or independent container updates.

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

# Restart specific service (Standalone stack)
docker compose -f docker-compose.standalone-universal-3-5-pro.yml restart streaming-standalone
docker compose -f docker-compose.standalone-universal-3-5-pro.yml logs -f streaming-standalone
```

## Deploying on Modal (serverless GPU)

The two multi-container streaming stacks (English/Multilingual and Universal-3.5 Pro)
run on Modal's serverless GPUs as self-contained, single-`modal deploy` Modal Apps.
See [`../modal/`](../modal/). The standalone single-container stack is not yet packaged
for Modal.

## Production deployment recommendations

See the [top-level README](../../README.md#production-recommendations-license-and-usage-proxy)
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

### streaming-standalone service
- **Deployment Strategy**: Deploy new versions by starting a second container and switching traffic after its health endpoint (`GET /v3/ws/health`) returns 200. Since a container restart incurs the full model warmup (~5 min), Blue/Green deployments are preferred over rolling updates.
- **Hardware Requirements**: Size the host for both the API's per-session CPU work and the ASR's CPU-side preprocessing (in the multi-container stack, these run on separate hosts). NVIDIA L40S, RTX PRO 4500, or RTX PRO 6000 GPU. Allow ~30 GB of disk for the ~23 GB Docker image plus working space.
- **Resource Allocation**: One GPU per container; size CPU and RAM for both the API gateway (2 CPU, 2 GB RAM minimum) and ASR preprocessing overhead (~2–4 CPU, 4–8 GB RAM for concurrent session handling).
- **Monitoring**: Always monitor logs during deployment and the health endpoint after startup to verify model readiness.
- **Health Checks**: Use the healthcheck endpoint and the compose healthcheck; traffic should only be sent to containers where `GET /v3/ws/health` returns 200.
