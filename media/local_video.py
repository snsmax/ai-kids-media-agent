"""Local procedural 2D block-character animation without video API charges."""
import argparse
import hashlib
import os
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

from media.providers import ProviderFailure


class LocalVideoProvider:
    def __init__(self, output_dir, duration=30, public_domain=""):
        self.output_dir = Path(output_dir).resolve()
        self.duration = duration
        self.public_domain = public_domain

    def generate(self, prompt, request_id):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ProviderFailure("Install FFmpeg to render local character videos")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        seed = hashlib.sha256(("character-v3:" + prompt + request_id).encode()).hexdigest()
        target = self.output_dir / (seed[:32] + ".mp4")
        url = (f"https://{self.public_domain}/media/videos/{target.name}"
               if self.public_domain else target.as_uri())
        if target.is_file() and target.stat().st_size:
            return url
        # Opening excerpt only; keep the complete story for the paid product.
        excerpt = " ".join(prompt.split())[:90].rsplit(" ", 1)[0] + "..."
        caption = "\n".join(textwrap.wrap(excerpt, 30))
        palette = ["0xffb74d", "0x81c784", "0x64b5f6", "0xba68c8", "0xf06292"]
        filters = []
        for i in range(2):
            color = palette[int(seed[i], 16) % len(palette)]
            filters.append(
                f"[{i+1}:v]drawbox=x=12:y=10:w=76:h=74:color={color}:t=fill,"
                "drawbox=x=24:y=30:w=12:h=12:color=black:t=fill,"
                "drawbox=x=64:y=30:w=12:h=12:color=black:t=fill,"
                f"drawbox=x=24:y=30:w=12:h=12:color={color}:t=fill:enable='lt(mod(t,4),0.15)',"
                f"drawbox=x=64:y=30:w=12:h=12:color={color}:t=fill:enable='lt(mod(t,4),0.15)',"
                "drawbox=x=36:y=60:w=28:h=6:color=black:t=fill,"
                f"drawbox=x=22:y=88:w=56:h=60:color={color}:t=fill,"
                "drawbox=x=10:y=94:w=12:h=38:color=white:t=fill,"
                "drawbox=x=78:y=94:w=12:h=38:color=white:t=fill,"
                "drawbox=x=22:y=150:w=16:h=25:color=white:t=fill,"
                f"drawbox=x=62:y=150:w=16:h=25:color=white:t=fill[c{i}]"
            )
        filters.extend([
            "[0:v][c0]overlay=x='40+12*sin(t*2)':y='320+8*sin(t*3)'[a]",
            "[a][c1]overlay=x='220+12*sin(t*2+2)':y='320+8*sin(t*3+2)'[b]",
            "[b]drawtext=textfile=caption.txt:expansion=none:fontcolor=white:fontsize=18:"
            "line_spacing=5:x=(w-text_w)/2:y=45,"
            "drawtext=textfile=cta.txt:expansion=none:fontcolor=white:fontsize=18:"
            "x=(w-text_w)/2:y=555,scale=720:1280[v]",
        ])
        with tempfile.TemporaryDirectory(dir=self.output_dir) as scratch:
            scratch = Path(scratch)
            (scratch / "caption.txt").write_text(caption, encoding="utf-8")
            (scratch / "cta.txt").write_text("Parents: full story on Telegram", encoding="utf-8")
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
            for size in ["360x640", "100x180", "100x180"]:
                cmd += ["-f", "lavfi", "-i", f"color=c=0x263238:s={size}:r=24:d={self.duration}"]
            cmd += ["-filter_complex", ";".join(filters), "-map", "[v]", "-an",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(scratch / "render.mp4")]
            try:
                subprocess.run(cmd, cwd=scratch, check=True, capture_output=True,
                               timeout=min(240, max(120, self.duration * 4)))
                os.replace(scratch / "render.mp4", target)
            except (OSError, subprocess.SubprocessError):
                raise ProviderFailure("Local character rendering failed") from None
        return url


def main():
    parser = argparse.ArgumentParser(description="Render a local teaser without API keys")
    parser.add_argument("--story-file", type=Path, required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--duration", type=int, choices=range(10, 61), default=30)
    args = parser.parse_args()
    story = args.story_file.read_text(encoding="utf-8")
    print(LocalVideoProvider(args.output_dir, args.duration).generate(story, "local-preview-v1"))


if __name__ == "__main__":
    main()
