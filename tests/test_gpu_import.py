"""GPU boundaries are mocked explicitly; these tests never claim model inference."""
import ast
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from media.import_teaser import import_teaser
from media.safety import require_approval


def export(tmp_path):
    source = tmp_path / "export"
    source.mkdir()
    video = source / "teaser.mp4"
    video.write_bytes(b"unit-test-only-video")
    manifest = {
        "schema_version": 1, "story": "A complete original story about two kind friends. " * 5,
        "age_min": 4, "age_max": 7, "language": "en-US", "market": "US",
        "marketing": "For parents and guardians.", "video_file": "teaser.mp4",
        "video_sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
        "model_id": "Wan-AI/Wan2.1-T2V-1.3B-Diffusers", "model_revision": "b" * 40,
        "seed": 42, "scene_prompts": ["Original fox and rabbit friends discover a seed in their garden."],
    }
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return source, manifest


def mock_tools(monkeypatch, duration=5):
    from media import import_teaser as module

    monkeypatch.setattr(module.shutil, "which", lambda name: name)

    def run(command, **kwargs):
        if command[0] == "ffprobe":
            return subprocess.CompletedProcess(command, 0, json.dumps({"format": {"duration": duration}}))
        assert "-an" in command and "-t" in command
        Path(command[-1]).write_bytes(b"mock-normalized-video")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, "run", run)


def test_import_is_private_and_replay_preserves_asset(env, tmp_path, monkeypatch):
    config, db, _, _ = env
    config = config.model_copy(update={"public_domain": "media.test", "local_video_output_dir": str(tmp_path / "media")})
    source, _ = export(tmp_path)
    mock_tools(monkeypatch)
    content_id = import_teaser(db, config, source)
    content = db.content.find_one({"_id": content_id})
    assert content["status"] == "pending_review"
    assert len(content["story"]) > 100
    assert db.reviews.count_documents({}) == 0
    with pytest.raises(PermissionError):
        require_approval(db, content)
    target = tmp_path / "media" / (content_id[:32] + ".mp4")
    original = target.stat().st_mtime_ns
    assert import_teaser(db, config, source) == content_id
    assert target.stat().st_mtime_ns == original
    assert db.content.count_documents({}) == 1


@pytest.mark.parametrize("change", [
    {"video_file": "../secret.mp4"}, {"video_file": "https://outside.test/file.mp4"},
    {"age_min": 10, "age_max": 4}, {"schema_version": 99}, {"approved": True},
])
def test_manifest_rejects_unsafe_inputs(env, tmp_path, change):
    config, db, _, _ = env
    source, manifest = export(tmp_path)
    (source / "manifest.json").write_text(json.dumps({**manifest, **change}), encoding="utf-8")
    with pytest.raises(ValidationError):
        import_teaser(db, config, source)
    assert db.content.count_documents({}) == 0


def test_checksum_mismatch_is_not_imported(env, tmp_path):
    config, db, _, _ = env
    source, _ = export(tmp_path)
    (source / "teaser.mp4").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        import_teaser(db, config, source)


@pytest.mark.parametrize("duration", [0, 31, float("nan")])
def test_overlong_or_invalid_media_is_not_imported(env, tmp_path, monkeypatch, duration):
    config, db, _, _ = env
    config = config.model_copy(update={"public_domain": "media.test"})
    source, _ = export(tmp_path)
    mock_tools(monkeypatch, duration)
    with pytest.raises(ValueError, match="duration"):
        import_teaser(db, config, source)
    assert db.content.count_documents({}) == 0


def test_notebook_has_compilable_code_and_no_saved_outputs():
    root = Path(__file__).resolve().parent.parent
    notebook = json.loads((root / "notebooks/free_cartoon_teaser.ipynb").read_text())
    assert notebook["nbformat"] == 4
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            assert not cell["outputs"] and cell["execution_count"] is None
            source = "".join(line for line in cell["source"] if not line.startswith("%"))
            ast.parse(source)
    source = (root / "notebooks/wan_generate.py").read_text()
    assert "enable_model_cpu_offload" in source
    assert "b7f0d36dfbc20355d3184ad63c709d888cdb640f" in source


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                    reason="Real media integration requires FFmpeg and ffprobe")
def test_real_mp4_import_and_decode(env, tmp_path):
    config, db, _, _ = env
    config = config.model_copy(update={"public_domain": "media.test", "local_video_output_dir": str(tmp_path / "media")})
    source, manifest = export(tmp_path)
    subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=blue:s=480x832:d=2", "-c:v", "libx264", str(source / "teaser.mp4")], check=True)
    manifest["video_sha256"] = hashlib.sha256((source / "teaser.mp4").read_bytes()).hexdigest()
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    content_id = import_teaser(db, config, source)
    target = tmp_path / "media" / (content_id[:32] + ".mp4")
    metadata = json.loads(subprocess.check_output([
        shutil.which("ffprobe"), "-v", "error", "-show_streams", "-of", "json", str(target)]))
    video = next(stream for stream in metadata["streams"] if stream["codec_type"] == "video")
    assert (video["width"], video["height"], video["codec_name"]) == (720, 1280, "h264")
    assert all(stream["codec_type"] != "audio" for stream in metadata["streams"])
