"""Build a source-only Docker Space upload bundle. Never includes .env or Git metadata."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def build(destination):
    root = Path(__file__).resolve().parent.parent
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as bundle:
        for name in ("pyproject.toml", "requirements.lock", ".env.example"):
            bundle.write(root / name, name)
        bundle.write(root / "deploy/huggingface/Dockerfile", "Dockerfile")
        bundle.write(root / "deploy/huggingface/README.md", "README.md")
        for source in sorted((root / "media").glob("*.py")):
            bundle.write(source, "media/" + source.name)
        bundle.write(root / "docs/huggingface-setup.md", "SETUP.md")
    return destination


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.output)
    print("Created source-only Space bundle. No secrets included; no Space was deployed.")
