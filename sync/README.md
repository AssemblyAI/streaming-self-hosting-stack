# Sync (synchronous HTTP) self-hosted stack

Synchronous full-file transcription on Universal-3.5 Pro: POST audio, get the entire
transcript back in one HTTP response. Run all commands from this `sync/`
directory.

> Prerequisites (license, Docker, GPU runtime, ECR auth) and the shared
> license-and-usage-proxy (usage reporting, license status endpoint, proxy
> production recommendations) are documented in the [top-level README](../README.md).

The stack (`docker-compose.universal-3-5-pro.yml`) runs two containers — `sync-api`
(GPU) and `license-and-usage-proxy` — with no nginx load balancer and no
separate ASR backend. Authentication and rate limiting are expected to be
handled at your own infrastructure layer (reverse proxy / API gateway); the
service accepts all requests.

| File | API | Models served | GPU requirement |
|------|-----|--------------|-----------------|
| `docker-compose.universal-3-5-pro.yml` | Sync (synchronous HTTP) | Universal-3.5 Pro | NVIDIA L40S, RTX PRO 4500, or RTX PRO 6000 (preferred) |

## Setup

Complete the [shared prerequisites](../README.md#prerequisites-all-services)
(GPU runtime, ECR authentication, license file) first, then configure images:

```bash
cp .env.example .env
# Set SYNC_API_IMAGE + LICENSE_AND_USAGE_PROXY_IMAGE in .env
```

Place your `license.jwt` in this directory (or repoint `LICENSE_FILE_PATH` in the compose file).

## Run

```bash
docker compose -f docker-compose.universal-3-5-pro.yml up -d
docker compose -f docker-compose.universal-3-5-pro.yml logs -f

# Stop
docker compose -f docker-compose.universal-3-5-pro.yml down
```

The `sync-api` container is ready once the model is warm (cold start can take a
few minutes while weights load and the engine warms up). It exposes
`GET /readyz` (returns `200` once warm, `503` during cold start), which the
container uses as its Docker healthcheck — point your load balancer's readiness
probe at it so requests are only routed once the model is warm.

## Verify

```bash
# License proxy state (Connected / Ready / TrustBased / Failed)
curl -fsS http://localhost:8082/v1/status

# Sync API readiness — 200 once the model is warm, 503 during cold start
curl -fsS http://localhost:8080/readyz
```

## Transcribe

`POST /transcribe` takes `multipart/form-data` with a required `audio` part and
an optional `config` JSON part. Audio constraints: 80 ms – 120 s, ≤ 40 MB,
16-bit, mono or stereo, sample rate one of
`{8000, 16000, 22050, 24000, 32000, 44100, 48000}`.

```bash
curl -F 'audio=@sample.wav;type=audio/wav' \
  -F 'config={"language_code":"en"};type=application/json' \
  -H 'Authorization: self-hosted' \
  http://localhost:8080/transcribe
```

The full request/response contract (config fields, error envelope, response
shape) is documented in the service's `API.md`.

## Running the sync example

A Python example is provided in `example/`:

```bash
cd example
python -m venv sync_venv && source sync_venv/bin/activate
pip install -r requirements.txt
python transcribe_file.py path/to/audio.wav
```

## Production deployment recommendations

See the [top-level README](../README.md#production-recommendations-license-and-usage-proxy)
for the license-and-usage-proxy.

### sync-api service

- **Hardware Requirements**: NVIDIA L40S, RTX PRO 4500, or RTX PRO 6000. The model weights use ~11 GB of VRAM; the remaining VRAM becomes vLLM KV cache and sets max concurrency (e.g. ~74 concurrent max-length requests on 96 GB — more VRAM, higher concurrency). Allow ~30 GB of disk for the ~23 GB Docker image plus working space.
- **Deployment Strategy**: Sync requests are short-lived HTTP calls, so rolling deployments work well. Drain in-flight requests before stopping a container.
- **Scaling**: The load signal that matters is concurrent in-flight `/transcribe` requests (equivalently, the total in-flight audio duration) — this is what fills the GPU KV cache. Scale out before the container saturates; once vLLM's queue backs up, latency climbs sharply. A container's capacity is bounded by KV-cache headroom (and thus GPU VRAM), so load-test your specific GPU to find the concurrency at which latency degrades, and set that as your scale-out threshold.
- **Authentication & rate limiting**: Handle these at your own reverse proxy / API gateway — the service accepts all requests.
- **Health Checks**: Use `GET /readyz` (200 once warm) as the target-group health check; `GET /healthz` is always 200.
- **Monitoring**: Monitor logs during deployment and watch for warning-level messages.
