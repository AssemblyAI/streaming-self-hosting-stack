# AssemblyAI Self-Hosted Services

Docker Compose stacks for running AssemblyAI transcription services on your own
infrastructure. The stacks are organized by **service** (the API you talk to)
and, within each service, by **model**.

## Choosing a service and model

| Service | Directory | API | Models | GPU requirement |
|---------|-----------|-----|--------|-----------------|
| **Streaming** | [`streaming/`](streaming/) | WebSocket, real-time | Universal English + Multilingual | NVIDIA T4+ per ASR container |
| **Streaming** | [`streaming/`](streaming/) | WebSocket, real-time | U3 Pro | 24 GB+ VRAM (e.g. L4, A10, A100); image bundles ~14 GB of weights |
| **Sync** | [`sync/`](sync/) | Synchronous HTTP, full-file | U3 Pro | 24 GB+ VRAM (e.g. L4, A10, A100); image bundles ~14 GB of weights |

- **Streaming** transcribes a live audio stream over a WebSocket connection. One
  stack serves multiple models; the client selects the model per session. See
  [`streaming/README.md`](streaming/README.md).
- **Sync** transcribes a complete file in a single HTTP request/response (audio
  ≤ 120 s). It is self-contained — a single GPU container plus the
  license-and-usage-proxy, no load balancer. See [`sync/README.md`](sync/README.md).

Each service directory is self-contained: its compose file(s), `.env.example`,
example client, and `README.md` live together. Run commands from inside the
service directory.

## Repository layout

```
.
├── streaming/   # WebSocket streaming ASR (Universal English/Multilingual, U3 Pro)
└── sync/        # Synchronous full-file HTTP transcription (U3 Pro)
```

## Prerequisites (all services)

1. **AssemblyAI license** valid for the self-hosted product (the `license.jwt` file).
2. **Docker & Docker Compose**.
3. **GPU support**: NVIDIA Container Toolkit for GPU-enabled containers.
4. **AWS access**: credentials that can pull images from AssemblyAI's ECR.

### GPU runtime setup

Verify NVIDIA drivers:
```bash
nvidia-smi
```

Install the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html),
then verify the Docker runtime has GPU access:
```bash
docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi
```

### ECR authentication

```bash
aws ecr get-login-password --region us-west-2 \
  | docker login --username AWS --password-stdin 344839248844.dkr.ecr.us-west-2.amazonaws.com
```

### License file

Place your AssemblyAI `license.jwt` in the directory of the service you are
running (`streaming/` or `sync/`), or point the `LICENSE_FILE_PATH` environment
variable in that service's compose file at your license file's location.

## Shared component: license-and-usage-proxy

Every service runs the **license-and-usage-proxy** alongside it. It validates
your license and, for usage-based licenses, reports usage to AssemblyAI. Its
configuration and operational behavior are the same regardless of service.

### Usage reporting

The proxy supports two billing modes based on your license:

- **Flat billing** — usage tracking is disabled; no extra configuration needed.
- **Usage-based billing** — the proxy reports usage to AssemblyAI's usage
  tracker. Set `USAGE_TRACKING_API_KEY` (any key from the AssemblyAI dashboard)
  for the `license-and-usage-proxy` service.

**Behavior with usage-based billing:**
- At startup the proxy validates connectivity by registering with
  https://usage-tracker.assemblyai.com. If validation fails, the proxy shuts down.
- Usage is batched and reported every few seconds, with automatic retries.
- If https://usage-tracker.assemblyai.com stays unreachable and all retries fail
  (after 5–60 minutes), the proxy terminates itself as a fail-safe to protect
  usage-data integrity. Your orchestrator should replace the container.
- If the in-memory usage queue exceeds 1000 items, the proxy logs a warning
  suggesting you upscale.

### License status endpoint

`GET /v1/status` reports the live license-validation state:

```json
{
  "state": "Ready | Connected | TrustBased | Failed",
  "last_successful_checkin": "2025-01-01T12:00:00.000000Z",
  "trust_expiration": "2025-01-05T12:00:00.000000Z"
}
```

- `Ready` — initial state before any validation has occurred.
- `Connected` — last validation check succeeded.
- `TrustBased` — last validation failed but is within the trust-window grace
  period; services keep serving.
- `Failed` — last validation failed and the trust window expired; serving
  containers shut down.

`last_successful_checkin` and `trust_expiration` are ISO 8601 timestamps (null
until the first successful validation).

### Production recommendations (license-and-usage-proxy)

- **Deployment**: gradual rollouts; alert on service restarts.
- **Resources**: 1 CPU + ≥ 2 GB RAM per container; prefer more small containers
  over fewer large ones.
- **Monitoring**: alert on `/v1/status` transitions to `TrustBased` (warning)
  and `Failed` (critical). For usage-based billing, also monitor usage-reporting
  warnings and restarts.
- **Dependencies**: requires a valid license mounted on the container
  filesystem (set `LICENSE_FILE_PATH`). For usage-based billing, also requires
  connectivity to https://usage-tracker.assemblyai.com at startup.
- **Availability**: run a few containers behind a load balancer.

## Changelog

### v0.6.0

#### U3 Pro — New Self-Hosted Stack (NEW)

This release introduces the **U3 Pro self-hosted stack**
(`streaming/docker-compose.u3pro.yml`), which serves the U3 Pro async model. U3
Pro delivers significant improvements over the universal English model on
complex entities, short utterances, and end-of-turn (EOT) latency, and is
targeted at voice agent scenarios.

Hardware: NVIDIA L4 / A10 / A100 / L40S / H100 (24 GB+ VRAM).

Highlights of U3 Pro behavior delivered with this release:

- **New transcription prompt** ("Transcribe verbatim with standard punctuation. Include filler words and incomplete utterances.") — 22% reduction in voice-agent hallucinations, 10% WER and 29% short-utterance error-rate reduction on voice-agent traffic, 5% improvement on medical, and improved EP F1.
- **Continuous partials during long turns** — partials are emitted incrementally instead of being delayed; turns now stitch up to 60s instead of hard-cutting at 16s/32s.
- **Early partial at 750ms** of detected speech for faster UI feedback.

#### Streaming API — New Features

- **`continuous_partials` query parameter** — clients can opt into continuous partials during long turns.
- **Structured logging** — both the U3 Pro ASR server and the universal ASR server now honor `USE_STRUCTURED_LOGGING`, matching the streaming-api behavior.

#### Other Improvements

- Various logging and metrics improvements across the streaming-api and ASR services.
- Bug fixes and stability improvements.

### v0.5.0

#### English ASR Model

A new English model is released, which produces already-formatted outputs directly and delivers large quality gains on digits, telephony, medical, and CI segments:

- **34% improvement on digit sequence error rate (DSER)**
- **17% improvement on telephony WER**
- **12% average improvement on medical WER**
- **10% average improvement on CI segments WER**
- **~2.4% absolute F1 score improvement on keyterms prompting**
- **Significantly improved timestamp accuracy** — resolves overlapping and zero-duration word issues

#### Multilingual ASR Model

- **~70% absolute improvement in timestamp accuracy** — fixes overlapping words and zero-duration word bugs

#### Streaming API — New Features

- **Error and Warning WebSocket message types** — Dedicated message types that let clients distinguish actionable errors from non-fatal warnings without relying on close codes.
- **Configuration echoed in SessionBegins** — The `SessionBegins` message now includes the resolved session configuration so clients can verify applied settings.
- **Explicit speech-model selection** — Clients explicitly select the speech model at session start.

#### Streaming API — Fixes and Improvements

- **More specific WebSocket close codes** for session termination scenarios, making client-side error handling more precise.
- **Improved `word_finalized` events** — All word finalizations are emitted (not only the last word of a turn).

#### Other Improvements

- Various logging, metrics, and observability improvements across the streaming-api and ASR services.
- Bug fixes and stability improvements.

### v0.4.0

#### English ASR Model

Major improvements to short utterance handling and hallucination reduction:

- **100% reduction in hallucinations**
- **12.8% improvement on short utterances** - Better performance for voice agent use cases
- **7.39% improvement on digit sequence error rate**
- **1.75% improvement on proper nouns**
- **0.46% improvement on CI segments**
- **0.39% improvement on accented speech**

#### Multilingual ASR Model

- **Context biasing support** - Customers can now use context biasing (model-based biasing) with the multilingual model

#### Other Improvements

- Increased concurrent session handling per container, leading to reduced deployment costs
- Improved observability for the license-and-usage-proxy service
- Various bug fixes and stability improvements
