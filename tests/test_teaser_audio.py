import shutil
import subprocess
import wave

import pytest

from notebooks.teaser_audio import add_audio, audio_duration, make_music, video_duration


def test_original_music_is_bounded_and_has_requested_duration(tmp_path):
    path = tmp_path / "music.wav"
    make_music(path, 2)
    assert audio_duration(path) == 2
    with wave.open(str(path), "rb") as source:
        assert source.getsampwidth() == 2 and source.getnchannels() == 1
        assert any(source.readframes(source.getnframes()))


def test_real_audio_mux_preserves_video_and_refuses_overwrite(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg not installed")
    video = tmp_path / "input.mp4"
    voice = tmp_path / "voice.wav"
    output = tmp_path / "output.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                    "color=c=blue:s=64x64:r=16:d=3", "-c:v", "libx264", str(video)],
                   check=True, capture_output=True)
    make_music(voice, 1)  # Test-only WAV fixture, not a production narration provider.
    result = add_audio(video, output, "Test-only narration fixture.", voice_wav=voice, ffmpeg=ffmpeg)
    assert result["voice_engine"] == "provided-wav"
    assert video_duration(ffmpeg, output) == 3
    streams = subprocess.run([ffmpeg, "-i", str(output)], capture_output=True, text=True)
    assert "Audio: aac" in streams.stderr

    def video_hash(path):
        return subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:v:0",
                               "-c", "copy", "-f", "hash", "-"],
                              check=True, capture_output=True).stdout

    assert video_hash(video) == video_hash(output)
    with pytest.raises(ValueError, match="overwrite"):
        add_audio(video, output, "Test-only narration fixture.", voice_wav=voice, ffmpeg=ffmpeg)
    make_music(voice, 6)
    with pytest.raises(ValueError, match="too long"):
        add_audio(video, tmp_path / "too-long.mp4", "Test-only narration fixture.",
                  voice_wav=voice, ffmpeg=ffmpeg)
