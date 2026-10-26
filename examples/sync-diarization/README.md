# Example: sync + diarization in one stack

Sync transcribes a file, and diarization adds speaker labels to the result.
`docker-compose.sync-diarization.yml` runs both with one
license-and-usage-proxy and one `license.jwt`. Run all commands from this
`examples/sync-diarization/` directory.

> To run only diarization, use [`diarization/docker/`](../../diarization/docker/).
> Prerequisites (license, Docker, GPU runtime, ECR auth) are in the
> [top-level README](../../README.md).

The stack runs three containers: `sync-api` (GPU), `diarization-api` (GPU) and
`license-and-usage-proxy`. Sync word timestamps are on
(`SYNC_BFA_ALIGNMENT_ENABLED=true`), so the sync response posts to
`/v1/diarize` as is.

## Setup

```bash
cp .env.example .env
# Set SYNC_API_IMAGE, DIARIZATION_API_IMAGE + LICENSE_AND_USAGE_PROXY_IMAGE in .env
```

Place your `license.jwt` in this directory.

Both services use GPU 0. Sync needs the GPU listed in
[`sync/docker/`](../../sync/docker/). On a host with two GPUs, set
`DIARIZATION_GPU=1` in `.env`.

## Run

```bash
docker compose -f docker-compose.sync-diarization.yml up -d
docker compose -f docker-compose.sync-diarization.yml logs -f

# Stop
docker compose -f docker-compose.sync-diarization.yml down
```

The host ports are `8080` (sync), `8090` (diarization) and `8092` (proxy), the
same as the standalone stacks. Do not run this stack together with them.

```bash
curl -fsS http://localhost:8092/v1/status   # license proxy state
curl -fsS http://localhost:8080/readyz      # sync: 200 once the model is warm
curl -fsS http://localhost:8090/readyz      # diarization: 200 once the model is loaded
```

## Transcribe and diarize

```bash
cd ../../diarization/docker/example
pip install -r requirements.txt
python transcribe_and_diarize.py                    # uses the sync example audio file
python transcribe_and_diarize.py path/to/audio.wav  # or your own audio
```

## Limits

The sync limits use a `SYNC_` prefix in `.env` (for example
`SYNC_MAX_AUDIO_DURATION_MS`), because both services read the same variable
names. The diarization limits keep their names. All defaults are the same as in
the standalone stacks; `.env.example` lists them.
