# Diarization (speaker labels) self-hosted stack

Speaker diarization: POST audio and its transcript, get each word's speaker back
in one HTTP response. Run all commands from this
`diarization/docker/` directory.

> Prerequisites (license, Docker, GPU runtime, ECR auth) and the shared
> license-and-usage-proxy are documented in the [top-level README](../../README.md).

The stack (`docker-compose.diarization.yml`) runs two containers —
`diarization-api` (GPU) and `license-and-usage-proxy`. The service does not
validate credentials, but every request must carry a **non-empty `Authorization`
header** (any value works).

| File | API | Model | GPU requirement |
|------|-----|-------|-----------------|
| `docker-compose.diarization.yml` | Diarization (synchronous HTTP) | Speaker diarization | NVIDIA T4 or newer, 16 GB+ VRAM |

## Setup

Complete the [shared prerequisites](../../README.md#prerequisites-all-services)
first, then configure images:

```bash
cp .env.example .env
# Set DIARIZATION_API_IMAGE + LICENSE_AND_USAGE_PROXY_IMAGE in .env
```

Place your `license.jwt` in this directory.

## Run

```bash
docker compose -f docker-compose.diarization.yml up -d
docker compose -f docker-compose.diarization.yml logs -f

# Stop
docker compose -f docker-compose.diarization.yml down
```

The host ports are `8090` (diarization) and `8092` (proxy), so the stack can run
next to the sync stack on one host. The compose files set the project names
`diarization` and `sync`, so each stack keeps its own proxy.

## Verify

Use `self-hosted-streaming-license-and-usage-proxy:release-v1.3.0` or newer.
Older proxies reject diarization usage reports with a 422, so the proxy does
not record usage. The diarize requests continue to work.

```bash
curl -fsS http://localhost:8092/v1/status   # license proxy state
curl -fsS http://localhost:8090/readyz      # 200 once the model is loaded
```

## Diarize

`POST /v1/diarize` takes `multipart/form-data` with the `audio` file and a
`transcript` JSON part holding `words` with `text`, `start` and `end` (ms). The
[sync](../../sync/docker/) response with `"timestamps": true` works as is.

```bash
curl -F 'audio=@meeting.wav;type=audio/wav' \
  -F 'transcript=@transcript.json;type=application/json' \
  -H 'Authorization: any value works' \
  http://localhost:8090/v1/diarize
```

The response has `words` and `utterances` with `speaker` labels (`A`, `B`, …) and
`speaker_confidence`, and the `speakers` list. The model reports at most 8
speakers.

## Limits

Set in `.env` (defaults shown): `MAX_AUDIO_DURATION_MS=3600000`,
`MAX_REQUEST_BYTES=1073741824`, `INFERENCE_TIMEOUT_SECONDS=300`,
`MAX_CONCURRENT_REQUESTS=1`.

## Running the example

`example/transcribe_and_diarize.py` transcribes a file with the sync stack, then
diarizes it.

```bash
cd example
pip install -r requirements.txt
python transcribe_and_diarize.py                    # uses the sync example audio file
python transcribe_and_diarize.py path/to/audio.wav  # or your own audio
```
