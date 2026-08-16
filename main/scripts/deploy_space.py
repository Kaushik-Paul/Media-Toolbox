"""Deploy the Space described by the root README to Hugging Face Spaces.

Adapted from the deploy helper used in Manga-Translator-OCR: builds a temporary
CPU- or GPU-specific deployment package from git-visible files (respecting
.gitignore), so each Space repo receives only the infrastructure it can use.

The root README.md is the single source of truth for the Space SDK and
entrypoint. For the CPU Space it maps ``Dockerfile.cpu`` to the required root
``Dockerfile``. For the GPU Space it maps ``requirements.gpu.txt`` and
``packages.gpu.txt`` to the names consumed by the Gradio builder. This script
never edits the README.

What it does:
  1. Validates the root README Space metadata and matching local infrastructure.
  2. Stages and uploads the SDK-specific package to the selected Space.
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
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
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

CPU_DOCKERFILE = "Dockerfile.cpu"
CPU_REQUIREMENTS = "requirements.cpu.txt"
GPU_REQUIREMENTS = "requirements.gpu.txt"
GPU_PACKAGES = "packages.gpu.txt"
INFRA_SOURCE_FILES = {
    CPU_DOCKERFILE,
    CPU_REQUIREMENTS,
    GPU_REQUIREMENTS,
    GPU_PACKAGES,
}

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


def filtered_source_files() -> list[str]:
    # git ls-files --cached can list paths deleted from disk but still staged.
    allow = [f for f in git_visible_files() if (PROJECT_ROOT / f).is_file()]
    return list(filter_repo_objects(allow, allow_patterns=allow,
                                    ignore_patterns=DEPLOY_IGNORE_PATTERNS))


@dataclass(frozen=True)
class DeploymentFile:
    """A source-repository file and its path in the Space repository."""

    source: str
    destination: str


def deployment_files(sdk: str) -> list[DeploymentFile]:
    """Return the exact SDK-specific package, including infrastructure maps."""
    files: list[DeploymentFile] = []
    for path in filtered_source_files():
        if path in INFRA_SOURCE_FILES:
            continue
        # The CPU image has no use for AI model code or its heavy dependencies.
        if sdk == "docker" and path.startswith("main/gpu/"):
            continue
        files.append(DeploymentFile(path, path))

    if sdk == "docker":
        files.extend([
            DeploymentFile(CPU_DOCKERFILE, "Dockerfile"),
            DeploymentFile(CPU_REQUIREMENTS, CPU_REQUIREMENTS),
        ])
    else:
        files.extend([
            DeploymentFile(GPU_REQUIREMENTS, "requirements.txt"),
            DeploymentFile(GPU_PACKAGES, "packages.txt"),
        ])

    destinations = [item.destination for item in files]
    if len(destinations) != len(set(destinations)):
        raise SystemExit("Deployment package contains duplicate destination paths.")
    return sorted(files, key=lambda item: item.destination)


def stage_deployment(files: list[DeploymentFile], stage_root: Path) -> None:
    """Copy the selected package into an empty temporary Space repo root."""
    for item in files:
        source = PROJECT_ROOT / item.source
        destination = stage_root / item.destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def validate_gpu_packages() -> None:
    """Ensure packages.gpu.txt is safe for HF's xargs-based apt installer."""
    for line_number, raw in enumerate(
        (PROJECT_ROOT / GPU_PACKAGES).read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        package = raw.strip()
        if not package:
            continue
        if package.startswith("#") or any(char.isspace() for char in package):
            raise SystemExit(
                f"{GPU_PACKAGES}:{line_number} must contain one Debian package "
                "name and no comments; Hugging Face passes every token to apt-get."
            )


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
        for required in (CPU_DOCKERFILE, CPU_REQUIREMENTS):
            if not (PROJECT_ROOT / required).is_file():
                raise SystemExit(
                    f"README.md selects Docker, but {required} is missing."
                )
        app_port = metadata.get("app_port", 7860)
        if not isinstance(app_port, int) or not 1 <= app_port <= 65535:
            raise SystemExit("README.md app_port must be an integer between 1 and 65535.")
    else:
        for required in (GPU_REQUIREMENTS, GPU_PACKAGES):
            if not (PROJECT_ROOT / required).is_file():
                raise SystemExit(
                    f"README.md selects Gradio, but {required} is missing."
                )
        validate_gpu_packages()
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
            "Deploy the Space configured by root README.md using the matching "
            "root infrastructure files."
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

    upload_files = deployment_files(sdk)
    upload_bytes = sum((PROJECT_ROOT / item.source).stat().st_size
                       for item in upload_files)
    entrypoint = "Dockerfile" if sdk == "docker" else metadata.get("app_file", "app.py")
    print(f"Space config: sdk={sdk}, entrypoint={entrypoint}, hardware={hardware}")
    print(f"Upload set: {len(upload_files)} files, {upload_bytes / 1024:.1f} KiB")

    if args.dry_run:
        for item in upload_files:
            mapping = (f" <- {item.source}"
                       if item.source != item.destination else "")
            print(f"  {item.destination}{mapping}")
        return

    if not any(item.destination == "README.md" for item in upload_files):
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
        private=False,
        space_hardware=hardware,
    )
    # create_repo(exist_ok=True) does not change an existing repo's visibility,
    # so explicitly keep both application Spaces public on every deployment.
    api.update_repo_settings(
        repo_id=space_id,
        repo_type="space",
        private=False,
    )
    with tempfile.TemporaryDirectory(prefix="media-toolbox-deploy-") as tmp:
        stage_root = Path(tmp)
        stage_deployment(upload_files, stage_root)
        api.upload_folder(
            repo_id=space_id,
            repo_type="space",
            folder_path=stage_root,
            # Keep the remote Space an exact mirror of this selected package;
            # additions in this commit supersede matching deletions.
            delete_patterns="*",
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

    cleanup_space_id = (
        space_id if sdk == "docker"
        else f"{namespace}/{DEFAULT_SPACE_NAMES['docker']}"
    )
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
        f"--volume hf://spaces/{cleanup_space_id}:/workspace:ro "
        f"--volume hf://buckets/{bucket_id}:{BUCKET_MOUNT} "
        f"@hourly python:3.12-slim python /workspace/main/cleanup/cleanup.py "
        f"--bucket {BUCKET_MOUNT}\n"
        "  3. Verify the Space build/startup logs and run /_health.\n"
        f"  4. Space: https://huggingface.co/spaces/{space_id}"
    )


if __name__ == "__main__":
    main()
