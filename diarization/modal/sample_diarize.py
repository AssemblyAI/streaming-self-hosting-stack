#!/usr/bin/env python3
"""Send a sample request to a deployed **diarization** stack and print the speakers.

Diarization needs the audio and its transcript with per-word start/end. Give the
transcript as a file, or let the script get it from a deployed sync stack
(started with SYNC_BFA_ALIGNMENT_ENABLED=true, see ../../sync/modal/):

    pip install requests
    python sample_diarize.py \
        --endpoint https://<workspace>--aai-diarization-diarizationapi.<region>.modal.direct \
        --audio meeting.wav --transcript transcript.json

    python sample_diarize.py \
        --endpoint https://<workspace>--aai-diarization-diarizationapi.<region>.modal.direct \
        --sync-endpoint https://<workspace>--aai-sync-u3pro-syncapi.<region>.modal.direct \
        --audio ../../sync/docker/example/example_audio_file.wav

If the APIs were deployed with Modal proxy auth (the shipped default), pass
--modal-key / --modal-secret (or set MODAL_KEY / MODAL_SECRET); for a test
endpoint deployed with AAI_REQUIRE_MODAL_AUTH=0 they are not needed.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time

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


def _transcribe(args: argparse.Namespace, audio: bytes, content_type: str, headers: dict[str, str]) -> bytes:
    resp = requests.post(
        f"{args.sync_endpoint.rstrip('/')}/v1/transcribe",
        files={
            "audio": ("audio", audio, content_type),
            "config": (None, json.dumps({"timestamps": True}), "application/json"),
        },
        headers=headers,
        timeout=args.timeout,
    )
    resp.raise_for_status()
    return resp.content


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--endpoint", required=True, help="https://...diarizationapi... URL")
    ap.add_argument("--audio", required=True, help="audio file")
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--transcript", help="JSON with `words` (text, start, end in ms)")
    source.add_argument("--sync-endpoint", help="https://...syncapi... URL to transcribe first")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--modal-key")
    ap.add_argument("--modal-secret")
    args = ap.parse_args()

    with open(args.audio, "rb") as fh:
        audio = fh.read()
    content_type = mimetypes.guess_type(args.audio)[0] or "audio/wav"
    headers = _auth_headers(args)

    if args.transcript:
        with open(args.transcript, "rb") as fh:
            transcript = fh.read()
    else:
        print(f"POST {args.sync_endpoint.rstrip('/')}/v1/transcribe")
        transcript = _transcribe(args, audio, content_type, headers)

    print(f"POST {args.endpoint.rstrip('/')}/v1/diarize\n")
    start = time.perf_counter()
    resp = requests.post(
        f"{args.endpoint.rstrip('/')}/v1/diarize",
        files={
            "audio": ("audio", audio, content_type),
            "transcript": (None, transcript, "application/json"),
        },
        headers=headers,
        timeout=args.timeout,
    )
    elapsed = time.perf_counter() - start
    resp.raise_for_status()
    result = resp.json()

    print(f"speakers:        {', '.join(result['speakers'])}")
    print(f"request_time_ms: {result['request_time_ms']:.0f}")
    print(f"wall:            {elapsed:.2f}s")
    print()
    for utterance in result["utterances"]:
        print(f"{utterance['speaker']}: {utterance['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
