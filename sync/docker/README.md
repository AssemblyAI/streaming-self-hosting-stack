# Sync (synchronous HTTP) self-hosted stack

Synchronous full-file transcription on Universal-3.6 Pro: POST audio, get the entire
transcript back in one HTTP response. Run all commands from this `sync/docker/`
directory.

> Prerequisites (license, Docker, GPU runtime, ECR auth) and the shared
> license-and-usage-proxy (usage reporting, license status endpoint, proxy
> production recommendations) are documented in the [top-level README](../../README.md).

The stack (`docker-compose.universal-3-6-pro.yml`) runs two containers — `sync-api`
(GPU) and `license-and-usage-proxy` — with no nginx load balancer and no
separate ASR backend. Authentication and rate limiting are expected to be
handled at your own infrastructure layer (reverse proxy / API gateway); the
service does not validate credentials, but every request must still carry a
**non-empty `Authorization` header** (any value works). A missing or empty
header returns `401`, so make sure your proxy doesn't strip it.

| File | API | Models served | GPU requirement |
|------|-----|--------------|-----------------|
| `docker-compose.universal-3-6-pro.yml` | Sync (synchronous HTTP) | Universal-3.6 Pro | NVIDIA L40S, RTX PRO 4500, or RTX PRO 6000 (preferred) |

## Setup

Complete the [shared prerequisites](../../README.md#prerequisites-all-services)
(GPU runtime, ECR authentication, license file) first, then configure images:

```bash
cp .env.example .env
# Set SYNC_API_IMAGE + LICENSE_AND_USAGE_PROXY_IMAGE in .env
```

Place your `license.jwt` in this directory (or repoint `LICENSE_FILE_PATH` in the compose file).
If your license uses usage-based billing, also set `USAGE_TRACKING_API_KEY` in `.env` to any API key from your AssemblyAI dashboard, or the license-and-usage-proxy shuts down at startup (see [Usage reporting](../../README.md#usage-reporting)).

## Run

```bash
docker compose -f docker-compose.universal-3-6-pro.yml up -d
docker compose -f docker-compose.universal-3-6-pro.yml logs -f

# Stop
docker compose -f docker-compose.universal-3-6-pro.yml down
```

The `sync-api` container is ready once the model is warm. Every start takes about
7 to 8 minutes on an RTX PRO 6000, about 12 minutes on an RTX PRO 4500, and about
12 minutes on an L40S with 4 vCPU, while weights load and the engine compiles and
warms up. Give the container at least 8 CPUs. It exposes
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

`POST /v1/transcribe` takes `multipart/form-data` with a required `audio` part
and an optional `config` JSON part. The same routes are also served unprefixed
(`POST /transcribe`) for clients written against earlier releases; `/v1` is the
canonical path for new integrations.

Accepted audio formats: **16-bit PCM WAV** (`audio/wav`), **raw S16LE PCM**
(`audio/pcm`, with `sample_rate` and `channels` in the config part), and the
compressed formats **AAC, MP3, M4A, OGG, WebM, and FLAC**. Audio constraints:
80 ms – 1 h and ≤ 256 MB by default (both configurable — see
[Audio limits](#audio-limits)), 16-bit, mono or stereo, sample rate one of
`{8000, 16000, 22050, 24000, 32000, 44100, 48000}`.

```bash
curl -F 'audio=@example/example_audio_file.wav;type=audio/wav' \
  -F 'config={"language_code":"en"};type=application/json' \
  -H 'Authorization: any value works' \
  http://localhost:8080/v1/transcribe
```

The optional `config` part also accepts `language_code`, `prompt`,
`keyterms_prompt`, `conversation_context`, and `timestamps` (see
[Word timestamps](#word-timestamps)). `keyterms_prompt` is also accepted as
`keyterms` or `word_boost`; passing more than one of those names returns `400`.
Unknown fields are rejected with `400`, so a misspelled option fails loudly
rather than being ignored. For transcription options and further help, see the
[AssemblyAI documentation](https://www.assemblyai.com/docs) or reach out to your
AssemblyAI contact.

`GET /v1/warm` returns `{"warm": "toasty"}` once the model is loaded. Use it to
pre-warm a connection before a latency-sensitive request; use `/readyz` for
health and readiness probes.

## Word timestamps

Set `"timestamps": true` in the `config` part to get per-word `start`/`end`
values in milliseconds alongside each word's `text` and `confidence`.

From `release-v1.3.0`, no container setting is needed. Timestamps are off
unless the request sets `"timestamps": true`, and timing words adds a small
latency cost under load.

```bash
curl -F 'audio=@example/example_audio_file.wav;type=audio/wav' \
  -F 'config={"timestamps":true};type=application/json' \
  -H 'Authorization: any value works' \
  http://localhost:8080/v1/transcribe
```

## Audio limits

The accepted audio length and request size are controlled by environment
variables on the `sync-api` container. The compose file passes them through, so
set them in `.env` (defaults shown):

| Variable | Default | Meaning |
|----------|---------|---------|
| `MAX_AUDIO_DURATION_MS` | `3600000` | Longest accepted audio (1 h); longer requests return `413`. |
| `MIN_AUDIO_DURATION_MS` | `80` | Shortest accepted audio; shorter requests return `400`. |
| `MAX_REQUEST_BYTES` | `268435456` | Request-body cap (256 MB); larger requests return `413`. |
| `INFERENCE_TIMEOUT_SECONDS` | `300` | Per-request deadline, including the upload; requests that exceed it return `504`. |

When raising `MAX_AUDIO_DURATION_MS`, adjust the other limits to match:

- **`MAX_REQUEST_BYTES`** — the 256 MB default fits ~20 min of 48 kHz stereo WAV.
  Size the cap to your longest audio at your highest sample rate / channel
  count (`bytes ≈ seconds × sample_rate × channels × 2`, plus a small WAV
  header).
- **`INFERENCE_TIMEOUT_SECONDS`** — longer audio takes longer to transcribe,
  especially under concurrent load; raise the deadline to keep long requests
  from timing out.

Longer audio also holds one of the container's request slots for longer, which
lowers the throughput a container can sustain (see [Scaling](#sync-api-service)). Load-test at your chosen limit before relying on
it in production.

## Running the sync example

A Python example is provided in `example/`:

```bash
cd example
python3 -m venv sync_venv && source sync_venv/bin/activate
pip install -r requirements.txt
python transcribe_file.py                    # uses the bundled example_audio_file.wav
python transcribe_file.py path/to/audio.wav  # or your own 16-bit PCM WAV
```

## Deploying on Modal (serverless GPU)

This stack also runs on Modal's serverless GPUs as a self-contained,
single-`modal deploy` Modal App. It has not been tested yet with the v1.5.0
image. See [`../modal/`](../modal/).

## Production deployment recommendations

See the [top-level README](../../README.md#production-recommendations-license-and-usage-proxy)
for the license-and-usage-proxy.

### sync-api service

- **Hardware Requirements**: NVIDIA L40S, RTX PRO 4500, or RTX PRO 6000, and at least 8 CPUs. The container uses about 25 GB of GPU memory, but needs about 90% of the GPU free when it starts, so do not share the GPU with another service that starts first. Allow ~55 GB of disk for the ~41 GB Docker image (~16 GB compressed pull) plus working space. See [Performance by GPU](../../README.md#performance-by-gpu) for start time, latency and throughput on each GPU.
- **Deployment Strategy**: Sync requests are short-lived HTTP calls, so rolling deployments work well. Drain in-flight requests before stopping a container.
- **Scaling**: A container serves up to 16 requests at once. A request beyond that waits up to 10 s for a slot and then returns `503` with `capacity_exceeded`. More GPU memory does not raise this limit, so scale by adding containers, using concurrent in-flight `/transcribe` requests as the signal.
- **Authentication & rate limiting**: Handle these at your own reverse proxy / API gateway — the service does not validate credentials (though every request must carry a non-empty `Authorization` header).
- **Health Checks**: Use `GET /readyz` (200 once warm) as the target-group health check; `GET /healthz` is always 200.
- **Monitoring**: Monitor logs during deployment and watch for warning-level messages.

## Troubleshooting

- **`deep_gemm` `AssertionError` traceback during warmup**: harmless. The
  inference engine probes for an optional GEMM kernel at startup and falls
  back when the probe fails; the traceback is noisy but does not affect
  readiness or transcription quality. The container is healthy once
  `GET /readyz` returns `200`.
