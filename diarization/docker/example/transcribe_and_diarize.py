"""Transcribe a file with the self-hosted sync stack, then diarize it.

POSTs the audio to the sync-api container with word timestamps on, POSTs the same
audio and the sync response to the diarization-api container, and prints one line
per utterance as "A: text".

Usage:
    python transcribe_and_diarize.py [path/to/audio.wav]

Defaults to the sync stack's example_audio_file.wav when no path is given.

Both stacks must be running: sync on port 8080 with SYNC_BFA_ALIGNMENT_ENABLED=true
(see ../../sync/docker/), diarization on port 8090. The self-hosted services do not
validate credentials, but every request must carry a non-empty Authorization header.
"""

import json
import mimetypes
import pathlib
import sys

import requests

SYNC_ENDPOINT = "http://localhost:8080/v1/transcribe"
DIARIZE_ENDPOINT = "http://localhost:8090/v1/diarize"
HEADERS = {"Authorization": "any value works"}
DEFAULT_AUDIO = (
    pathlib.Path(__file__).parents[3] / "sync/docker/example/example_audio_file.wav"
)


def main() -> None:
    if len(sys.argv) > 2:
        print("usage: python transcribe_and_diarize.py [audio.wav]", file=sys.stderr)
        sys.exit(2)

    audio_path = sys.argv[1] if len(sys.argv) == 2 else str(DEFAULT_AUDIO)
    content_type = mimetypes.guess_type(audio_path)[0] or "audio/wav"
    with open(audio_path, "rb") as f:
        audio = f.read()

    sync = requests.post(
        SYNC_ENDPOINT,
        files={
            "audio": (audio_path, audio, content_type),
            "config": (None, json.dumps({"timestamps": True}), "application/json"),
        },
        headers=HEADERS,
        timeout=300,
    )
    sync.raise_for_status()

    # The full sync response works as the transcript: diarization reads its words.
    diarized = requests.post(
        DIARIZE_ENDPOINT,
        files={
            "audio": (audio_path, audio, content_type),
            "transcript": (None, sync.content, "application/json"),
        },
        headers=HEADERS,
        timeout=600,
    )
    diarized.raise_for_status()
    result = diarized.json()

    print(f"speakers:        {', '.join(result['speakers'])}")
    print(f"request_time_ms: {result['request_time_ms']:.0f}")
    print()
    for utterance in result["utterances"]:
        print(f"{utterance['speaker']}: {utterance['text']}")


if __name__ == "__main__":
    main()
