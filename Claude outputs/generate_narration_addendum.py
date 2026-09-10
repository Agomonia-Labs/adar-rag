#!/usr/bin/env python3
"""
Generates ONE additional narration line for the combined highlight video
(the new "packet / PDF download" beat), using the same Google Cloud TTS
voice (en-US-Chirp3-HD-Fenrir) as before.

RUN THIS IN A NORMAL TERMINAL ON YOUR MAC (not through any sandbox).

Usage:
    python3 generate_narration_addendum.py

Output: 12b_pdf_packet.mp3 written to:
    ~/Documents/Wondershare DemoCreator 8/ExportFiles/_video_work/talent_mobility/combined_tts/
"""
import base64
import json
import os
import re
import sys
import urllib.request
import urllib.error

HOME = os.path.expanduser("~")
ENV_FILE = os.path.join(HOME, "project", "adar-core", ".env.geetabitan")
OUT_DIR = os.path.join(
    HOME, "Documents", "Wondershare DemoCreator 8", "ExportFiles",
    "_video_work", "talent_mobility", "combined_tts",
)

VOICE_NAME = "en-US-Chirp3-HD-Fenrir"
LANGUAGE_CODE = "en-US"

BEAT_ID = "12b_pdf_packet"
TEXT = "Every completed review produces a documented PDF packet, ready to download or share."


def get_api_key():
    key = os.environ.get("GEETABITAN_TTS_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key:
        return key
    if os.path.isfile(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                m = re.match(r"^\s*GEETABITAN_TTS_API_KEY\s*=\s*(.+?)\s*$", line)
                if m:
                    return m.group(1).strip().strip('"').strip("'")
    return None


def synthesize(text, api_key):
    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}"
    payload = {
        "input": {"text": text},
        "voice": {"languageCode": LANGUAGE_CODE, "name": VOICE_NAME},
        "audioConfig": {"audioEncoding": "MP3"},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    audio_b64 = body.get("audioContent")
    if not audio_b64:
        raise RuntimeError(f"No audioContent in response: {body}")
    return base64.b64decode(audio_b64)


def main():
    api_key = get_api_key()
    if not api_key:
        print("ERROR: could not find a TTS API key.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"{BEAT_ID}.mp3")
    print(f"Synthesizing: {TEXT}")
    try:
        audio = synthesize(TEXT, api_key)
    except urllib.error.HTTPError as e:
        print(f"FAILED ({e.code}): {e.read().decode('utf-8', 'replace')[:300]}", file=sys.stderr)
        sys.exit(1)
    with open(out_path, "wb") as f:
        f.write(audio)
    print(f"Done -> {out_path} ({len(audio)} bytes)")
    print("Let Claude know when this is finished.")


if __name__ == "__main__":
    main()
