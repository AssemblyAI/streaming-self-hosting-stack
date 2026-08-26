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

`modal_app.py` runs this stack on [Modal](https://modal.com) instead of a GPU
box you manage. It is a self-contained Modal App: one `modal deploy` brings up
both services and wires them together, and nothing depends on another
deployment. Compose's two services become two Modal Servers:

| Compose service | Modal Server | Hardware |
|---|---|---|
| `sync-api` | `SyncApi` | L40S GPU |
| `license-and-usage-proxy` | `LicenseProxy` | CPU |

`SyncApi` resolves `LicenseProxy`'s URL from the same App at startup, so there
is no manual wiring or two-phase deploy.

### Prerequisites

```bash
pip install modal && modal setup   # authenticate the Modal CLI
```

### Store credentials as Modal secrets

Modal has no bind mounts, so the license travels as a secret and is written to
disk at container startup.

```bash
# ECR pull credentials, used only when Modal builds (pulls) the image.
# Prefer a dedicated pull-only IAM principal over long-lived root/admin keys
# (ecr:GetAuthorizationToken + ecr:BatchGetImage / GetDownloadUrlForLayer /
# BatchCheckLayerAvailability on the AssemblyAI repositories). If you use SSO or
# assume-role session credentials, include AWS_SESSION_TOKEN; note they expire,
# so an image *rebuild* after expiry needs fresh values (redeploys of an
# already-built image do not).
modal secret create aai-ecr-credentials \
  AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_SESSION_TOKEN=... \
  AWS_REGION=us-west-2

# The license itself. Usage-billed licenses: add USAGE_TRACKING_API_KEY here
# too; it reaches the proxy automatically, no code change.
modal secret create aai-license LICENSE_JWT="$(cat license.jwt)"
```

### Deploy

```bash
modal deploy modal_app.py
```

The first deploy pulls and converts the ~13.5 GB sync image (several minutes);
later deploys reuse the cached image and take seconds. Two endpoint URLs are
printed, of the form `https://<workspace>--aai-sync-u3pro-<server>.<region>.modal.direct`.

### Verify

```bash
curl -fsS https://<workspace>--aai-sync-u3pro-licenseproxy.<region>.modal.direct/v1/status
# {"state":"Connected", ...}

curl -sS -o /dev/null -w '%{http_code}\n' \
  https://<workspace>--aai-sync-u3pro-syncapi.<region>.modal.direct/readyz
# 503 while the model is cold, 200 once warm (Modal's edge may answer 303 first)

curl -F 'audio=@example/example_audio_file.wav;type=audio/wav' \
  -F 'config={"language_code":"en"};type=application/json' \
  -H 'Authorization: any-non-empty-value' \
  https://<workspace>--aai-sync-u3pro-syncapi.<region>.modal.direct/transcribe
```

### Authentication

`SyncApi` requires a Modal proxy-auth token by default (`unauthenticated=False`),
so a guessed URL alone cannot reach it. Mint a proxy-auth token in the Modal
dashboard and send it on every request as `Modal-Key` / `Modal-Secret` headers
(or `Authorization: Bearer <key>.<secret>`). For a throwaway public test
endpoint, deploy with `AAI_REQUIRE_MODAL_AUTH=0` — it then accepts any non-empty
`Authorization` header, exactly like the compose stack behind your own gateway.
`LicenseProxy` is always `unauthenticated=True` because `SyncApi` calls it
server-side and cannot attach Modal headers; its URL is unguessable but public,
so treat it as such.

### Configuration

The audio limits (`MAX_AUDIO_DURATION_MS`, `MIN_AUDIO_DURATION_MS`,
`MAX_REQUEST_BYTES`, `INFERENCE_TIMEOUT_SECONDS`) are read from the environment
with the compose defaults as fallback, so you can override them by adding the
variable to the `aai-license` secret (or any Server env) — your value wins.

### Cost and teardown

`SyncApi` keeps one L40S warm (`min_containers=1`) so requests do not eat a cold
start; it autoscales up to `max_containers` under load and back down after
`scaledown_window`. Modal bills the GPU while it is up, so tear the app down when
you are done:

```bash
modal app stop aai-sync-u3pro
```

Audio is processed on Modal's multi-tenant cloud in `routing_region`/`compute_region`
(default `us-east`); pin them near your callers, and note this is a different
data-residency posture than a stack you host yourself.

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
