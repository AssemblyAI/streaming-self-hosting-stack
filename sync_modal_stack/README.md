# Sync stack on Modal (serverless GPU)

`modal_app.py` runs the self-hosted **sync** (full-file HTTP) stack on
[Modal](https://modal.com) instead of a GPU box you manage. It is a
self-contained Modal App: one `modal deploy` brings up both services and wires
them together, and nothing depends on another deployment. Compose's two services
(see [`../sync/`](../sync/)) become two Modal Servers:

| Compose service | Modal Server | Hardware |
|---|---|---|
| `sync-api` | `SyncApi` | L40S GPU |
| `license-and-usage-proxy` | `LicenseProxy` | CPU |

`SyncApi` resolves `LicenseProxy`'s URL from the same App at startup, so there
is no manual wiring or two-phase deploy.

## Prerequisites

```bash
pip install modal && modal setup   # authenticate the Modal CLI
```

## Store credentials as Modal secrets

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

Both streaming stacks share these same two secrets.

## Deploy

```bash
modal deploy modal_app.py
```

The first deploy pulls and converts the ~13.5 GB sync image (several minutes);
later deploys reuse the cached image and take seconds. Two endpoint URLs are
printed, of the form `https://<workspace>--aai-sync-u3pro-<server>.<region>.modal.direct`.

## Verify

`syncapi` is behind Modal proxy auth by default, so its probes need a
`Modal-Key` / `Modal-Secret` header pair (a proxy-auth token from the Modal
dashboard); `licenseproxy` `/v1/status` needs none. The `syncapi` examples below
show those headers — omit them only against an endpoint deployed with
`AAI_REQUIRE_MODAL_AUTH=0`, where any non-empty `Authorization` connects.

```bash
curl -fsS https://<workspace>--aai-sync-u3pro-licenseproxy.<region>.modal.direct/v1/status
# {"state":"Connected", ...}

curl -sS -o /dev/null -w '%{http_code}\n' \
  -H "Modal-Key: $MODAL_KEY" -H "Modal-Secret: $MODAL_SECRET" \
  https://<workspace>--aai-sync-u3pro-syncapi.<region>.modal.direct/readyz
# 503 while the model is cold, 200 once warm (Modal's edge may answer 303 first)

curl -F 'audio=@../sync/example/example_audio_file.wav;type=audio/wav' \
  -F 'config={"language_code":"en"};type=application/json' \
  -H "Modal-Key: $MODAL_KEY" -H "Modal-Secret: $MODAL_SECRET" \
  -H 'Authorization: any-non-empty-value' \
  https://<workspace>--aai-sync-u3pro-syncapi.<region>.modal.direct/transcribe
```

Or use the [sample script](#sample-requests) (pass `--modal-key` / `--modal-secret`).

## Authentication

`SyncApi` requires a Modal proxy-auth token by default (`unauthenticated=False`),
so a guessed URL alone cannot reach it. Mint a proxy-auth token in the Modal
dashboard and send it on every request as `Modal-Key` / `Modal-Secret` headers
(or `Authorization: Bearer <key>.<secret>`). For a throwaway public test
endpoint, deploy with `AAI_REQUIRE_MODAL_AUTH=0` — it then accepts any non-empty
`Authorization` header, exactly like the compose stack behind your own gateway.
`LicenseProxy` is always `unauthenticated=True` because `SyncApi` calls it
server-side and cannot attach Modal headers; its URL is unguessable but public,
so treat it as such.

## Configuration

The audio limits (`MAX_AUDIO_DURATION_MS`, `MIN_AUDIO_DURATION_MS`,
`MAX_REQUEST_BYTES`, `INFERENCE_TIMEOUT_SECONDS`) are read from the environment
with the compose defaults as fallback, so you can override them by adding the
variable to the `aai-license` secret (or any Server env) — your value wins.

## Sample requests

`sample_sync.py` sends a request and prints the transcript, server-side time,
and word count; `--concurrency N` fires N at once for a quick load check.

```bash
pip install requests
python sample_sync.py \
  --endpoint https://<workspace>--aai-sync-u3pro-syncapi.<region>.modal.direct \
  --audio ../sync/example/example_audio_file.wav
```

If the stack was deployed with the default proxy auth, pass `--modal-key` /
`--modal-secret` (or set `MODAL_KEY` / `MODAL_SECRET`).

## Cost and teardown

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
