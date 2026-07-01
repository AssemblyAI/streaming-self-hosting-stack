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
| `docker-compose.universal-3-5-pro.yml` | Universal-3.5 Pro | 24 GB+ VRAM (e.g. L4, A10, A100); image bundles ~14 GB of weights |

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
serve!" when ready (typically ~2 min); `streaming-asr-universal-3-5-pro` logs its
ready message when warm (typically ~5 min).

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
- **Hardware Requirements**: NVIDIA L4 / A10 / A100 / L40S / H100 or equivalent with at least 24 GB VRAM. The container also needs ~14 GB of disk for the bundled model weights.
- **Autoscaling**: You can set up autoscaling based on the number of active sessions. A container using L40S GPU can generally handle up to 40 concurrent sessions.
- **Monitoring**: Always monitor logs during deployment to catch any potential issues early.
- **Health Checks**: Use the healthcheck command provided in the compose file to monitor container health.
