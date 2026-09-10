#!/usr/bin/env python3
"""
Generates ALL 14 narration lines for the combined ADAR Talent Management /
Employee Growth & Mobility highlight video in ONE run — the original 13
beats PLUS the new "packet / PDF download" beat (12b_pdf_packet) — using
the same Google Cloud TTS voice your adar-core backend uses for its own
/api/tts endpoint (en-US-Chirp3-HD-Fenrir).

THIS REPLACES BOTH generate_narration.py AND generate_narration_addendum.py.
Please don't run those two anymore — just run this one script from now on
any time narration needs to be (re)generated. That removes the mix-up
between "the main script" and "the addendum script."

RUN THIS IN A NORMAL TERMINAL ON YOUR MAC (not through any sandbox) so it
can reach texttospeech.googleapis.com directly.

Usage:
    python3 generate_narration_all.py

It reads your existing TTS API key from (in order):
    1. $GEETABITAN_TTS_API_KEY or $GOOGLE_API_KEY environment variables
    2. ~/project/adar-core/.env.geetabitan  (GEETABITAN_TTS_API_KEY=... line)

Output: one .mp3 per beat (14 total), written to:
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

# id, voiceover line — ALL 14 beats, in final video order
SCRIPT_BEATS = [
    ("01_title", "Meet Adar Talent Management Readiness. Evidence backed hiring and growth decisions, powered by your own documents."),
    ("02_tm_upload", "Start with a candidate's resume and the role's job description. Adar ingests them in seconds."),
    ("03_tm_run", "One click builds a full readiness assessment against the role."),
    ("04_tm_profile", "Adar reads the resume into a structured talent profile: role history, skills, and highlights, organized automatically."),
    ("05_tm_skills", "Every skill is scored, with the evidence it came from cited right next to it."),
    ("06_tm_rolematch", "A documented match score shows exactly how the candidate stacks up against the role's requirements."),
    ("07_tm_interview", "For anything uncertain, Adar drafts interview questions to validate it with a real conversation."),
    ("08_tm_gap", "And any remaining gaps are laid out clearly, so nothing slips through before an offer goes out."),
    ("09_egm_switch", "The same engine also powers internal mobility. Just switch the workflow to Employee Growth and Mobility."),
    ("10_egm_validate", "Adar validates the employee's current skills against the target role, the same way it validates a new hire."),
    ("11_egm_devplan", "And builds a development plan: concrete actions, owners, and target dates, to close the gap."),
    ("12_egm_review", "A human always reviews and approves before anything moves forward."),
    ("12b_pdf_packet", "Every completed review produces a documented PDF packet, ready to download or share."),
    ("13_closing", "Adar Talent Management Readiness. Hiring and growth, evidence backed, every step of the way."),
]


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
        print("ERROR: could not find a TTS API key (checked env vars and "
              f"{ENV_FILE}). Set GEETABITAN_TTS_API_KEY and re-run.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Writing narration files to: {OUT_DIR}\n")

    manifest = []
    for beat_id, text in SCRIPT_BEATS:
        out_path = os.path.join(OUT_DIR, f"{beat_id}.mp3")
        print(f"  {beat_id}: {text[:60]}{'...' if len(text) > 60 else ''}")
        try:
            audio = synthesize(text, api_key)
        except urllib.error.HTTPError as e:
            print(f"    FAILED ({e.code}): {e.read().decode('utf-8', 'replace')[:300]}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"    FAILED: {e}", file=sys.stderr)
            sys.exit(1)
        with open(out_path, "wb") as f:
            f.write(audio)
        manifest.append(beat_id)
        print(f"    -> {out_path} ({len(audio)} bytes)")

    print(f"\nDone. {len(manifest)} narration clips written to:\n  {OUT_DIR}")
    print("\nLet Claude know when this is finished so it can pick up the files and finish the video.")


if __name__ == "__main__":
    main()
