"""Zero-API-cost vertical video renderer using FFmpeg.

This creates simple animated story Shorts from text with local rendering only.
It intentionally does not call a paid video-generation service.
"""

import hashlib
import shutil
import subprocess
import textwrap
from pathlib import Path

from media.providers import ProviderFailure


class LocalVideoProvider:
    def __init__(self, output_dir: str, duration: int = 30):
        self.output_dir = Path(output_dir)
        self.duration = duration

    def generate(self, prompt: str, request_id: str) -> str:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ProviderFailure("FFmpeg is required for local video generation")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:24] + ".mp4"
        target = self.output_dir / name
        if target.exists() and target.stat().st_size > 0:
            return target.as_uri()

        # Keep rendered text compact and remove characters that complicate drawtext parsing.
        clean = " ".join(prompt.replace("'", "").replace(":", "-").split())[:700]
        lines = textwrap.wrap(clean, width=34)[:10]
        text = "\\n".join(lines).replace("%", "percent").replace(",", "\\,")
        draw = (
            "drawtext=fontcolor=white:fontsize=46:line_spacing=16:"
            "box=1:boxcolor=black@0.45:boxborderw=28:"
            "x=(w-text_w)/2:y=(h-text_h)/2:"
            f"text='{text}'"
        )
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=0x263238:s=1080x1920:r=30:d={self.duration}",
            "-vf", draw,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target),
        ]
        try:
            subprocess.run(cmd, check=True, timeout=max(120, self.duration * 6), capture_output=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            target.unlink(missing_ok=True)
            raise ProviderFailure("Local FFmpeg video generation failed") from None
        return target.as_uri()
