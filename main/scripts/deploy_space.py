"""Deploy the Space described by the root README to Hugging Face Spaces.

Adapted from the deploy helper used in Manga-Translator-OCR: uploads only
git-visible files (respecting .gitignore) plus extra deploy-only excludes, so
the Space repo stays clean.

The root README.md is the single source of truth for the Space SDK and
entrypoint. For the CPU Space it selects the Docker SDK; for the future GPU
Space it selects the Gradio SDK and an app_file. This script never edits the
README.

What it does:
  1. Validates the root README Space metadata and matching local entrypoint.
  2. Uploads git-visible repo files to the Space using that SDK.
  3. Optionally creates the shared private Storage Bucket (--create-bucket).

Auth: uses the cached `hf auth login` token or the HF_TOKEN env var.

Examples:
  python main/scripts/deploy_space.py --dry-run
  python main/scripts/deploy_space.py --repo-id <user>/media-toolbox-cpu --create-bucket

After changing README.md to `sdk: gradio` with the GPU app_file:
  python main/scripts/deploy_space.py --repo-id <user>/media-toolbox-gpu
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from huggingface_hub import HfApi, Volume
from huggingface_hub.repocard import metadata_load
from huggingface_hub.utils import filter_repo_objects

PROJECT_ROOT = Path(__file__).resolve().parents[2]
README_PATH = PROJECT_ROOT / "README.md"

DEFAULT_SPACE_NAMES = {
    "docker": "media-toolbox-cpu",
    "gradio": "media-toolbox-gpu",
}
DEFAULT_HARDWARE = {
    "docker": "cpu-basic",
    "gradio": "zero-a10g",
}
DEFAULT_BUCKET_NAME = "media-toolbox"
BUCKET_MOUNT = "/data/media-bucket"

# Extra excludes on top of .gitignore (paths relative to the repo root).
DEPLOY_IGNORE_PATTERNS = [
    ".venv/**",
    "venv/**",
    "__pycache__/**",
    "**/__pycache__/**",
    "*.pyc",
    ".env",
    ".env.*",
    ".idea/**",
]


def git_visible_files() -> list[str]:
    """Repo files Git would include, respecting .gitignore.

    Falls back to walking the directory if this is not a Git checkout.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "ls-files", "--cached", "--others",
             "--exclude-standard"],
            check=True, capture_output=True, text=True,
        )
        files = [line for line in result.stdout.splitlines() if line]
        if files:
            return files
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return [
        str(p.relative_to(PROJECT_ROOT))
        for p in sorted(PROJECT_ROOT.rglob("*"))
        if p.is_file()
    ]


def filtered_upload_files() -> list[str]:
    # git ls-files --cached can list paths deleted from disk but still staged.
    allow = [f for f in git_visible_files() if (PROJECT_ROOT / f).is_file()]
    return list(filter_repo_objects(allow, allow_patterns=allow,
                                    ignore_patterns=DEPLOY_IGNORE_PATTERNS))


def read_space_metadata() -> dict:
    """Read and validate the root README's Hugging Face Space frontmatter."""
    if not README_PATH.exists():
        raise SystemExit("Root README.md with Hugging Face Space frontmatter is required.")

    metadata = metadata_load(README_PATH) or {}
    sdk = str(metadata.get("sdk", "")).strip().lower()
    if sdk not in DEFAULT_SPACE_NAMES:
        raise SystemExit(
            "README.md must set 'sdk: docker' for CPU or 'sdk: gradio' for ZeroGPU."
        )
    metadata["sdk"] = sdk

    if sdk == "docker":
        if not (PROJECT_ROOT / "Dockerfile").is_file():
            raise SystemExit("README.md selects Docker, but the root Dockerfile is missing.")
        app_port = metadata.get("app_port", 7860)
        if not isinstance(app_port, int) or not 1 <= app_port <= 65535:
            raise SystemExit("README.md app_port must be an integer between 1 and 65535.")
    else:
        app_file = str(metadata.get("app_file", "app.py")).strip()
        app_path = Path(app_file)
        if not app_file or app_path.is_absolute() or ".." in app_path.parts:
            raise SystemExit("README.md app_file must be a safe path relative to the repo root.")
        if not (PROJECT_ROOT / app_path).is_file():
            raise SystemExit(
                f"README.md selects Gradio, but app_file does not exist: {app_file}"
            )

    return metadata


def resolve_space_id(api: HfApi, explicit: str | None, sdk: str) -> str:
    if explicit:
        return explicit
    env_repo_id = os.getenv("HF_SPACE_ID")
    if env_repo_id:
        return env_repo_id
    username = api.whoami()["name"]
    return f"{username}/{DEFAULT_SPACE_NAMES[sdk]}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Deploy the Space configured by root README.md, respecting .gitignore."
        )
    )
    parser.add_argument("--repo-id",
                        help="Space id. Defaults to HF_SPACE_ID or your HF user plus "
                             "media-toolbox-cpu/media-toolbox-gpu based on README sdk.")
    parser.add_argument(
        "--hardware",
        default=None,
        help=(
            "Space hardware. Defaults to cpu-basic for Docker or zero-a10g for "
            "Gradio. Pass a different flavor to override it."
        ),
    )
    parser.add_argument("--public", action="store_true",
                        help="Create the Space as public (default: private).")
    parser.add_argument("--create-bucket", action="store_true",
                        help="Create the shared private Storage Bucket and attach it to this Space.")
    parser.add_argument(
        "--attach-bucket",
        action="store_true",
        help=(
            "Attach the shared bucket to this Space. This replaces the Space's "
            "current volume list. --create-bucket implies this option."
        ),
    )
    parser.add_argument("--bucket-id", default=None,
                        help=f"Bucket id. Defaults to <user>/{DEFAULT_BUCKET_NAME}.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the upload set without creating or updating anything.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api = HfApi()
    metadata = read_space_metadata()
    sdk = metadata["sdk"]
    hardware = args.hardware or DEFAULT_HARDWARE[sdk]

    upload_files = filtered_upload_files()
    upload_bytes = sum((PROJECT_ROOT / path).stat().st_size for path in upload_files)
    entrypoint = "Dockerfile" if sdk == "docker" else metadata.get("app_file", "app.py")
    print(f"Space config: sdk={sdk}, entrypoint={entrypoint}, hardware={hardware}")
    print(f"Upload set: {len(upload_files)} files, {upload_bytes / 1024:.1f} KiB")

    if args.dry_run:
        for path in upload_files:
            print(f"  {path}")
        return

    if "README.md" not in upload_files:
        raise SystemExit("README.md is not in the upload set; check .gitignore.")

    space_id = resolve_space_id(api, args.repo_id, sdk)
    namespace = space_id.split("/", 1)[0]
    bucket_id = args.bucket_id or f"{namespace}/{DEFAULT_BUCKET_NAME}"

    if args.create_bucket:
        url = api.create_bucket(bucket_id, private=True, exist_ok=True)
        print(f"Bucket ready: {url}")

    print(f"Deploying to https://huggingface.co/spaces/{space_id}")
    api.create_repo(
        repo_id=space_id,
        repo_type="space",
        space_sdk=sdk,
        exist_ok=True,
        private=not args.public,
        space_hardware=hardware,
    )
    api.upload_folder(
        repo_id=space_id,
        repo_type="space",
        folder_path=PROJECT_ROOT,
        allow_patterns=upload_files,
        ignore_patterns=DEPLOY_IGNORE_PATTERNS,
        commit_message=f"Deploy Media Toolbox {sdk.title()} Space",
    )
    api.request_space_hardware(repo_id=space_id, hardware=hardware)
    print(f"Requested Space hardware: {hardware}")
    print(f"Space available at https://huggingface.co/spaces/{space_id}")

    if args.create_bucket or args.attach_bucket:
        api.set_space_volumes(
            repo_id=space_id,
            volumes=[Volume(type="bucket", source=bucket_id, mount_path=BUCKET_MOUNT)],
        )
        print(f"Attached {bucket_id} read/write at {BUCKET_MOUNT}")

    print(
        "\nNext steps:\n"
        + (
            f"  1. Bucket {bucket_id} is attached at {BUCKET_MOUNT}.\n"
            if args.create_bucket or args.attach_bucket
            else (
                "  1. Attach the existing shared bucket:\n"
                f"       hf spaces volumes set {space_id} --volume "
                f"hf://buckets/{bucket_id}:{BUCKET_MOUNT}\n"
            )
        )
        + "  2. Create one cleanup schedule for the shared bucket (CPU deploy only):\n"
        f"       hf jobs scheduled run --name media-toolbox-cleanup "
        f"--volume hf://spaces/{space_id}:/workspace:ro "
        f"--volume hf://buckets/{bucket_id}:{BUCKET_MOUNT} "
        f"@hourly python:3.12-slim python /workspace/main/cleanup/cleanup.py "
        f"--bucket {BUCKET_MOUNT}\n"
        "  3. Verify the Space build/startup logs and run /_health.\n"
        f"  4. Space: https://huggingface.co/spaces/{space_id}"
    )


if __name__ == "__main__":
    main()
