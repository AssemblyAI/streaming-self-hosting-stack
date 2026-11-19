# Streaming (WebSocket) self-hosted stack

Real-time transcription over a WebSocket connection. Run all commands from this
`streaming/` directory.

> Prerequisites (license, Docker, GPU runtime, ECR auth) and the shared
> license-and-usage-proxy (usage reporting, license status endpoint, proxy
> production recommendations) are documented in the [top-level README](../../README.md).

## The stack

One stack is shipped. `docker-compose.stt-3-7-realtime-agent.yml` serves the
`assemblyai-stt-3-7-realtime-agent` model on one GPU. It has been tested on an
NVIDIA RTX PRO 6000; other GPUs, including the L40S, have not been measured yet.

## Services included

- **streaming-api**: Gateway API service handling WebSocket connections.
- **streaming-asr-lb**: nginx load balancer for ASR services with header-based routing.
- **streaming-asr-stt-3-7-agent**: the ASR backend for `assemblyai-stt-3-7-realtime-agent`.
- **license-and-usage-proxy**: License validation and usage reporting (see [top-level README](../../README.md#shared-component-license-and-usage-proxy)).

## Connection flow

```
Websocket client → streaming-api:8080 (WebSocket)
                          │
                          ├─ Usage reporting     ───────→ license-and-usage-proxy:8080 [if usage-based billing] ────→ https://usage-tracker.assemblyai.com
                          │                               │
                          ├─ License validation  ─────────┘
                          │
                          └─ ASR requests        ───────→ streaming-asr-lb:80 → Header-based routing (X-Model-Version):
                                                                                └── default → streaming-asr-stt-3-7-agent:50051 (gRPC)
```

## Setup

Complete the [shared prerequisites](../../README.md#prerequisites-all-services)
(GPU runtime, ECR authentication, license file) first.

Copy the env reference and set the image variables:

```bash
cp .env.example .env
```

```bash
STREAMING_API_IMAGE=<CUSTOM_IMAGE>
LICENSE_AND_USAGE_PROXY_IMAGE=<CUSTOM_IMAGE>
STREAMING_ASR_STT_3_7_AGENT_IMAGE=<CUSTOM_IMAGE>
```

Place your `license.jwt` in this directory (or repoint `LICENSE_FILE_PATH` in the compose file).
If your license uses usage-based billing, also set `USAGE_TRACKING_API_KEY` in `.env` to any API key from your AssemblyAI dashboard, or the license-and-usage-proxy shuts down at startup (see [Usage reporting](../../README.md#usage-reporting)).

## Run

Websocket clients select the model with the `speech_model` query parameter set
to `assemblyai-stt-3-7-realtime-agent`. It is also the default when the
parameter is omitted.

```bash
docker compose -f docker-compose.stt-3-7-realtime-agent.yml up -d
docker compose -f docker-compose.stt-3-7-realtime-agent.yml logs -f

# Check service status
docker compose -f docker-compose.stt-3-7-realtime-agent.yml ps

# Stop services
docker compose -f docker-compose.stt-3-7-realtime-agent.yml down
```

## Service endpoints

- **WebSocket**: `ws://localhost:8080`

## Running the streaming example

A Python example script is provided to demonstrate how to stream audio to the stack.

_Note_: You can initiate a session as soon as the `streaming-asr-stt-3-7-agent`
container is healthy. The model warms up on every start, which takes about 13 to
14 minutes on an RTX PRO 6000 and longer on slower GPUs.

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
```bash
python example_with_prerecorded_audio_file.py --audio-file "example_audio_file.wav" --endpoint "ws://localhost:8080" --speech-model "assemblyai-stt-3-7-realtime-agent"
```

**Command-line arguments:**

| Argument | Description                                            | Default                  |
|----------|--------------------------------------------------------|--------------------------|
| `--audio-file` | Path to the audio file to transcribe                   | `example_audio_file.wav` |
| `--endpoint` | WebSocket endpoint URL                                 | `ws://localhost:8080`     |
| `--speech-model` | Speech model to use (e.g., 'assemblyai-stt-3-7-realtime-agent') | ``               |

**View help:**
```bash
python example_with_prerecorded_audio_file.py --help
```

## Configuration

### Nginx configuration

**ASR Load Balancer** (`nginx_streaming_asr.conf`):
- gRPC proxying to ASR services.
- Routes every session to the `streaming-asr-stt-3-7-agent` backend.

### Usage reporting

The license-and-usage-proxy's billing modes and behavior are documented in the
[top-level README](../../README.md#usage-reporting).

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
docker compose -f docker-compose.stt-3-7-realtime-agent.yml exec streaming-asr-lb nginx -t

# Restart specific service
docker compose -f docker-compose.stt-3-7-realtime-agent.yml restart streaming-api
docker compose -f docker-compose.stt-3-7-realtime-agent.yml restart streaming-asr-stt-3-7-agent
```

## Deploying on Modal (serverless GPU)

The streaming stack also has a self-contained, single-`modal deploy` Modal App
for Modal's serverless GPUs. It has not been tested yet with the v1.5.0 images.
See [`../modal/`](../modal/).

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

### streaming-asr-stt-3-7-agent service

- **Deployment Strategy**: Do gradual rollouts to ensure stability. Both Blue/Green and rolling deployments are good strategies, as the streaming-api can reconnect to a new ASR container if a persistent connection gets disrupted with minimal state loss.
- **Hardware Requirements**: Tested on an NVIDIA RTX PRO 6000, where it used up to about 72 GB of GPU memory. The L40S and other GPUs have not been measured yet. The Docker image is about 40 GB, so allow enough disk for it plus working space.
- **Startup**: The model warms up on every start, which takes about 13 to 14 minutes on an RTX PRO 6000 and longer on slower GPUs. The health check allows up to an hour. Start new containers well before you need them, and do not scale this service to zero.
- **Autoscaling**: You can set up autoscaling based on the number of active sessions.
- **Monitoring**: Always monitor logs during deployment to catch any potential issues early.
- **Health Checks**: Use the healthcheck command provided in the compose file to monitor container health.
