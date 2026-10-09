# Google Colab and Kaggle cartoon generation

This workflow uses **only Kaggle or Google Colab for GPU compute**. There is no
Hugging Face Space integration, paid video API fallback, or background quota
switching. The public Wan model weights are downloaded from their model repository;
that is a model download, not a hosted inference service.

## Start a notebook

Two separate original examples are provided:

- Kaggle: `notebooks/free_cartoon_teaser.ipynb` â€” Milo and Pippa's mystery seed.
- Colab: [Open Nori and Pip's missing kite in Colab](https://colab.research.google.com/github/snsmax/ai-kids-media-agent/blob/main/notebooks/colab_cartoon_teaser.ipynb).

For Colab, sign in, save a private copy in Drive, choose Runtime â†’ Change runtime
type â†’ T4 GPU, then Run all. Decline paid compute upgrades. The first cell checks
GPU/Internet before downloading dependencies. Free GPU allocation and host RAM
are not guaranteed; this notebook has been tested for code validity locally,
but a real Colab GPU run must be verified separately. Download the ZIP when it
finishes: temporary runtime files disappear when the session ends. No credentials
are needed for the public model. Do not paste merchant tokens into either notebook.

Both examples generate four opening scenes (20.25 seconds). Their full stories
are public original samples; replace them in your private copy for paid products.
`notebooks/story_examples.json` is the source for these distinct configurations.

1. Sign into [Kaggle Code](https://www.kaggle.com/code) or
   [Google Colab](https://colab.research.google.com/).
2. Upload `notebooks/free_cartoon_teaser.ipynb`. Keep it private because it contains
   the complete paid story. Do not publish it as a public Kaggle notebook/dataset.
3. Kaggle: create a notebook, use File > Import Notebook, select the file, select a
   GPU accelerator and enable Internet. Complete any account verification yourself.
   Colab: use File > Upload notebook and Runtime > Change runtime type > GPU.
4. Run dependency and GPU checks. Write your full original story and a detailed
   opening scene prompt in the configuration cell. A complete original example is
   provided, so an initial test does not need a separate paid text service.
5. Run generation for **one scene first**. Each scene is 81 frames at 16 fps,
   approximately five seconds. Up to three scenes can form a 5â€“15 second teaser.
   Reuse the exact character descriptions across scenes; consistency is not guaranteed.
6. Preview and download `cartoon-teaser-export.zip`. Colab offers a download; Kaggle
   exposes it in the Output panel. The export contains the MP4 plus a private
   manifest with the full story, prompts, seed, model revision and file checksum.

The notebook calls the actual Wan 2.1 T2V 1.3B model through Diffusers. It requests
cartoon styling rather than drawing block characters. It generates silent footage;
voice, captions and product-specific Telegram links are not included. The model
revision and selected Python dependencies are pinned, but the host-supplied PyTorch
runtime varies. Backend tests do not prove model inference works on every free GPU.
A real 20.25-second silent cartoon was generated successfully on a signed-in Kaggle T4 session, with its checksum and all 324 frames verified locally.

The first Kaggle test was accepted with GPU enabled in its saved metadata, but
execution failed: CUDA was unavailable and package downloads failed DNS resolution.
Treat this as a failed CPU/offline session, not a successful GPU deployment. The
notebook now checks actual GPU and Internet access before installing anything.
If this happens, check account phone verification at Kaggle Settings, then enable
GPU T4 x2 and Internet in Notebook options and restart. An API token and a reported
GPU quota alone do not prove the notebook has GPU access.

A subsequent session confirmed a Tesla T4 and working Internet, but full-size
attention exhausted VRAM. The notebook now uses FP16 on T4 (native BF16 only on
newer GPUs), requests 368x640 footage and permits only fused SDPA attention
backends. This avoids silently using the attention math implementation that
requested approximately 48 GB. These settings produced a verified 20.25-second cartoon on Kaggle.
Longer clips generate each scene independently and save scene checkpoints. The importer still
normalizes successful footage to 720x1280, which is upscaling, not extra detail.

The Colab example enables `low_ram=True`. It loads the text encoder directly on
the GPU, encodes prompts, releases that encoder, then loads the video model.
This avoids holding both large models in host RAM at once. Encoded prompts move
to the inference GPU before each scene. The initial combined loader restarted
the free Colab kernel; the staged loader has been observed generating frames
on a free T4. This is not yet a verified completed Colab export.

Free GPU availability, RAM, disk, quotas and session lifetimes vary. CPU offloading
reduces VRAM pressure but needs host RAM. A failed or unavailable GPU session stops
without a paid fallback. Start with one scene and reduce the frame count to 49
(`4*k+1`) in `wan_generate.py` and rebuild the notebook if memory is insufficient.
Do not turn paid runtime features on. This cannot promise twenty finished cartoons
daily or act as the always-on backend.

## Import into the existing platform

Extract the ZIP into a private directory on the API server. Configure MongoDB,
PUBLIC_DOMAIN (the HTTPS API hostname) and LOCAL_VIDEO_OUTPUT_DIR (the persistent
directory shared with the API and worker). Install FFmpeg/ffprobe; both Docker
images already install these. Run:

```sh
python -m media.import_teaser /path/to/cartoon-teaser
```

The importer validates manifest schema and checksum, rejects path traversal and
symlinks, accepts MP4 files up to 100 MB and footage of 1â€“30 seconds, and transcodes
to silent vertical 720x1280 H.264 at 24 fps. It preserves the complete story as
content, with the teaser as its video asset. Re-importing the same export reuses
the record. It always creates a **pending_review** draft, never a review or payment
listing. Operators preview using `/operator/videos/<filename>` and X-API-Key.

A human reviewer must check age suitability, story safety, all visuals, rights
and parent-facing marketing through the existing reviewer API. Public asset
access is blocked until the exact content version has approval. Approved content
can use the existing Instagram workflow. Create and separately review a Telegram
storybook product to sell the full story; current delivery is a text document,
not an illustrated PDF playbook. The existing daily CPU renderer is a separate
mode and is not replaced by unattended Colab/Kaggle inference.

## Sources and model license

- [Wan model card and Apache-2.0 license](https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B-Diffusers).
- [Official Diffusers Wan instructions](https://huggingface.co/docs/diffusers/v0.35.1/api/pipelines/wan).
- [Kaggle notebooks](https://www.kaggle.com/docs/notebooks) and
  [GPU quota guidance](https://www.kaggle.com/docs/efficient-gpu-usage).
- [Colab availability and restrictions](https://research.google.com/colaboratory/faq.html).

Preserve applicable license/NOTICE obligations if distributing model weights.
Generated content still needs a rights review. No celebrity likenesses or existing
cartoon franchise characters are used in the original example.

Rebuild the notebook after editing its source with
`python scripts/build_gpu_notebook.py`. It deliberately has no saved outputs,
credentials, automatic publication or cloud deployment claims.
