# Streaming Self-Hosted Services Docker Compose

This Docker Compose configuration runs the AssemblyAI streaming services as a standalone self-hosted stack.

## Services Included
- **streaming-api**: Gateway API service handling WebSocket connections.
- **streaming-asr-lb**: nginx load balancer for ASR services with header-based routing.
- **streaming-asr-english**: English ASR model service (requires GPU).
- **streaming-asr-multilang**: Multilingual ASR model service (requires GPU).
- **license-and-usage-proxy**: License validation service. Usage tracking will be added in upcoming releases.

## Connection Flow
```
Websocket client → streaming-api:8080 (WebSocket)
                          │
                          ├─ License validation ─→ license-and-usage-proxy:8080 (HTTP)
                          │
                          └─ ASR requests ───────→ streaming-asr-lb:80 → Header-based routing (X-Model-Version):
                                                                                ├── en-default → streaming-asr-english:50051 (gRPC)
                                                                                └── ml-default → streaming-asr-multilang:50051 (gRPC)
```

## Prerequisites
1. **AssemblyAI license**: Valid for the streaming self-hosted product.
2. **Docker & Docker Compose**: Ensure Docker and Docker Compose are installed.
3. **GPU Support**: NVIDIA Container Toolkit for GPU-enabled services.
4. **AWS Access**: Valid AWS credentials to pull images from ECR.

## Setup Instructions

### 1. Docker runtime with GPU support

**1.1** Verify NVIDIA drivers are installed:
```bash
nvidia-smi
```

**1.2** Install NVIDIA Container Toolkit:

Follow the [NVIDIA Container Toolkit installation guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) to set up GPU support for Docker.

**1.3** Verify the Docker runtime has GPU access:
```bash
docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi
```

### 2. AWS ECR Authentication

```bash
# Login to ECR to pull container images
aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin 344839248844.dkr.ecr.us-west-2.amazonaws.com
```

### 3. Configure Container Images

Use the reference `.env.example` file to create a `.env` file with container image references:

```bash
STREAMING_API_IMAGE=<CUSTOM_IMAGE>
STREAMING_ASR_ENGLISH_IMAGE=<CUSTOM_IMAGE>
STREAMING_ASR_MULTILANG_IMAGE=<CUSTOM_IMAGE>
LICENSE_AND_USAGE_PROXY_IMAGE=<CUSTOM_IMAGE>
```

### 4. Have the license file ready

Ensure you have your AssemblyAI license file in the current working directory as `license.jwt`, or modify the `LICENSE_FILE_PATH` environment variable in the `docker-compose.yml` to point to your license file location.

### 5. Start Services

```bash
# Start all services
docker compose up -d

# View logs
docker compose logs -f

# Check service status
docker compose ps
```

## Service Endpoints

- **WebSocket**: `ws://localhost:8080`

## Running the Streaming Example

A Python example script is provided to demonstrate how to stream audio to the self-hosted stack.

_Note_: You can initiate a session as soon as the `streaming-asr-english` and `streaming-asr-multilang` related containers are healthy, which happens after they output a "Ready to serve!" log line.

Change the current directory to the `streaming_example` directory:
``` bash
cd streaming_example
```

Create a fresh Python virtual environment and activate it:
```bash
python -m venv streaming_venv
source streaming_venv/bin/activate
```

Install the required packages to run the example script:
```bash
pip install -r requirements.txt
```

The example script (`example_with_prerecorded_audio_file.py`) accepts several CLI arguments:

**Basic usage:**
```bash
python example_with_prerecorded_audio_file.py --audio-file "example_audio_file.wav"
```

**Example using all available options:**
```bash
python example_with_prerecorded_audio_file.py \
  --audio-file "example_audio_file.wav" \
  --endpoint "ws://localhost:8080" \
  --language "multi"
```

**Command-line arguments:**

| Argument | Description                                            | Default                  |
|----------|--------------------------------------------------------|--------------------------|
| `--audio-file` | Path to the audio file to transcribe                   | `example_audio_file.wav` |
| `--endpoint` | WebSocket endpoint URL                                 | `ws://localhost:8080`     |
| `--language` | Language code for transcription (e.g., 'multi')        | ``                       |

**View help:**
```bash
python example_with_prerecorded_audio_file.py --help
```

## Configuration

### Nginx Configuration
**ASR Load Balancer** (`nginx_streaming_asr.conf`):
- gRPC proxying to ASR services.
- Routes to English or Multilang model based on the `X-Model-Version` header value.

## Monitoring & Debugging

### View Service Logs
```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f streaming-api
```

### Check Service Status
```bash
# Container status
docker compose ps

# Resource usage
docker stats
```

## Troubleshooting

### Debug Commands

```bash
# Check nginx configurations
docker compose exec streaming-asr-lb nginx -t

# Restart specific service
docker compose restart streaming-api
docker compose restart streaming-asr-english
docker compose restart streaming-asr-multilang
```

## Production Deployment Recommendations

### streaming-api service

- **Deployment Strategy**: We recommend doing Blue/Green deployments to avoid disrupting ongoing sessions. Once you fully shift the traffic to the new color, wait at least 3 hours (the max session duration) before shutting down the old color to ensure no sessions get disrupted.
- **Resource Allocation**: We recommend allocating 1 CPU per container with at least 2GB of RAM for better hardware utilization. For example, it's better to have 4 containers with 1 CPU and 2GB RAM each rather than 1 container with 4 CPU and 8GB RAM.
- **Autoscaling**: We recommend setting up autoscaling based on the number of active sessions. A container with 1 CPU can generally handle around 32 concurrent sessions.
- **Monitoring**: Always monitor the logs during deployment to catch any potential issues early.
- **Dependencies**: For successful startup, the service depends on the license-and-usage-proxy service being up and running.
- **Configuration**: You can enable features like TLS encryption and structured logging via environment variables.
- **Health Checks**: Use the healthcheck command provided in the docker-compose.yml to monitor container health.

### license-and-usage-proxy service

- **Deployment Strategy**: Do gradual rollouts to ensure stability.
- **Resource Allocation**: We recommend allocating 1 CPU per container with at least 2GB of RAM for better hardware utilization. For example, it's better to have 4 containers with 1 CPU and 2GB RAM each rather than 1 container with 4 CPU and 8GB RAM.
- **Monitoring**: Always monitor logs during deployment to catch any potential issues early. You can set up an alert based on the responses of the `/v1/status` endpoint to alert you on any license issues.
- **Dependencies**: For successful startup, the service depends on having a valid license being mounted on the container filesystem. To mount it, set the `LICENSE_FILE_PATH` environment variable to point to the license file path on the host machine.
- **Health Checks**: Use the healthcheck command provided in the docker-compose.yml to monitor container health.

#### License Status Endpoint

The `/v1/status` endpoint provides real-time information about the license validation state:

**Endpoint**: `GET /v1/status`

**Response Schema**:
```json
{
  "state": "Ready | Connected | TrustBased | Failed",
  "last_successful_checkin": "2025-01-01T12:00:00.000000Z",
  "trust_expiration": "2025-01-05T12:00:00.000000Z"
}
```

**State Descriptions**:
- `Ready`: Initial state when the service starts before any license validation has occurred.
- `Connected`: Last license validation check was successful.
- `TrustBased`: Last license validation check failed, but the request was within the trust window grace period, so services will remain operational.
- `Failed`: Last license validation check failed and the trust window has expired. streaming-api containers will shut down and stop serving requests.

**Fields**:
- `state`: Current license validation state.
- `last_successful_checkin`: ISO 8601 timestamp of the last successful license validation (null if never successful).
- `trust_expiration`: ISO 8601 timestamp when the trust window expires (null if no successful validation yet).

**Recommended Alerts**:
- Alert when `state` transitions to `TrustBased` (indicates license validation issues).
- Critical alert when `state` is `Failed` (services will shut down).

### streaming-asr-english and streaming-asr-multilang services

- **Deployment Strategy**: Do gradual rollouts to ensure stability. Both Blue/Green and rolling deployments are good strategies, as the streaming-api can reconnect to a new streaming-asr container if a persistent connection gets disrupted with minimal state loss.
- **Hardware Requirements**: The services can run on NVIDIA T4 or newer GPUs. We recommend allocating at least 4 CPU and 16GB of RAM per container.
- **Autoscaling**: You can set up autoscaling based on the number of active sessions. A container with recommended hardware can generally handle up to 28 concurrent sessions.
- **Monitoring**: Always monitor logs during deployment to catch any potential issues early.
- **Health Checks**: Use the healthcheck command provided in the docker-compose.yml to monitor container health.
