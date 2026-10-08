"""Import a downloaded GPU teaser as a private draft; never grants safety approval."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from media.agents import QualityControlAgent
from media.config import settings
from media.store import connect, now


class TeaserManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(ge=1, le=1)
    story: str = Field(min_length=100, max_length=100000)
    age_min: int = Field(ge=3, le=17)
    age_max: int = Field(ge=3, le=17)
    language: str = Field(pattern=r"^[a-z]{2}-[A-Z]{2}$")
    market: str = Field(pattern=r"^[A-Z]{2}$")
    marketing: str = Field(min_length=10, max_length=3000)
    video_file: str = Field(pattern=r"^[A-Za-z0-9_-]+\.mp4$")
    video_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_id: str = Field(min_length=1, max_length=200)
    model_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    seed: int = Field(ge=0, le=2**32 - 1)
    scene_prompts: list[str] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_brief(self):
        if self.age_min > self.age_max or any(not 20 <= len(p) <= 2000 for p in self.scene_prompts):
            raise ValueError("Invalid age range or scene prompts")
        return self


def import_teaser(db, config, directory):
    directory = Path(directory).resolve()
    manifest_file = directory / "manifest.json"
    if manifest_file.is_symlink() or manifest_file.stat().st_size > 150000:
        raise ValueError("Invalid manifest file")
    manifest = TeaserManifest.model_validate_json(manifest_file.read_text(encoding="utf-8"))
    source = directory / manifest.video_file
    if source.is_symlink() or not source.is_file() or not 0 < source.stat().st_size <= 100_000_000:
        raise ValueError("Video must be a regular local MP4, no larger than 100 MB")
    with source.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != manifest.video_sha256:
            raise ValueError("Video checksum mismatch")
    if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", config.public_domain):
        raise ValueError("Set PUBLIC_DOMAIN to the HTTPS API hostname before importing")
    key = hashlib.sha256((manifest.model_dump_json() + config.public_domain).encode()).hexdigest()
    workflow_id = "gpu-import:" + key
    existing = db.content.find_one({"workflow_id": workflow_id})
    if existing:
        return existing["_id"]
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("FFmpeg and ffprobe are required on the API server")
    probe = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(source)],
        check=True, capture_output=True, timeout=30,
    )
    duration = float(json.loads(probe.stdout)["format"]["duration"])
    if not 1 <= duration <= 30:
        raise ValueError("Teaser duration must be between one and thirty seconds")
    output = Path(config.local_video_output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    target = output / (key[:32] + ".mp4")
    with tempfile.TemporaryDirectory(dir=output) as scratch:
        temporary = Path(scratch) / "normalized.mp4"
        subprocess.run([
            ffmpeg, "-v", "error", "-y", "-i", str(source), "-map", "0:v:0", "-an",
            "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,"
                   "pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24",
            "-t", "30", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(temporary),
        ], check=True, capture_output=True, timeout=240)
        # Never replace an approved asset's bytes on a replay.
        if not target.exists():
            try:
                if os.name == "nt":
                    os.rename(temporary, target)  # Windows refuses an existing destination.
                else:
                    os.link(temporary, target)
            except FileExistsError:
                pass  # A concurrent importer already promoted this exact input.
    content = {
        "_id": key, "workflow_id": workflow_id, "story": manifest.story,
        "age_min": manifest.age_min, "age_max": manifest.age_max,
        "language": manifest.language, "market": manifest.market, "marketing": manifest.marketing,
        "assets": [{"type": "video", "url": f"https://{config.public_domain}/media/videos/{target.name}"}],
        "created_at": now(), "generation": {
            "model_id": manifest.model_id, "revision": manifest.model_revision,
            "seed": manifest.seed, "scene_prompts": manifest.scene_prompts,
            "source_sha256": manifest.video_sha256, "source": "interactive-gpu-notebook",
        },
    }
    content.update(QualityControlAgent().run(content))
    db.content.update_one({"workflow_id": workflow_id}, {"$setOnInsert": content}, upsert=True)
    return db.content.find_one({"workflow_id": workflow_id})["_id"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Extracted notebook export directory")
    args = parser.parse_args()
    config = settings()
    print(import_teaser(connect(config), config, args.directory))


if __name__ == "__main__":
    main()
