# Streaming stacks on Modal (serverless GPU)

The streaming stack runs on [Modal](https://modal.com) as a self-contained
Modal App: one `modal deploy` brings up every service and wires them together,
with no dependency on any other deployment. Compose equivalents live in
[`../docker/`](../docker/).

| Stack | File | Servers |
|---|---|---|
| `assemblyai-stt-3-7-realtime-agent` | `modal_app_stt_3_7_realtime_agent.py` | `StreamingApi` (CPU), `Asr` (L40S), `LicenseProxy` (CPU) |

This Modal App has not been tested yet with the v1.5.0 images. The RTX PRO 4500
is not supported for streaming in this release.

`StreamingApi` resolves its backend and proxy URLs from the same App at startup,
so there is no manual wiring or two-phase deploy. The stack serves one model
and needs no router, so nginx is dropped.

## Prerequisites and secrets

Identical to the [sync stack](../../sync/modal/README.md#store-credentials-as-modal-secrets):
create the `aai-ecr-credentials` and `aai-license` Modal secrets once; every
stack shares them. If your license uses usage-based billing, include
`USAGE_TRACKING_API_KEY` (any API key from your AssemblyAI dashboard) in the
`aai-license` secret, or the license-and-usage-proxy shuts down at startup.

## Deploy

```bash
modal deploy modal_app_stt_3_7_realtime_agent.py
```

The GPU backend keeps one L40S warm (`min_containers=1`) and gates readiness on
`grpc_health_probe`. The model warms up on every start, which took about 12 to
14 minutes on an RTX PRO 6000 and about 22 minutes on an L40S with 4 vCPU in
Docker, so the startup timeout is 3600 s. Modal then autoscales on concurrent sessions
(`target_concurrency`). The endpoint URLs are printed, of the form
`https://<workspace>--<app>-streamingapi.<region>.modal.direct`.

## Verify

The `streamingapi` endpoint is behind Modal proxy auth by default, so send a
proxy-auth token (`--modal-key` / `--modal-secret`, or `MODAL_KEY` /
`MODAL_SECRET`); a `licenseproxy` `/v1/status` check needs none. To probe
without a token, deploy the endpoint with `AAI_REQUIRE_MODAL_AUTH=0`.

```bash
curl -fsS https://<workspace>--aai-streaming-stt-3-7-agent-licenseproxy.<region>.modal.direct/v1/status

# Stream with the bundled sample client (it forwards Modal proxy-auth headers;
# the repo's example_with_prerecorded_audio_file.py does not, so it only works
# against an AAI_REQUIRE_MODAL_AUTH=0 endpoint):
python sample_streaming.py \
  --endpoint wss://<workspace>--aai-streaming-stt-3-7-agent-streamingapi.<region>.modal.direct \
  --audio ../docker/example/example_audio_file.wav \
  --speech-model assemblyai-stt-3-7-realtime-agent \
  --modal-key "$MODAL_KEY" --modal-secret "$MODAL_SECRET"
```

Or use the [sample script](#sample-requests).

## Authentication and security

`StreamingApi` requires a Modal proxy-auth token by default
(`unauthenticated=False`); Modal enforces it on the WebSocket upgrade, so a
guessed URL alone gets `401`. Send the token as `Modal-Key` / `Modal-Secret`
headers, or deploy with `AAI_REQUIRE_MODAL_AUTH=0` for a throwaway public test
endpoint (any non-empty `Authorization` then connects, as behind your own
gateway).

This authenticated front door is the real access gate.

The internal hops (`StreamingApi` to `Asr`, and to `LicenseProxy`) cross
Modal's TLS edge, **not** a private bridge network as in compose, because Modal
has no private inter-container network by default, so these `.modal.direct`
endpoints are public. The gRPC hop is encrypted (`h2_enabled` advertises ALPN h2
so the API's default-TLS gRPC client connects with
`AAI_USE_SECURE_CHANNEL_TO_ASR_SERVICE=True`), but the backends and proxy run
`unauthenticated=True`, because the API dials them server-side and cannot attach
Modal auth headers. Their URLs are long and structured but public: obscurity, not
access control. The residual risk is bounded, though: forging a `POST /v1/usage`
inflates the self-hoster's own usage bill (it does not grant free service), and
reaching the ASR directly needs the URL and only buys time on the operator's own
GPU. No audio leaves the deployment and there is no cross-tenant surface.

Taking the backends fully private is a Modal limitation for this deployment
shape, not a config toggle. Both native routes fall down here: co-locating every
service in one container is not viable (the three vendor images are separate),
and Modal's `i6pn` private fabric only connects containers in the same underlying
datacenter, which the credit-card plan does not guarantee. A GPU backend (L40S)
is placed in a different datacenter than the CPU front-ends, and `i6pn` does not
bridge datacenters (forcing same-datacenter placement is gated behind Modal
sales). A full private `i6pn` build was implemented and tested to confirm this;
it connects only when the containers happen to co-locate. Treat the shipped
topology as suitable for evaluation behind the authenticated front door; a
stricter production posture (backends off the public internet) needs Modal to
enable same-datacenter placement.

## Sample requests

`sample_streaming.py` streams the audio at real time and prints turns live
(partial `…`, finalized `✓`); `--speech-model` picks the model and `--load N`
opens N concurrent sessions.

```bash
pip install websockets
python sample_streaming.py \
  --endpoint wss://<workspace>--aai-streaming-stt-3-7-agent-streamingapi.<region>.modal.direct \
  --audio ../docker/example/example_audio_file.wav \
  --speech-model assemblyai-stt-3-7-realtime-agent
```

If the stack was deployed with the default proxy auth, pass `--modal-key` /
`--modal-secret` (or set `MODAL_KEY` / `MODAL_SECRET`).

## Cost and teardown

The GPU backend holds an L40S while up (Modal bills it), scaling to at most
`max_containers` and down after `scaledown_window`. Tear the stack down when done:

```bash
modal app stop aai-streaming-stt-3-7-agent
```

Audio is processed on Modal's multi-tenant cloud in the configured region
(default `us-east`); pin `routing_region`/`compute_region` near your callers,
and note the data-residency difference from a self-hosted deployment.
