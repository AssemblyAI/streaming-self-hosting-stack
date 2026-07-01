"""Minimal client for the self-hosted sync transcription API.

POSTs a WAV file to the local sync-api container and prints the transcript.

Usage:
    python transcribe_file.py path/to/audio.wav

The self-hosted stack uses the no-op SelfHostedAuthorizer, so any non-empty
Authorization value is accepted and there is no ALB, so no X-AAI-Model header
is required. Against the cloud API both would carry real values.
"""

import json
import sys

import requests

SYNC_ENDPOINT = "http://localhost:8080/transcribe"


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python transcribe_file.py <audio.wav>", file=sys.stderr)
        sys.exit(2)

    audio_path = sys.argv[1]
    with open(audio_path, "rb") as f:
        audio = f.read()

    response = requests.post(
        SYNC_ENDPOINT,
        files={
            "audio": (audio_path, audio, "audio/wav"),
            "config": (
                None,
                json.dumps({"language_code": "en"}),
                "application/json",
            ),
        },
        headers={"Authorization": "self-hosted"},
        timeout=120,
    )
    response.raise_for_status()
    result = response.json()

    print(f"session_id:        {result['session_id']}")
    print(f"audio_duration_ms: {result['audio_duration_ms']}")
    print(f"request_time_ms:   {result['request_time_ms']}")
    print(f"confidence:        {result['confidence']}")
    print()
    print(result["text"])


if __name__ == "__main__":
    main()
