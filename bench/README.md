# Load-test harness

Points at a running deployment — local compose or Modal — sends **real audio**,
verifies a transcript actually comes back, and reports latency and throughput.
`--ramp` sweeps concurrency to find the level a deployment sustains before it
degrades.

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## Correctness check

One request, verifying the transcript contains an expected word (so an empty
`200` is still counted as a failure):

```bash
# sync
python harness.py sync \
  --endpoint https://<workspace>--aai-sync-u3pro-sync-api.modal.run \
  --audio ../sync/example/example_audio_file.wav

# streaming
python harness.py streaming \
  --endpoint wss://<workspace>--aai-streaming-u3pro-streaming-api.modal.run \
  --audio ../streaming/example/example_audio_file.wav \
  --speech-model universal-3-5-pro
```

Against a local compose stack, use `--endpoint http://localhost:8080` and
`ws://localhost:8080`.

## Concurrency sweep

```bash
python harness.py sync --endpoint https://... --audio a.wav --ramp 1,2,4,8,16
python harness.py streaming --endpoint wss://... --audio a.wav \
  --ramp 1,4,16,32 --max-seconds 20
```

Each level fires N requests simultaneously and reports:

| column | meaning |
| --- | --- |
| `ok` / `fail` | requests that returned a valid transcript |
| `p50` / `p95` / `max` | per-request wall-clock latency |
| `req/s` | completed requests per second |
| `xRT` | audio seconds transcribed per wall-clock second |

Throughput plateauing while latency keeps climbing is the saturation point:
past it, requests queue on the GPU rather than being served in parallel.

## Options

| flag | purpose |
| --- | --- |
| `--concurrency N` | single level (default 1) |
| `--ramp a,b,c` | sweep several levels in order |
| `--max-seconds N` | truncate the audio; keeps sweeps short |
| `--expect WORD` | substring the transcript must contain; `''` disables |
| `--speech-model` | streaming only, e.g. `universal-3-5-pro` |
| `--speed N` | streaming send rate vs realtime; `2` sends twice as fast |
| `--open-timeout N` | WebSocket handshake wait, default 300s for cold starts |
| `--stop-on-failure` | end a ramp at the first level that fails |

Exit status is non-zero if any level had a failure, so it works as a CI gate.

## Interpreting Modal results

- **The first request after idle is a cold start** and is not a latency
  measurement. On sync that was ~168 s wall against ~1.8 s of server time. Warm
  the endpoint first, or discard the first level.
- **A burst shorter than a cold start does not autoscale.** Modal cannot bring
  up a second GPU container within a short ramp, so a sweep measures *one*
  container's capacity — which is what you want when sizing a replica.
- **Streaming sessions are realtime-paced by default**, so a level takes at
  least the audio's duration. Use `--max-seconds` to keep sweeps short.
