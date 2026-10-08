"""Optional real Wan inference for an interactive Kaggle/Colab GPU session.

GPU dependencies are isolated from the production API. No mock or paid fallback.
"""
import hashlib
import json
from pathlib import Path

MODEL_ID = "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"
MODEL_REVISION = "b7f0d36dfbc20355d3184ad63c709d888cdb640f"


def generate_teaser(full_story, scenes, output_dir, *, seed=42, age_min=4, age_max=7):
    import torch
    from diffusers import AutoencoderKLWan, WanPipeline
    from diffusers.schedulers.scheduling_unipc_multistep import UniPCMultistepScheduler
    from diffusers.utils import export_to_video

    if not torch.cuda.is_available():
        raise RuntimeError("Select a GPU runtime first. No CPU or paid fallback is enabled.")
    if len(full_story.strip()) < 100 or not 1 <= len(scenes) <= 3:
        raise ValueError("Provide the complete story and one to three opening scene prompts")
    if not 3 <= age_min <= age_max <= 17:
        raise ValueError("Invalid intended age range")
    if any(not isinstance(scene, str) or not 20 <= len(scene) <= 2000 for scene in scenes):
        raise ValueError("Each opening scene needs a detailed prompt of 20–2000 characters")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    vae = AutoencoderKLWan.from_pretrained(
        MODEL_ID, revision=MODEL_REVISION, subfolder="vae", torch_dtype=torch.float32
    )
    pipe = WanPipeline.from_pretrained(
        MODEL_ID, revision=MODEL_REVISION, vae=vae, torch_dtype=dtype
    )
    pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config, flow_shift=5.0)
    pipe.enable_model_cpu_offload()
    pipe.vae.enable_tiling()
    frames = []
    for index, scene in enumerate(scenes):
        prompt = (
            "Original colorful 2D cartoon animation, clear expressive characters, gentle motion, "
            "family friendly, no existing franchise characters, no text, no logos. " + scene
        )
        video = pipe(
            prompt=prompt,
            negative_prompt="violence, sexual content, horror, weapons, gore, nudity, text, watermarks, "
                            "deformed faces, extra limbs, blurry, flickering",
            height=832, width=480, num_frames=81, num_inference_steps=30,
            guidance_scale=6.0, generator=torch.Generator(device="cpu").manual_seed(seed + index),
        ).frames[0]
        frames.extend(video)
    target = output_dir / "teaser.mp4"
    export_to_video(frames, str(target), fps=16)
    manifest = {
        "schema_version": 1, "story": full_story.strip(), "age_min": age_min, "age_max": age_max,
        "language": "en-US", "market": "US",
        "marketing": "Parents and guardians: discover the full storybook through our Telegram catalog.",
        "video_file": target.name, "video_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "model_id": MODEL_ID, "model_revision": MODEL_REVISION, "seed": seed,
        "scene_prompts": scenes,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return target
