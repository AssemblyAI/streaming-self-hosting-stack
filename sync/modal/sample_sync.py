#!/usr/bin/env python3
"""Send a sample request to a deployed **sync** stack and print the transcript.

    pip install requests
    python sample_sync.py --endpoint https://<workspace>--aai-sync-u3pro-syncapi.<region>.modal.direct \
        --audio ../docker/example/example_audio_file.wav

Load test (fire N in parallel, watch throughput):
    python sample_sync.py --endpoint https://... --audio a.wav --concurrency 8

If the API was deployed with Modal proxy auth (the shipped default), pass
--modal-key / --modal-secret (or set MODAL_KEY / MODAL_SECRET); for a test
endpoint deployed with AAI_REQUIRE_MODAL_AUTH=0 they are not needed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


def _auth_headers(args: argparse.Namespace) -> dict[str, str]:
    # Any non-empty Authorization satisfies the self-hosted API; Modal proxy
    # auth, if enabled, rides its own headers.
    headers = {"Authorization": "sample"}
    key = args.modal_key or os.environ.get("MODAL_KEY")
    secret = args.modal_secret or os.environ.get("MODAL_SECRET")
    if key and secret:
        headers["Modal-Key"] = key
        headers["Modal-Secret"] = secret
    return headers


def one_request(args: argparse.Namespace, audio: bytes, headers: dict[str, str]) -> dict:
    start = time.perf_counter()
    resp = requests.post(
        f"{args.endpoint.rstrip('/')}/transcribe",
        files={"audio": ("audio.wav", audio, "audio/wav")},
        data={"config": json.dumps({"language_code": args.language})},
        headers=headers,
        timeout=args.timeout,
    )
    elapsed = time.perf_counter() - start
    resp.raise_for_status()
    body = resp.json()
    return {
        "elapsed_s": elapsed,
        "server_ms": body.get("request_time_ms"),
        "audio_ms": body.get("audio_duration_ms"),
        "words": len(body.get("words", [])),
        "text": body.get("text", ""),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--endpoint", required=True, help="https://...syncapi... URL")
    ap.add_argument("--audio", required=True, help="16-bit PCM WAV file")
    ap.add_argument("--language", default="en")
    ap.add_argument("--concurrency", type=int, default=1, help="fire N requests at once")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--modal-key")
    ap.add_argument("--modal-secret")
    args = ap.parse_args()

    with open(args.audio, "rb") as fh:
        audio = fh.read()
    headers = _auth_headers(args)
    print(f"POST {args.endpoint.rstrip('/')}/transcribe  x{args.concurrency}\n")

    started = time.perf_counter()
    results, failures = [], 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futs = [pool.submit(one_request, args, audio, headers) for _ in range(args.concurrency)]
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                r = fut.result()
                results.append(r)
                print(f"[{i}/{args.concurrency}] ok  {r['elapsed_s']:.2f}s wall | "
                      f"server {r['server_ms']}ms | {r['words']} words")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"[{i}/{args.concurrency}] FAIL {exc}")
    wall = time.perf_counter() - started

    if results:
        print("\n--- sample transcript ---")
        print(results[0]["text"][:600])
        audio_s = (results[0]["audio_ms"] or 0) / 1000
        xrt = (len(results) * audio_s / wall) if wall else 0
        print(f"\n{len(results)} ok, {failures} failed | wall {wall:.2f}s | ~{xrt:.1f}x realtime aggregate")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
