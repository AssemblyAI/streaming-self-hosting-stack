# Sample requests

Small standalone scripts to send a sample input to a deployed stack and watch
the output in real time. One file per stack shape.

```bash
python -m venv venv && source venv/bin/activate
pip install requests websockets
```

Endpoints are the URLs `modal deploy` printed, of the form
`https://<workspace>--<app>-<server>.<region>.modal.direct` (`wss://` for streaming).

## Sync

```bash
python sample_sync.py \
  --endpoint https://<workspace>--aai-sync-u3pro-syncapi.<region>.modal.direct \
  --audio ../sync/example/example_audio_file.wav
```

Prints the transcript, server-side time, and word count. `--concurrency N` fires
N requests at once and reports aggregate throughput.

## Streaming (Universal-3.5 Pro)

```bash
python sample_streaming.py \
  --endpoint wss://<workspace>--aai-streaming-u3pro-streamingapi.<region>.modal.direct \
  --audio ../streaming/example/example_audio_file.wav \
  --speech-model universal-3-5-pro
```

Streams the audio at real time; partial turns update in place (`…`) and finalize
(`✓`) exactly as a live microphone would.

## Streaming (English + Multilingual)

Same script, pick the model — the API routes it to the matching backend:

```bash
python sample_streaming.py \
  --endpoint wss://<workspace>--aai-streaming-english-multilang-streamingapi.<region>.modal.direct \
  --audio ../streaming/example/example_audio_file.wav \
  --speech-model universal-streaming-english      # or universal-streaming-multilingual
```

## Load

- `sample_sync.py --concurrency N` — N simultaneous transcription requests.
- `sample_streaming.py --load N` — N simultaneous realtime sessions (each prints
  a summary line); `--speed 2` sends faster than real time to pack a sweep.

## Authentication

If a stack was deployed with the default Modal proxy auth
(`unauthenticated=False`), pass `--modal-key` / `--modal-secret` (or set
`MODAL_KEY` / `MODAL_SECRET`) — mint the token in the Modal dashboard. A test
endpoint deployed with `AAI_REQUIRE_MODAL_AUTH=0` needs no credentials.
