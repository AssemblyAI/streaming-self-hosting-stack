# Sync API reference (self-hosted)

Synchronous full-file transcription: POST audio to the `sync-api` container,
get the entire transcript back in one HTTP response.

## Endpoint

`POST /transcribe` — `http://localhost:8080/transcribe` with the compose
stack's default port mapping.

## Authentication

The self-hosted service does **not** validate credentials — but it still
**requires a non-empty `Authorization` header** (or, for clients that can't
set request headers, a non-empty `token` query param). Any non-empty value is
accepted; a missing or empty value returns `401`. An optional `Bearer ` prefix
is stripped (`Authorization: Bearer ` with nothing after it counts as empty
and returns `401`).

```
Authorization: any-value
Authorization: Bearer any-value
?token=any-value
```

Real authentication, authorization, and rate limiting are yours to enforce at
your own infrastructure layer (reverse proxy / API gateway) in front of the
service. If your proxy strips or rewrites the `Authorization` header, make
sure a non-empty value still reaches the service, or pass `?token=`.

## Request body

The body is `multipart/form-data` with two parts:

| Part     | Required | Content-Type               | Notes |
|----------|----------|----------------------------|-------|
| `audio`  | yes      | `audio/wav` or `audio/pcm` | Raw audio bytes. The part's own Content-Type selects the decoder: `audio/wav` (parsed WAV; `audio/wave` and `audio/x-wav` are accepted aliases) or `audio/pcm` (raw signed 16-bit little-endian PCM). |
| `config` | no       | `application/json`         | JSON object carrying the parameters below. May be omitted entirely. |

These are the only accepted audio formats. Compressed formats (MP3, Opus,
FLAC, …) are rejected with `415` — transcode to 16-bit PCM WAV first.

### `config` fields

| Field         | Required        | Type     | Notes |
|---------------|-----------------|----------|-------|
| `sample_rate` | for `audio/pcm` | integer  | Source sample rate in Hz. One of: `8000`, `16000`, `22050`, `24000`, `32000`, `44100`, `48000`. WAV reads the rate from its header. |
| `channels`    | for `audio/pcm` | integer  | `1` (mono) or `2` (stereo). WAV reads the channel count from its header. |
| `prompt`      | no              | string   | Custom transcription instruction prepended to the model's system prompt. **Max 4096 chars.** When omitted, a default transcription prompt is applied. |
| `word_boost`  | no              | string[] | Keyterms biasing the decoder toward these tokens. Whitespace is stripped and empty terms dropped. **Max 2048 chars total.** Example: `["AssemblyAI", "Universal"]`. |
| `conversation_context` | no     | string or string[] | Prior turns from the same conversation that preceded this audio, giving the model the surrounding dialogue so it transcribes the current clip with better continuity and proper-noun consistency. **In chronological order: oldest first, most recent last.** Include turns from either side of the conversation (e.g. a voice agent's replies) as separate entries — entries carry no speaker labels. A single string is accepted and treated as one turn. Whitespace is stripped and empty entries dropped. Capped at **100 turns / 4096 chars total** — context over either cap is trimmed (oldest turns dropped first), not rejected. Example: `["I'd like to book a flight to Denver.", "Sure, what date?"]`. |
| `language_code` | no            | string or string[] | Language of the audio as an ISO 639-1 code, e.g. `"es"`. Pass a list (`["en", "es"]`) for multilingual audio. Steers the default transcription prompt toward the named language(s); **ignored when `prompt` is set**. Defaults to English. Supported: `en`, `es`, `de`, `fr`, `it`, `pt`, `tr`, `nl`, `sv`, `no`, `da`, `fi`, `hi`, `vi`, `ar`, `he`, `ja`, `ur`, `zh`. |

> **Unknown fields are silently ignored.** A misspelled field name (e.g.
> `wordboost`) does not error — it just has no effect. Double-check field
> names against this table if an option seems to be doing nothing.

## Audio constraints

| Constraint     | Value                                                      | Failure mode |
|----------------|------------------------------------------------------------|--------------|
| Min duration   | 80 ms                                                      | 400 `Audio Too Short` |
| Max duration   | 120 s                                                      | 413 `Audio Too Large` |
| Max audio size | 40 MB                                                      | 413 `Audio Too Large` (Content-Length pre-rejected when possible) |
| Sample width   | 16-bit only                                                | 415 `Unsupported Media Type` |
| Channels       | mono or stereo (stereo is down-mixed to mono internally)   | 415 `Unsupported Media Type` |
| Sample rate    | one of `{8000, 16000, 22050, 24000, 32000, 44100, 48000}`  | 415 `Unsupported Media Type` |

## Response 200

```json
{
  "text": "Hi, I'm calling about my order...",
  "words": [
    { "text": "Hi",   "start": 0,   "end": 200, "confidence": 0.91 },
    { "text": "I'm",  "start": 220, "end": 320, "confidence": 0.88 }
  ],
  "confidence": 0.87,
  "audio_duration_ms": 101567,
  "session_id": "eb92c4ff-4bbb-429f-9b99-7279d7fe738f",
  "request_time_ms": 243.7
}
```

- **`words[].start` / `words[].end`** — word timings in milliseconds.
- **`session_id`** — server-generated UUID, echoed in the container logs;
  record it to correlate a response with its log lines. Not configurable by
  the client.
- **`request_time_ms`** — end-to-end server-side request time in milliseconds:
  queue wait, auth, multipart parse, decode, inference, and serialization.

## Errors

All errors share one RFC 9457 problem-details envelope, served with
`Content-Type: application/problem+json`:

```json
{ "status": 413, "title": "Audio Too Large", "detail": "audio duration 130000 ms exceeds limit 120000 ms" }
```

- **`status`** — echo of the HTTP status code (the status line remains
  authoritative).
- **`title`** — stable identity of the error; safe to branch on. Does not
  change between occurrences of the same problem.
- **`detail`** — occurrence-specific human-readable explanation.

| HTTP | `title`                  | Cause |
|------|--------------------------|-------|
| 400  | `Bad Audio`              | Malformed WAV, misaligned PCM, missing `sample_rate` / `channels` in the config part for PCM. |
| 400  | `Audio Too Short`        | Below 80 ms. |
| 400  | `Bad Request`            | Missing `audio` part, invalid `config` JSON, `prompt` > 4096 chars, `word_boost` > 2048 chars, unsupported `language_code`, malformed multipart, or invalid `Content-Length`. |
| 401  | `Unauthorized`           | Missing / empty `Authorization` header (and no `token` query param). |
| 413  | `Audio Too Large`        | Duration > 120 s or audio part > 40 MB. |
| 415  | `Unsupported Media Type` | Request not `multipart/form-data`, unsupported `audio` part Content-Type (e.g. MP3), non-16-bit audio, or unsupported sample rate. |
| 503  | `Service Unavailable`    | Model still cold-starting (`/readyz` not yet 200). |
| 503  | `Capacity Exceeded`      | Server at concurrency cap. Retry after the `Retry-After` header (seconds). |
| 504  | `Inference Timeout`      | Request exceeded the 30 s deadline (covers auth + parse + decode + inference). |
| 500  | `Inference Error`        | Model failure. Response body is intentionally generic; full details are in container logs only. |

## Concurrency / capacity behavior

- Each container handles up to **16 concurrent requests** (configurable via
  the `MAX_CONCURRENT_REQUESTS` env var). The slot covers auth, multipart
  parse, decode, and inference. Beyond the cap, requests queue.
- A queued request waits up to **10 s** for an in-flight slot, then 503s with
  `Retry-After: 1`.
- Per-request deadline is **30 s**, spanning the full pipeline.
- There is **no built-in rate limiting** in the self-hosted stack — apply it
  at your gateway.

## Health checks

| Endpoint | Behavior |
|----------|----------|
| `GET /healthz` | Always `200 {"status": "ok"}` once the process is up. Liveness check. |
| `GET /readyz`  | `200 {"status": "ready"}` once the model is warm; `503 {"status": "not_ready"}` during cold start. Readiness / load-balancer health check. |

## Examples

### WAV — simplest case

The `;type=` on each `-F` sets the part's Content-Type; the `audio` part's
type selects the decoder.

```bash
curl -F 'audio=@example/example_audio_file.wav;type=audio/wav' \
  -H 'Authorization: any value works' \
  http://localhost:8080/transcribe
```

### WAV with prompt + word boost

```bash
curl -F 'audio=@example/example_audio_file.wav;type=audio/wav' \
  -F 'config={"prompt":"Transcribe verbatim. Preserve disfluencies.","word_boost":["AssemblyAI","Universal"]};type=application/json' \
  -H 'Authorization: any value works' \
  http://localhost:8080/transcribe
```

### WAV in a specific language

`language_code` steers the default prompt toward the named language. Pass a
single code, or a list for multilingual audio.

```bash
curl -F 'audio=@sample.wav;type=audio/wav' \
  -F 'config={"language_code":"es"};type=application/json' \
  -H 'Authorization: any value works' \
  http://localhost:8080/transcribe
```

### WAV with conversation context

Carry prior turns so the model keeps continuity and proper-noun spelling
across a multi-turn conversation. List the turns oldest-first; the most recent
turn goes last.

```bash
curl -F 'audio=@reply.wav;type=audio/wav' \
  -F 'config={"conversation_context":["I'\''d like to book a flight to Denver.","Sure, what date were you thinking?"]};type=application/json' \
  -H 'Authorization: any value works' \
  http://localhost:8080/transcribe
```

A single prior turn can be passed as a bare string instead of a list:
`"conversation_context":"Sure, what date were you thinking?"`.

### Raw PCM (16 kHz mono S16LE)

```bash
curl -F 'audio=@sample.pcm;type=audio/pcm' \
  -F 'config={"sample_rate":16000,"channels":1};type=application/json' \
  -H 'Authorization: any value works' \
  http://localhost:8080/transcribe
```

### Auth via query param

For clients that can't set the `Authorization` header (or sit behind a proxy
that strips it):

```bash
curl -F 'audio=@sample.pcm;type=audio/pcm' \
  -F 'config={"sample_rate":16000,"channels":1};type=application/json' \
  'http://localhost:8080/transcribe?token=example'
```

### Python

See [`example/transcribe_file.py`](example/transcribe_file.py) for a complete
runnable client.
