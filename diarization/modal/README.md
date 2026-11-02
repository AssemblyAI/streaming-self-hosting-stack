# Diarization stack on Modal (serverless GPU)

`modal_app.py` runs the self-hosted **diarization** stack on
[Modal](https://modal.com) instead of a GPU box you manage. It is a
self-contained Modal App: one `modal deploy` brings up both services and wires
them together. Compose's two services (see [`../docker/`](../docker/)) become two
Modal services:

| Compose service | Modal service | Kind | Hardware |
|---|---|---|---|
| `diarization-api` | `DiarizationApi` | Modal Server (`@app.server`) | L4 GPU |
| `license-and-usage-proxy` | `LicenseProxy` | web-server class (`@app.cls` + `@modal.web_server`) | CPU |

`DiarizationApi` resolves `LicenseProxy`'s URL from the same App at startup, so
there is no manual wiring or two-phase deploy.

## Prerequisites

```bash
pip install modal && modal setup   # authenticate the Modal CLI
```

## Store credentials as Modal secrets

The secrets are the same as for [`sync/modal/`](../../sync/modal/); if you
created them there, skip this step.

```bash
# ECR pull credentials, used only when Modal builds (pulls) the image.
modal secret create aai-ecr-credentials \
  AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_SESSION_TOKEN=... \
  AWS_REGION=us-west-2

# The license. Usage-based licenses also need USAGE_TRACKING_API_KEY, or the
# license-and-usage-proxy shuts down at startup. Flat licenses can leave it out.
modal secret create aai-license \
  LICENSE_JWT="$(cat license.jwt)" \
  USAGE_TRACKING_API_KEY=...
```

## Deploy

```bash
modal deploy modal_app.py
```

Two endpoint URLs are printed:
`https://<workspace>--aai-diarization-diarizationapi.<region>.modal.direct` for
the API and
`https://<workspace>--aai-diarization-licenseproxy-start-server.modal.run` for
the proxy.

## Verify

`diarizationapi` is behind Modal proxy auth by default, so its requests need a
`Modal-Key` / `Modal-Secret` header pair (a proxy-auth token from the Modal
dashboard). `licenseproxy` `/v1/status` needs none.

```bash
curl -fsS https://<workspace>--aai-diarization-licenseproxy-start-server.modal.run/v1/status

curl -sS -o /dev/null -w '%{http_code}\n' \
  -H "Modal-Key: $MODAL_KEY" -H "Modal-Secret: $MODAL_SECRET" \
  https://<workspace>--aai-diarization-diarizationapi.<region>.modal.direct/readyz
# 503 while the model loads, 200 once ready
```

## Sample requests

`sample_diarize.py` sends the audio and its transcript to `/v1/diarize` and
prints one line per utterance. Give the transcript as a JSON file, or pass the
URL of a [sync Modal stack](../../sync/modal/) deployed with
`SYNC_BFA_ALIGNMENT_ENABLED=true` and the script transcribes first.

```bash
pip install requests
python sample_diarize.py \
  --endpoint https://<workspace>--aai-diarization-diarizationapi.<region>.modal.direct \
  --sync-endpoint https://<workspace>--aai-sync-u3pro-syncapi.<region>.modal.direct \
  --audio ../../sync/docker/example/example_audio_file.wav
```

Pass `--modal-key` / `--modal-secret` (or set `MODAL_KEY` / `MODAL_SECRET`) for
endpoints with the default proxy auth.

## Authentication

`DiarizationApi` requires a Modal proxy-auth token by default. For a throwaway
public test endpoint, deploy with `AAI_REQUIRE_MODAL_AUTH=0`; it then accepts any
non-empty `Authorization` header. `LicenseProxy` is always public because
`DiarizationApi` calls it server-side; its URL is unguessable but public.

## Configuration

`MAX_AUDIO_DURATION_MS`, `MAX_REQUEST_BYTES` and `INFERENCE_TIMEOUT_SECONDS` use
the compose defaults (60 min / 1 GB / 300 s). To change them, add the variable to
the `aai-license` secret. Each container diarizes one request at a time and
Modal adds containers under load, up to `max_containers` (4).

`PROXY_MIN_CONTAINERS` (default `1`) sets how many `LicenseProxy` containers stay
warm. Set it in the shell that runs `modal deploy`.

## Cost and teardown

`DiarizationApi` keeps one L4 warm (`min_containers=1`). Modal bills the GPU
while it is up, so tear the app down when you are done.

`LicenseProxy` keeps one CPU container warm by default, because the proxy holds
queued usage in memory and a proxy that scales down loses billable usage.
Flat-billed licenses send no usage and can deploy with
`PROXY_MIN_CONTAINERS=0 modal deploy modal_app.py`.

```bash
modal app stop aai-diarization
```

Audio is processed on Modal's cloud in `routing_region` / `compute_region`
(default `us-east`), which is a different data-residency posture than a stack
you host yourself.
