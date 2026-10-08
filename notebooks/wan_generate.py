"""Optional real Wan inference for an interactive Kaggle/Colab GPU session.

GPU dependencies are isolated from the production API. No mock or paid fallback.
"""
import hashlib
import json
from pathlib import Path

MODEL_ID = "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"
MODEL_REVISION = "b7f0d36dfbc20355d3184ad63c709d888cdb640f"


def generate_teaser(full_story, scenes, output_dir, *, seed=42, age_min=4, age_max=7, low_ram=False):
    import gc

    import torch
    from diffusers import AutoencoderKLWan, WanPipeline
    from diffusers.schedulers.scheduling_unipc_multistep import UniPCMultistepScheduler
    from diffusers.utils import export_to_video
    from torch.nn.attention import SDPBackend, sdpa_kernel

    if not torch.cuda.is_available():
        raise RuntimeError("Select a GPU runtime first. No CPU or paid fallback is enabled.")
    if len(full_story.strip()) < 100 or not 1 <= len(scenes) <= 5:
        raise ValueError("Provide the complete story and one to five opening scene prompts")
    if not 3 <= age_min <= age_max <= 17:
        raise ValueError("Invalid intended age range")
    if any(not isinstance(scene, str) or not 20 <= len(scene) <= 2000 for scene in scenes):
        raise ValueError("Each opening scene needs a detailed prompt of 20–2000 characters")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    # T4 may report emulated BF16 support, but its fused attention requires FP16.
    dtype = torch.bfloat16 if torch.cuda.get_device_capability()[0] >= 8 else torch.float16
    prefix = (
        "Original colorful 2D cartoon animation, clear expressive characters, gentle motion, "
        "family friendly, no existing franchise characters, no text, no logos. "
    )
    negative = (
        "violence, sexual content, horror, weapons, gore, nudity, text, watermarks, "
        "deformed faces, extra limbs, blurry, flickering"
    )
    embeddings = []
    if low_ram:
        from transformers import AutoTokenizer, UMT5EncoderModel

        print("Encoding scene prompts on GPU before loading the video model", flush=True)
        encoder = UMT5EncoderModel.from_pretrained(
            MODEL_ID, revision=MODEL_REVISION, subfolder="text_encoder", torch_dtype=dtype,
            device_map={"": "cuda"}, low_cpu_mem_usage=True,
        )
        tokenizer = AutoTokenizer.from_pretrained(
            MODEL_ID, revision=MODEL_REVISION, subfolder="tokenizer"
        )
        text_pipe = WanPipeline(tokenizer=tokenizer, text_encoder=encoder, vae=None, scheduler=None)
        with torch.inference_mode():
            for scene in scenes:
                positive, excluded = text_pipe.encode_prompt(
                    prefix + scene, negative_prompt=negative, device=torch.device("cuda"), dtype=dtype
                )
                embeddings.append((positive.cpu(), excluded.cpu()))
                del positive, excluded
        del text_pipe, encoder, tokenizer
        gc.collect()
        torch.cuda.empty_cache()
        print("Text encoder released; loading video model", flush=True)
    vae = AutoencoderKLWan.from_pretrained(
        MODEL_ID, revision=MODEL_REVISION, subfolder="vae", torch_dtype=torch.float32
    )
    optional_components = {"text_encoder": None, "tokenizer": None} if low_ram else {}
    pipe = WanPipeline.from_pretrained(
        MODEL_ID, revision=MODEL_REVISION, vae=vae, torch_dtype=dtype, **optional_components
    )
    pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config, flow_shift=5.0)
    pipe.enable_model_cpu_offload()
    pipe.vae.enable_tiling()
    frames = []
    for index, scene in enumerate(scenes):
        prompt_args = (
            {"prompt_embeds": embeddings[index][0], "negative_prompt_embeds": embeddings[index][1]}
            if low_ram else {"prompt": prefix + scene, "negative_prompt": negative}
        )
        print(f"Generating scene {index + 1}/{len(scenes)} with {dtype}, 368x640", flush=True)
        def report_progress(pipeline, step_index, timestep, callback_kwargs):
            if (step_index + 1) % 5 == 0:
                print(f"Scene {index + 1}/{len(scenes)}: step {step_index + 1}/30", flush=True)
            return callback_kwargs

        # Forbid the quadratic-memory math fallback that exhausted the free T4.
        with sdpa_kernel([SDPBackend.FLASH_ATTENTION, SDPBackend.EFFICIENT_ATTENTION]):
            video = pipe(
                **prompt_args,
                height=640, width=368, num_frames=81, num_inference_steps=30,
                guidance_scale=6.0, generator=torch.Generator(device="cpu").manual_seed(seed + index),
                callback_on_step_end=report_progress,
            ).frames[0]
        export_to_video(video, str(output_dir / f"scene-{index + 1}.mp4"), fps=16)
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
