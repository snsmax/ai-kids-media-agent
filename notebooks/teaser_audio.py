"""Free offline narration and original procedural music for private teaser review."""

import math
import re
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path


def audio_duration(path):
    with wave.open(str(path), "rb") as source:
        return source.getnframes() / source.getframerate()


def video_duration(ffmpeg, path):
    result = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)],
                            capture_output=True, text=True, timeout=30)
    match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", result.stderr)
    if not match:
        raise ValueError("Video duration unavailable")
    hour, minute, second = map(float, match.groups())
    return hour * 3600 + minute * 60 + second


def make_music(path, duration):
    """An original soft pentatonic pattern; no downloaded songs or recordings."""
    rate = 22050
    notes = (261.63, 329.63, 392.0, 329.63, 293.66, 392.0, 440.0, 392.0)
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        buffer = bytearray()
        for sample in range(round(duration * rate)):
            t = sample / rate
            note = notes[int(t / 0.75) % len(notes)]
            envelope = math.exp(-3.0 * (t % 0.75))
            fade = min(1.0, t / 0.6, max(0.0, (duration - t) / 1.2))
            value = (0.12 * math.sin(2 * math.pi * note * t) * envelope
                     + 0.025 * math.sin(2 * math.pi * 130.815 * t)) * fade
            buffer.extend(struct.pack("<h", round(value * 32767)))
            if len(buffer) >= 65536:
                output.writeframesraw(buffer)
                buffer.clear()
        output.writeframes(buffer)


def add_audio(video, target, narration, *, voice_wav=None, ffmpeg=None):
    """Copy video frames unchanged, mix narration and quiet music, return metadata.

    Linux notebooks use real eSpeak NG synthesis. Windows callers can supply a
    real SAPI-generated WAV. No fake voice or paid/network TTS fallback exists.
    """
    video, target = Path(video), Path(target)
    if video.resolve() == target.resolve() or target.exists():
        raise ValueError("Use a new output path; never overwrite reviewed media")
    if not isinstance(narration, str) or not 10 <= len(narration.strip()) <= 1000:
        raise ValueError("Provide a short opening narration, without revealing the ending")
    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required")
    duration = video_duration(ffmpeg, video)
    if not 1 <= duration <= 30:
        raise ValueError("Audio teasers must be 1–30 seconds")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as scratch:
        scratch = Path(scratch)
        if voice_wav:
            voice = Path(voice_wav)
            engine = "provided-wav"
        else:
            engine = "espeak-ng"
            binary = shutil.which(engine)
            if not binary:
                raise RuntimeError("Install espeak-ng for free offline narration")
            voice = scratch / "voice.wav"
            script = scratch / "narration.txt"
            script.write_text(narration, encoding="utf-8")
            subprocess.run([binary, "-v", "en-us", "-s", "145", "-w", str(voice),
                            "-f", str(script)], check=True, capture_output=True, timeout=60)
        speed = max(1.0, audio_duration(voice) / (duration - 0.6))
        if speed > 1.35:
            raise ValueError("Narration is too long; shorten it instead of cutting words")
        music = scratch / "music.wav"
        make_music(music, duration)
        mixed = scratch / "mixed.mp4"
        filters = (
            f"[1:a]atempo={speed:.6f},loudnorm=I=-18:TP=-2:LRA=7,"
            "adelay=300:all=1,apad[voice];"
            "[2:a]volume=0.35[music];"
            "[voice][music]amix=inputs=2:duration=longest:normalize=0,"
            "alimiter=limit=0.95:level=false[audio]"
        )
        subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-y", "-i", str(video),
                        "-i", str(voice), "-i", str(music), "-filter_complex", filters,
                        "-map", "0:v:0", "-map", "[audio]", "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
                        "-t", str(duration), "-movflags", "+faststart", str(mixed)],
                       check=True, capture_output=True, timeout=180)
        # Exclusive creation also protects against a concurrent output writer.
        with target.open("xb") as output, mixed.open("rb") as source:
            shutil.copyfileobj(source, output)
    return {"narration": narration, "voice_engine": engine,
            "music_source": "original-procedural-pentatonic"}
