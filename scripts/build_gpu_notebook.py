"""Build the shared Kaggle/Colab notebook with no credentials or output cells."""
import json
from pathlib import Path


def build(profile="kaggle"):
    root = Path(__file__).resolve().parent.parent
    example = json.loads((root / "notebooks/story_examples.json").read_text(encoding="utf-8"))[profile]
    cells = []

    def markdown(source):
        cells.append({"cell_type": "markdown", "metadata": {}, "source": source.splitlines(True)})

    def code(source):
        cells.append({"cell_type": "code", "metadata": {}, "execution_count": None,
                      "outputs": [], "source": source.splitlines(True)})

    markdown("""# Free GPU cartoon teasers

Upload this notebook to Kaggle or Colab. Select GPU and enable Internet before running.
Start with one scene. Free quotas, RAM, disk and GPU availability vary: this is an
interactive workflow, not a guaranteed 20/day server. No paid fallback is enabled.
Keep the notebook private: it contains your complete paid story. Never add database,
Telegram, reviewer or operator credentials here.

Model: [Wan 2.1 T2V 1.3B](https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B-Diffusers),
Apache-2.0. Model revision and inference libraries are pinned below. The inference
code follows the official Wan Diffusers example. A five-second clip was generated
on a Kaggle T4; local tests do not run GPU inference. Memory offload does not guarantee the
model will fit every free runtime. Preserve model license/NOTICE if redistributing
weights. The license does not guarantee rights or safety of generated outputs.
""")
    code("""# Check the actual runtime before downloading packages or model weights.
import urllib.error
import urllib.request

import torch

print("PyTorch:", torch.__version__, "CUDA build:", torch.version.cuda)
if not torch.cuda.is_available():
    raise RuntimeError(
        "This session has no accessible GPU. Kaggle: complete account phone verification, "
        "select GPU T4 x2 in Notebook options and restart the session. "
        "Colab: select a GPU runtime and reconnect. Saved GPU settings alone are insufficient."
    )
print("GPU:", torch.cuda.get_device_name(0))
print("VRAM GB:", round(torch.cuda.get_device_properties(0).total_memory / 2**30, 1))
try:
    with urllib.request.urlopen("https://pypi.org/simple/diffusers/", timeout=15) as response:
        print("Package network check:", response.status)
except (urllib.error.URLError, TimeoutError) as exc:
    raise RuntimeError(
        "Internet is unavailable. Kaggle: enable Internet in Notebook options after account "
        "verification, then restart. No paid fallback is enabled."
    ) from exc
# CPU RAM and disk are also required; model weights are a substantial download.
""")
    code("""# GPU-only dependencies. Stop immediately if installation fails.
import subprocess
import sys

subprocess.run([
    sys.executable, "-m", "pip", "install", "diffusers==0.35.1", "transformers==4.56.2",
    "accelerate==1.10.1", "ftfy==6.3.1", "imageio-ffmpeg==0.6.0", "sentencepiece==0.2.1"
], check=True)
""")
    source = (root / "notebooks/wan_generate.py").read_text(encoding="utf-8")
    code(source.split('"""', 2)[2].lstrip())
    markdown(f"## {example['title']}\n\nThis {profile} example contains four original opening scenes (~20.25 seconds).")
    config = (
        "# Original story example. Keep paid stories and exports private.\n"
        "import os\n\nfrom IPython.display import Video, display\n\n"
        f"FULL_STORY = {example['story']!r}\n"
        f"SCENES = {example['scenes']!r}\n"
        "OUTPUT_DIR = '/kaggle/working/cartoon-teaser' if os.path.isdir('/kaggle/working') else '/content/cartoon-teaser'\n"
        "# Four scenes: 81 frames each at 16 fps = 20.25 seconds. Maximum five scenes.\n"
        "video = generate_teaser(FULL_STORY, SCENES, OUTPUT_DIR, seed=42, age_min=4, age_max=7)\n"
        "display(Video(str(video), embed=True))\n"
    )
    code(config)
    code("""# Download for PRIVATE human review. Nothing is published here.
import shutil

archive = shutil.make_archive(str(Path(OUTPUT_DIR).parent / 'cartoon-teaser-export'), 'zip', OUTPUT_DIR)
print("Export:", archive)
try:
    from google.colab import files
except ImportError:
    print("Kaggle: download cartoon-teaser-export.zip from the Output panel.")
else:
    files.download(archive)
""")
    markdown("""## Import and safety review

Extract the private export on your API server. It needs FFmpeg, ffprobe, a persistent
media directory and PUBLIC_DOMAIN pointing to its HTTPS endpoint. Run:

`python -m media.import_teaser /path/to/cartoon-teaser`

This verifies the video checksum, normalizes it and creates pending_review content.
Use the existing reviewer API to check the story, animation, rights and parent-facing
marketing. Import does not grant approval, publish, or create a Telegram listing.
The complete story stays separate from opening-only scene prompts. Create/review the
Telegram storybook product afterwards. Illustrated PDF playbooks remain separate work.

Kaggle/Colab sessions are interactive. No quota evasion, keep-alive hacks, unattended
24/7 sessions or background switching between accounts is included. The existing CPU
scheduler stays separate and will not silently route jobs into this notebook.
""")
    for i, cell in enumerate(cells):
        cell["id"] = f"cartoon-{i}"
    notebook = {"cells": cells, "metadata": {"kernelspec": {
        "display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.12"}},
        "nbformat": 4, "nbformat_minor": 5}
    filename = "colab_cartoon_teaser.ipynb" if profile == "colab" else "free_cartoon_teaser.ipynb"
    target = root / "notebooks" / filename
    target.write_text(json.dumps(notebook, indent=2) + "\n", encoding="utf-8")
    return target


if __name__ == "__main__":
    print(build("kaggle"))
    print(build("colab"))
