"""Deploy the CPU Space to Hugging Face Spaces.

Adapted from the deploy helper used in Manga-Translator-OCR: uploads only
git-visible files (respecting .gitignore) plus extra deploy-only excludes, so
the Space repo stays clean.

The repo root IS the Space repo: the Dockerfile lives at the root and builds
the app from main/; the root README.md carries the HF Space frontmatter.

What it does:
  1. Uploads git-visible repo files to the Space (Docker SDK).
  2. Optionally creates the shared private Storage Bucket (--create-bucket).

Auth: uses the cached `hf auth login` token or the HF_TOKEN env var.

Examples:
  python main/scripts/deploy_space.py --dry-run
  python main/scripts/deploy_space.py --repo-id <user>/media-toolbox-cpu --create-bucket
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from huggingface_hub import HfApi
from huggingface_hub.utils import filter_repo_objects

PROJECT_ROOT = Path(__file__).resolve().parents[2]
README_PATH = PROJECT_ROOT / "README.md"

DEFAULT_SPACE_NAME = "media-toolbox-cpu"
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


def resolve_space_id(api: HfApi, explicit: str | None) -> str:
    if explicit:
        return explicit
    env_repo_id = os.getenv("HF_SPACE_ID")
    if env_repo_id:
        return env_repo_id
    username = api.whoami()["name"]
    return f"{username}/{DEFAULT_SPACE_NAME}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy main/ to a Hugging Face Docker Space, respecting .gitignore."
    )
    parser.add_argument("--repo-id",
                        help=f"Space id, e.g. <user>/{DEFAULT_SPACE_NAME}. "
                             "Defaults to HF_SPACE_ID or your HF user + default name.")
    parser.add_argument("--hardware", default=None,
                        help="Space hardware, e.g. cpu-basic (default) or cpu-upgrade.")
    parser.add_argument("--public", action="store_true",
                        help="Create the Space as public (default: private).")
    parser.add_argument("--create-bucket", action="store_true",
                        help="Also create the shared private Storage Bucket.")
    parser.add_argument("--bucket-id", default=None,
                        help=f"Bucket id. Defaults to <user>/{DEFAULT_BUCKET_NAME}.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the upload set without creating or updating anything.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api = HfApi()

    upload_files = filtered_upload_files()
    upload_bytes = sum((PROJECT_ROOT / path).stat().st_size for path in upload_files)
    print(f"Upload set: {len(upload_files)} files, {upload_bytes / 1024:.1f} KiB")

    if args.dry_run:
        for path in upload_files:
            print(f"  {path}")
        return

    if not README_PATH.exists():
        raise SystemExit("Root README.md with the Space frontmatter is required.")
    if "README.md" not in upload_files:
        raise SystemExit("README.md is not in the upload set; check .gitignore.")

    space_id = resolve_space_id(api, args.repo_id)
    print(f"Deploying to https://huggingface.co/spaces/{space_id}")
    api.create_repo(
        repo_id=space_id,
        repo_type="space",
        space_sdk="docker",
        exist_ok=True,
        private=not args.public,
        space_hardware=args.hardware,
    )
    api.upload_folder(
        repo_id=space_id,
        repo_type="space",
        folder_path=PROJECT_ROOT,
        allow_patterns=upload_files,
        ignore_patterns=DEPLOY_IGNORE_PATTERNS,
        commit_message="Deploy Media Toolbox CPU Space",
    )
    print(f"Space available at https://huggingface.co/spaces/{space_id}")

    if args.create_bucket:
        bucket_id = args.bucket_id or f"{api.whoami()['name']}/{DEFAULT_BUCKET_NAME}"
        url = api.create_bucket(bucket_id, private=True, exist_ok=True)
        print(f"Bucket ready: {url}")

    print(
        "\nNext steps (once, in the Space settings UI):\n"
        f"  1. Attach the storage bucket at {BUCKET_MOUNT} (read/write).\n"
        "  2. Schedule an @hourly HF Job running:\n"
        f"       python main/cleanup/cleanup.py --bucket {BUCKET_MOUNT}\n"
        "  3. Verify https://huggingface.co/spaces/"
        f"{space_id} shows 'Bucket: connected' in the startup logs."
    )


if __name__ == "__main__":
    main()
