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
service does not validate credentials, but every request must still carry a
**non-empty `Authorization` header** (any value works). A missing or empty
header returns `401`, so make sure your proxy doesn't strip it.

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
an optional `config` JSON part. Accepted audio formats: **16-bit PCM WAV**
(`audio/wav`) or **raw S16LE PCM** (`audio/pcm`, with `sample_rate` and
`channels` in the config part) — compressed formats like MP3 are rejected
with `415`. Audio constraints: 80 ms – 120 s and ≤ 40 MB by default (both
configurable — see [Audio limits](#audio-limits)), 16-bit, mono or stereo,
sample rate one of `{8000, 16000, 22050, 24000, 32000, 44100, 48000}`.

```bash
curl -F 'audio=@example/example_audio_file.wav;type=audio/wav' \
  -F 'config={"language_code":"en"};type=application/json' \
  -H 'Authorization: any value works' \
  http://localhost:8080/transcribe
```

The optional `config` part also accepts `language_code`, `prompt`,
`word_boost`, and `conversation_context`. Unknown fields are silently ignored,
so double-check spelling if an option seems to have no effect. For transcription
options and further help, see the [AssemblyAI documentation](https://www.assemblyai.com/docs)
or reach out to your AssemblyAI contact.

## Audio limits

The accepted audio length and request size are controlled by environment
variables on the `sync-api` container. The compose file passes them through, so
set them in `.env` (defaults shown):

| Variable | Default | Meaning |
|----------|---------|---------|
| `MAX_AUDIO_DURATION_MS` | `120000` | Longest accepted audio; longer requests return `413`. |
| `MIN_AUDIO_DURATION_MS` | `80` | Shortest accepted audio; shorter requests return `400`. |
| `MAX_REQUEST_BYTES` | `41943040` | Request-body cap; larger requests return `413`. |
| `INFERENCE_TIMEOUT_SECONDS` | `30` | Per-request inference deadline; requests that exceed it return `504`. |

When raising `MAX_AUDIO_DURATION_MS`, adjust the other limits to match:

- **`MAX_REQUEST_BYTES`** — the 40 MB default fits ~120 s of 48 kHz stereo WAV.
  Size the cap to your longest audio at your highest sample rate / channel
  count (`bytes ≈ seconds × sample_rate × channels × 2`, plus a small WAV
  header).
- **`INFERENCE_TIMEOUT_SECONDS`** — longer audio takes longer to transcribe,
  especially under concurrent load; raise the deadline to keep long requests
  from timing out.

Longer audio also consumes proportionally more GPU KV cache while in flight,
which lowers the request concurrency a container can sustain (see
[Scaling](#sync-api-service)). Load-test at your chosen limit before relying on
it in production.

## Running the sync example

A Python example is provided in `example/`:

```bash
cd example
python -m venv sync_venv && source sync_venv/bin/activate
pip install -r requirements.txt
python transcribe_file.py                    # uses the bundled example_audio_file.wav
python transcribe_file.py path/to/audio.wav  # or your own 16-bit PCM WAV
```

## Deploying on Modal (serverless GPU)

`modal_app.py` runs this same stack on [Modal](https://modal.com) instead of a
GPU box you manage. Compose's two services become two Modal functions, because
Modal runs one image per container and has no sidecars:

| Compose service | Modal function | Hardware |
|---|---|---|
| `sync-api` | `sync_api` | L40S GPU |
| `license-and-usage-proxy` | `license_proxy` | CPU |

`sync_api` resolves the proxy's Modal URL at startup and passes it as
`LICENSE_AND_USAGE_PROXY_ENDPOINT`, replacing the compose bridge network.

### Prerequisites

```bash
pip install modal && modal setup   # authenticate the Modal CLI
```

### Store credentials as Modal secrets

Modal has no bind mounts, so the license travels as a secret and is written to
disk at container startup. Both secrets are read at image-build and run time:

```bash
# ECR pull credentials. Modal re-mints the 12-hour registry token from these.
modal secret create aai-ecr \
  AWS_ACCESS_KEY_ID="$(aws configure get aws_access_key_id)" \
  AWS_SECRET_ACCESS_KEY="$(aws configure get aws_secret_access_key)" \
  AWS_REGION=us-west-2

# The license itself, kept out of any image layer.
modal secret create aai-license AAI_LICENSE_JWT="$(cat license.jwt)"
```

If you authenticate with `aws login` or SSO rather than static keys, export the
temporary session credentials instead — note they expire, so image *rebuilds*
need a refresh (deploys of an already-built image do not):

```bash
eval "$(aws configure export-credentials --format env)"
modal secret create aai-ecr \
  AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
  AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  AWS_SESSION_TOKEN="$AWS_SESSION_TOKEN" \
  AWS_REGION=us-west-2 --force
```

Usage-based licenses also need `USAGE_TRACKING_API_KEY` added to `aai-license`
and passed through in `license_proxy`. Flat-billed licenses need nothing extra.

### Deploy

```bash
modal deploy modal_app.py
```

The first deploy pulls and converts the ~13.5 GB sync image and takes several
minutes; later deploys reuse the cached image and take seconds. Two public URLs
are printed:

```
https://<workspace>--aai-sync-u3pro-sync-api.modal.run
https://<workspace>--aai-sync-u3pro-license-proxy.modal.run
```

### Verify

```bash
curl -fsS https://<workspace>--aai-sync-u3pro-license-proxy.modal.run/v1/status
# {"state":"Connected", ...}

curl -sS -o /dev/null -w '%{http_code}\n' \
  https://<workspace>--aai-sync-u3pro-sync-api.modal.run/readyz
# 303 while the container is cold, 200 once the model is warm
```

The [load-test harness](../bench/README.md) checks correctness and concurrency
in one step:

```bash
cd ../bench && pip install -r requirements.txt
python harness.py sync \
  --endpoint https://<workspace>--aai-sync-u3pro-sync-api.modal.run \
  --audio ../sync/example/example_audio_file.wav
```

Or transcribe exactly as documented in [Transcribe](#transcribe), swapping
`http://localhost:8080` for the `sync-api` URL:

```bash
curl -F 'audio=@example/example_audio_file.wav;type=audio/wav' \
  -F 'config={"language_code":"en"};type=application/json' \
  -H 'Authorization: any value works' \
  https://<workspace>--aai-sync-u3pro-sync-api.modal.run/transcribe
```

### Tear down

Both functions scale to zero on their own, so an idle deployment holds no GPU.
To remove it entirely:

```bash
modal app stop aai-sync-u3pro
```

### Measured behaviour (single L40S)

From `bench/harness.py` against the bundled 60 s sample, one warm container:

| concurrent requests | ok | p50 | p95 | audio x realtime |
|---:|---:|---:|---:|---:|
| 1 | 1/1 | 2.60 s | 2.60 s | 22.6 |
| 2 | 2/2 | 4.41 s | 4.41 s | 27.2 |
| 4 | 4/4 | 6.15 s | 7.88 s | 30.5 |
| 8 | 8/8 | 9.43 s | 14.57 s | 33.0 |
| 16 | 16/16 | 16.73 s | 28.85 s | 33.3 |

Server-side inference was ~1.8–2.1 s for 60 s of audio. Throughput plateaus
near **33x realtime at concurrency 8**; past that, latency grows roughly
linearly while throughput does not, which is the point where requests queue on
the GPU. Size a replica against that knee.

Note the first request after idle is a cold start, not a latency measurement —
one measured 168 s wall against 1.8 s of server time. A burst shorter than a
cold start also will not autoscale, so a sweep measures a single container.

### Modal-specific notes

- **Cold starts.** A cold `sync_api` container spends roughly 2–4 minutes
  pulling the image, loading weights, and capturing CUDA graphs; Modal returns
  `303` until the server binds. `scaledown_window=300` keeps a warm container
  for 5 minutes after the last request. For latency-sensitive traffic set
  `min_containers=1` on `sync_api` — that holds a GPU and bills accordingly.
- **`.entrypoint([])` is required.** Modal prepends an image's `ENTRYPOINT` to
  its own runtime command. Left in place, the vendor binary swallows Modal's
  arguments, starts with default environment (so `LICENSE_FILE_PATH` reverts to
  the compose path and the proxy dies with `License file not found`), and the
  Python in `modal_app.py` never executes.
- **Interpreter handling differs per image.** The proxy (Wolfi) already exposes
  `python3`, so it must *not* get `add_python`. The sync image's interpreter is
  hermetic inside Bazel runfiles and invisible to Modal, so it needs
  `add_python="3.12"`. Both images additionally pip-install the Modal client,
  because Modal's runtime-mounted client dependencies do not land on these
  interpreters' `sys.path` (symptom: `ModuleNotFoundError: grpclib`).
- **Authentication.** As on any other host, the service does not validate
  credentials but rejects an empty `Authorization` header with `401`. A
  `.modal.run` URL is public — put auth in front of it, or deploy the endpoint
  with Modal proxy auth, before exposing it beyond testing.

## Production deployment recommendations

See the [top-level README](../README.md#production-recommendations-license-and-usage-proxy)
for the license-and-usage-proxy.

### sync-api service

- **Hardware Requirements**: NVIDIA L40S, RTX PRO 4500, or RTX PRO 6000. The model weights use ~11 GB of VRAM; the remaining VRAM becomes vLLM KV cache and sets max concurrency (e.g. ~74 concurrent max-length requests on 96 GB — more VRAM, higher concurrency). Allow ~30 GB of disk for the ~23 GB Docker image plus working space.
- **Deployment Strategy**: Sync requests are short-lived HTTP calls, so rolling deployments work well. Drain in-flight requests before stopping a container.
- **Scaling**: The load signal that matters is concurrent in-flight `/transcribe` requests (equivalently, the total in-flight audio duration) — this is what fills the GPU KV cache. Scale out before the container saturates; once vLLM's queue backs up, latency climbs sharply. A container's capacity is bounded by KV-cache headroom (and thus GPU VRAM), so load-test your specific GPU to find the concurrency at which latency degrades, and set that as your scale-out threshold.
- **Authentication & rate limiting**: Handle these at your own reverse proxy / API gateway — the service does not validate credentials (though every request must carry a non-empty `Authorization` header).
- **Health Checks**: Use `GET /readyz` (200 once warm) as the target-group health check; `GET /healthz` is always 200.
- **Monitoring**: Monitor logs during deployment and watch for warning-level messages.

## Troubleshooting

- **`deep_gemm` `AssertionError` traceback during warmup**: harmless. The
  inference engine probes for an optional GEMM kernel at startup and falls
  back when the probe fails; the traceback is noisy but does not affect
  readiness or transcription quality. The container is healthy once
  `GET /readyz` returns `200`.
