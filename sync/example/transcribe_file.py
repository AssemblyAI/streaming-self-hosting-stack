"""Minimal client for the self-hosted sync transcription API.

POSTs a WAV file to the local sync-api container and prints the transcript.

Usage:
    python transcribe_file.py [path/to/audio.wav]

Defaults to the bundled example_audio_file.wav when no path is given.

The self-hosted service does not validate credentials, but every request
must carry a non-empty Authorization header — any value works (see
../API.md#authentication).
"""

import json
import pathlib
import sys

import requests

SYNC_ENDPOINT = "http://localhost:8080/transcribe"
DEFAULT_AUDIO = pathlib.Path(__file__).parent / "example_audio_file.wav"


def main() -> None:
    if len(sys.argv) > 2:
        print("usage: python transcribe_file.py [audio.wav]", file=sys.stderr)
        sys.exit(2)

    audio_path = sys.argv[1] if len(sys.argv) == 2 else str(DEFAULT_AUDIO)
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
