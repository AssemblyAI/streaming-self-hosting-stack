# Streaming Self-Hosted Services Docker Compose

This Docker Compose configuration runs the AssemblyAI streaming services as a standalone self-hosted stack.

## Services Included
- **streaming-api**: Gateway API service handling WebSocket connections
- **streaming-asr-lb**: nginx load balancer for ASR services with header-based routing
- **streaming-asr-english**: English ASR model service (requires GPU)
- **streaming-asr-multilang**: Multilingual ASR model service (requires GPU)

## Connection Flow

```
External Request → streaming-api:8080 (WebSocket) → streaming-asr-lb:80 → Header-based routing (X-Model-Version):
                                                                        ├── en-default → streaming-asr-english:50051 (gRPC)
                                                                        └── ml-default → streaming-asr-multilang:50051 (gRPC)
```

## Prerequisites

1. **Docker & Docker Compose**: Ensure Docker and Docker Compose are installed
2. **GPU Support**: NVIDIA Container Toolkit for GPU-enabled services
3. **AWS Access**: Valid AWS credentials to pull images from ECR

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
```

### 4. Start Services

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
- gRPC proxying to ASR services
- Routes to English or Multilang model based on the `X-Model-Version` header value

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
