#!/usr/bin/env python3
"""Stream a sample WAV to a deployed **streaming** stack and print turns live.

    pip install websockets
    # Universal-3.5 Pro stack:
    python sample_streaming.py \
        --endpoint wss://<workspace>--aai-streaming-u3pro-streamingapi.<region>.modal.direct \
        --audio ../streaming/example/example_audio_file.wav \
        --speech-model universal-3-5-pro

    # English + Multilingual stack (pick the model):
    python sample_streaming.py --endpoint wss://<workspace>--aai-streaming-english-multilang-streamingapi.<region>.modal.direct \
        --audio ../streaming/example/example_audio_file.wav --speech-model universal-streaming-english
    #   ... or --speech-model universal-streaming-multilingual

Audio is sent at real time by default so you watch partial turns update and
finalize, exactly as a live microphone would. Use --speed 2 to send twice as
fast, or --load N to open N sessions at once and print each one's summary.

If the API was deployed with Modal proxy auth (the shipped default), pass
--modal-key / --modal-secret (or set MODAL_KEY / MODAL_SECRET). For a test
endpoint deployed with AAI_REQUIRE_MODAL_AUTH=0 they are not needed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode


def load_pcm16_mono(path: str) -> tuple[bytes, int]:
    with wave.open(path, "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
            raise SystemExit(f"{path}: must be uncompressed 16-bit PCM WAV")
        if wav.getnchannels() != 1:
            raise SystemExit(f"{path}: must be mono (1 channel)")
        rate = wav.getframerate()
        return wav.readframes(wav.getnframes()), rate


def stream_once(args, pcm: bytes, rate: int, live: bool) -> dict:
    from websockets.sync.client import connect

    params = {"sample_rate": rate, "format_turns": "true"}
    if args.speech_model:
        params["speech_model"] = args.speech_model
    url = f"{args.endpoint.rstrip('/')}?{urlencode(params)}"

    headers = {"Authorization": "sample"}
    key = args.modal_key or os.environ.get("MODAL_KEY")
    secret = args.modal_secret or os.environ.get("MODAL_SECRET")
    if key and secret:
        headers["Modal-Key"], headers["Modal-Secret"] = key, secret

    frame = int(rate * 0.05) * 2  # 50 ms of 16-bit mono
    chunks = [pcm[i : i + frame] for i in range(0, len(pcm), frame)]

    start = time.perf_counter()
    first_turn: float | None = None
    finals: list[str] = []

    with connect(url, additional_headers=headers, open_timeout=args.open_timeout, max_size=None) as ws:
        def writer():
            for chunk in chunks:
                time.sleep(0.05 / args.speed)
                ws.send(chunk)
            ws.send('{"type": "Terminate"}')

        with ThreadPoolExecutor(max_workers=1) as pool:
            wf = pool.submit(writer)
            for message in ws:
                data = json.loads(message)
                if data.get("type") == "Turn":
                    words = data.get("words") or []
                    if not words:
                        continue
                    if first_turn is None:
                        first_turn = time.perf_counter() - start
                    text = " ".join(w["text"] for w in words)
                    if data.get("end_of_turn"):
                        finals.append(text)
                        if live:
                            print(f"\r  ✓ {text}", flush=True)
                    elif live:
                        # Update the current line in place while the turn forms.
                        print(f"\r  … {text[:110]}", end="", flush=True)
                elif data.get("type") == "Termination":
                    break
            wf.result()

    return {
        "elapsed_s": time.perf_counter() - start,
        "first_turn_s": first_turn,
        "turns": len(finals),
        "text": " ".join(finals),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--endpoint", required=True, help="wss://...streamingapi... URL")
    ap.add_argument("--audio", required=True, help="16-bit PCM mono WAV")
    ap.add_argument("--speech-model", help="universal-3-5-pro | universal-streaming-english | universal-streaming-multilingual")
    ap.add_argument("--speed", type=float, default=1.0, help="send rate vs realtime (2 = twice as fast)")
    ap.add_argument("--load", type=int, default=1, help="open N concurrent sessions")
    ap.add_argument("--open-timeout", type=float, default=300.0, help="WS handshake wait (cold starts are slow)")
    ap.add_argument("--modal-key")
    ap.add_argument("--modal-secret")
    args = ap.parse_args()

    pcm, rate = load_pcm16_mono(args.audio)
    dur = len(pcm) / 2 / rate
    print(f"{args.endpoint}\n  audio {dur:.1f}s @ {rate} Hz | model={args.speech_model or '(default)'} | "
          f"speed={args.speed}x | sessions={args.load}\n")

    if args.load == 1:
        r = stream_once(args, pcm, rate, live=True)
        print(f"\n{r['turns']} final turns | first turn {r['first_turn_s']:.2f}s | wall {r['elapsed_s']:.1f}s")
        return 0

    # Load mode: run N sessions at once, print a summary line per session.
    started = time.perf_counter()
    ok = 0
    with ThreadPoolExecutor(max_workers=args.load) as pool:
        futs = {pool.submit(stream_once, args, pcm, rate, False): i for i in range(args.load)}
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                r = fut.result()
                ok += 1
                print(f"  session {i:>2}: ok | {r['turns']} turns | first turn {r['first_turn_s']:.2f}s")
            except Exception as exc:  # noqa: BLE001
                print(f"  session {i:>2}: FAIL {exc}")
    print(f"\n{ok}/{args.load} sessions ok | wall {time.perf_counter() - started:.1f}s")
    return 0 if ok == args.load else 1


if __name__ == "__main__":
    sys.exit(main())
