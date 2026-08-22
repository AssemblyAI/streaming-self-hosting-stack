#!/usr/bin/env python3
"""Load-test harness for the self-hosted stacks, local or on Modal.

Sends real audio at a chosen concurrency, verifies transcripts actually come
back, and reports latency and throughput. Use --ramp to sweep concurrency and
find the level a deployment sustains before it degrades.

  # correctness check, one request
  python harness.py sync --endpoint https://host --audio ../sync/example/example_audio_file.wav

  # concurrency sweep
  python harness.py sync --endpoint https://host --audio a.wav --ramp 1,2,4,8,16
  python harness.py streaming --endpoint wss://host --audio a.wav --ramp 1,4,16,32,48
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlencode

# Words expected in the bundled sample; used only as a sanity check that the
# transcript is real output rather than an empty 200.
DEFAULT_EXPECT = "assemblyai"


@dataclass
class Result:
    ok: bool
    seconds: float
    detail: str = ""
    text: str = ""
    extra: dict = field(default_factory=dict)


def load_pcm16(path: str, max_seconds: float | None) -> tuple[bytes, int, float]:
    """Read a 16-bit PCM WAV, optionally truncated, returning raw frames."""
    with wave.open(path, "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
            raise SystemExit(f"{path}: must be uncompressed 16-bit PCM WAV")
        rate = wav.getframerate()
        frames = wav.getnframes()
        if max_seconds:
            frames = min(frames, int(rate * max_seconds))
        return wav.readframes(frames), rate, frames / rate


def wav_bytes(pcm: bytes, rate: int, channels: int = 1) -> bytes:
    """Re-wrap raw PCM as a WAV container for the sync API's multipart upload."""
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buf.getvalue()


# --------------------------------------------------------------------------
# sync: one HTTP POST per request
# --------------------------------------------------------------------------
def sync_once(endpoint: str, audio: bytes, expect: str) -> Result:
    import requests

    start = time.perf_counter()
    try:
        resp = requests.post(
            f"{endpoint.rstrip('/')}/transcribe",
            files={"audio": ("audio.wav", audio, "audio/wav")},
            data={"config": json.dumps({"language_code": "en"})},
            headers={"Authorization": "harness"},
            timeout=300,
        )
    except Exception as exc:  # network/timeout
        return Result(False, time.perf_counter() - start, detail=repr(exc))
    elapsed = time.perf_counter() - start

    if resp.status_code != 200:
        return Result(
            False, elapsed, detail=f"HTTP {resp.status_code}: {resp.text[:120]}"
        )
    body = resp.json()
    text = body.get("text", "")
    if expect and expect.lower() not in text.lower():
        return Result(
            False, elapsed, detail=f"transcript missing {expect!r}", text=text
        )
    return Result(
        True,
        elapsed,
        text=text,
        extra={
            "server_ms": body.get("request_time_ms"),
            "audio_ms": body.get("audio_duration_ms"),
            "words": len(body.get("words", [])),
        },
    )


# --------------------------------------------------------------------------
# streaming: one WebSocket session per request
# --------------------------------------------------------------------------
def streaming_once(
    endpoint: str,
    pcm: bytes,
    rate: int,
    expect: str,
    speech_model: str | None,
    speed: float,
    open_timeout: float,
) -> Result:
    from websockets.sync.client import connect

    params = {"sample_rate": rate, "format_turns": "true"}
    if speech_model:
        params["speech_model"] = speech_model
    url = f"{endpoint.rstrip('/')}?{urlencode(params)}"

    # 50 ms of audio per frame, the granularity the example client uses.
    frame = int(rate * 0.05) * 2
    chunks = [pcm[i : i + frame] for i in range(0, len(pcm), frame)]

    start = time.perf_counter()
    first_turn: float | None = None
    turns = 0
    final_text: list[str] = []

    try:
        # Generous open timeout: a cold Modal container can take minutes to
        # accept the upgrade, and the default 10s reads as a spurious failure.
        with connect(
            url,
            additional_headers={"Authorization": "harness"},
            open_timeout=open_timeout,
            max_size=None,
        ) as ws:

            def writer():
                for chunk in chunks:
                    time.sleep(0.05 / speed)
                    ws.send(chunk)
                ws.send('{"type": "Terminate"}')

            with ThreadPoolExecutor(max_workers=1) as pool:
                write_future = pool.submit(writer)
                for message in ws:
                    data = json.loads(message)
                    kind = data.get("type")
                    if kind == "Turn":
                        nonlocal_words = data.get("words") or []
                        if nonlocal_words:
                            if first_turn is None:
                                first_turn = time.perf_counter() - start
                            turns += 1
                            if data.get("end_of_turn"):
                                final_text.append(
                                    " ".join(w["text"] for w in nonlocal_words)
                                )
                    elif kind == "Termination":
                        break
                write_future.result()
    except Exception as exc:
        return Result(False, time.perf_counter() - start, detail=repr(exc))

    elapsed = time.perf_counter() - start
    text = " ".join(final_text)
    if expect and expect.lower() not in text.lower():
        return Result(
            False, elapsed, detail=f"transcript missing {expect!r}", text=text
        )
    return Result(
        True, elapsed, text=text, extra={"first_turn_s": first_turn, "turns": turns}
    )


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def run_level(work: Callable[[], Result], n: int) -> list[Result]:
    """Fire n copies of `work` at once and collect every outcome."""
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(work) for _ in range(n)]
        return [f.result() for f in futures]


def summarize(results: list[Result], wall: float, audio_seconds: float) -> dict:
    ok = [r for r in results if r.ok]
    lat = sorted(r.seconds for r in ok)

    def quantile(p: float) -> float:
        if not lat:
            return float("nan")
        return lat[min(int(len(lat) * p), len(lat) - 1)]

    return {
        "n": len(results),
        "ok": len(ok),
        "failed": len(results) - len(ok),
        "p50": quantile(0.50),
        "p95": quantile(0.95),
        "max": lat[-1] if lat else float("nan"),
        "wall": wall,
        "rps": len(ok) / wall if wall else 0.0,
        "audio_x_realtime": (len(ok) * audio_seconds / wall) if wall else 0.0,
    }


def print_table(rows: list[tuple[int, dict]]) -> None:
    print(
        f"\n{'conc':>5} {'ok':>5} {'fail':>5} {'p50 s':>8} {'p95 s':>8} "
        f"{'max s':>8} {'req/s':>7} {'xRT':>7}"
    )
    print("-" * 60)
    for conc, s in rows:
        print(
            f"{conc:>5} {s['ok']:>5} {s['failed']:>5} {s['p50']:>8.2f} "
            f"{s['p95']:>8.2f} {s['max']:>8.2f} {s['rps']:>7.2f} "
            f"{s['audio_x_realtime']:>7.1f}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("mode", choices=["sync", "streaming"])
    ap.add_argument(
        "--endpoint",
        required=True,
        help="https://... for sync, wss://... for streaming",
    )
    ap.add_argument("--audio", required=True, help="16-bit PCM WAV")
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--ramp", help="comma-separated concurrency levels, e.g. 1,4,8,16")
    ap.add_argument("--max-seconds", type=float, help="truncate audio to N seconds")
    ap.add_argument(
        "--expect",
        default=DEFAULT_EXPECT,
        help="substring the transcript must contain ('' to skip)",
    )
    ap.add_argument("--speech-model", help="streaming only, e.g. universal-3-5-pro")
    ap.add_argument(
        "--speed", type=float, default=1.0, help="streaming send rate vs realtime"
    )
    ap.add_argument(
        "--open-timeout",
        type=float,
        default=300.0,
        help="seconds to wait for the WebSocket handshake (cold starts are slow)",
    )
    ap.add_argument(
        "--stop-on-failure",
        action="store_true",
        help="end a ramp at the first level with failures",
    )
    args = ap.parse_args()

    pcm, rate, seconds = load_pcm16(args.audio, args.max_seconds)
    print(f"audio: {args.audio} | {seconds:.1f}s @ {rate} Hz | mode={args.mode}")
    print(f"endpoint: {args.endpoint}")

    if args.mode == "sync":
        payload = wav_bytes(pcm, rate)
        work = lambda: sync_once(args.endpoint, payload, args.expect)  # noqa: E731
    else:
        work = lambda: streaming_once(  # noqa: E731
            args.endpoint,
            pcm,
            rate,
            args.expect,
            args.speech_model,
            args.speed,
            args.open_timeout,
        )

    levels = [int(x) for x in args.ramp.split(",")] if args.ramp else [args.concurrency]

    rows = []
    sample_shown = False
    for conc in levels:
        started = time.perf_counter()
        results = run_level(work, conc)
        wall = time.perf_counter() - started
        stats = summarize(results, wall, seconds)
        rows.append((conc, stats))

        if not sample_shown:
            first_ok = next((r for r in results if r.ok), None)
            if first_ok:
                print(f"\ntranscript: {first_ok.text[:160]}...")
                if first_ok.extra:
                    print(f"detail: {first_ok.extra}")
                sample_shown = True

        print(
            f"[conc={conc}] ok={stats['ok']}/{stats['n']} "
            f"p50={stats['p50']:.2f}s p95={stats['p95']:.2f}s "
            f"{stats['audio_x_realtime']:.1f}x realtime"
        )
        for r in results:
            if not r.ok:
                print(f"  FAIL: {r.detail[:160]}")
        if stats["failed"] and args.stop_on_failure:
            print(f"\nstopping: first failures at concurrency {conc}")
            break

    if len(rows) > 1:
        print_table(rows)
        clean = [c for c, s in rows if s["failed"] == 0]
        if clean:
            print(f"\nhighest fully-successful concurrency tested: {max(clean)}")
        else:
            print("\nno concurrency level completed without failures")

    return 0 if all(s["failed"] == 0 for _, s in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
