"""Deploy the Media Toolbox bucket-cleanup function and daily scheduler.

The script provisions the required Google Cloud APIs, service accounts, secret,
second-generation Cloud Run function, and Cloud Scheduler job. It creates a
temporary GCS source bucket for deployment and removes it on success, failure,
or interruption.

Configuration can be supplied with the command-line options shown by ``--help``
or through the matching environment variables. ``HF_TOKEN`` is intentionally
accepted only through the environment so it does not appear in shell history.

Example:
    python main/scripts/deploy_cleanup_function.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from types import FrameType
from typing import NoReturn, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FUNCTION_SOURCE = PROJECT_ROOT / "main" / "cloud_cleanup"


def positive_int(value: str) -> int:
    """Parse a positive integer for argparse."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Deploy the authenticated Media Toolbox cleanup function and its "
            "daily Google Cloud Scheduler job."
        )
    )
    parser.add_argument(
        "--project-id",
        default=os.getenv("GCP_PROJECT_ID", "adept-fountain-349605"),
        help="Google Cloud project (env: GCP_PROJECT_ID).",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("GCP_REGION", "asia-south1"),
        help="Function and scheduler region (env: GCP_REGION).",
    )
    parser.add_argument(
        "--function-name",
        default=os.getenv("GCP_FUNCTION_NAME", "media-toolbox-cleanup"),
        help="Cloud Run function name (env: GCP_FUNCTION_NAME).",
    )
    parser.add_argument(
        "--schedule-name",
        default=os.getenv("GCP_SCHEDULE_NAME", "media-toolbox-cleanup-daily"),
        help="Cloud Scheduler job name (env: GCP_SCHEDULE_NAME).",
    )
    parser.add_argument(
        "--bucket-id",
        default=os.getenv("HF_BUCKET_ID", "kaushikpaul/media-toolbox"),
        help="Hugging Face bucket in owner/name form (env: HF_BUCKET_ID).",
    )
    parser.add_argument(
        "--retention-days",
        type=positive_int,
        default=os.getenv("RETENTION_DAYS", "30"),
        help="Physical retention period (env: RETENTION_DAYS).",
    )
    parser.add_argument(
        "--schedule",
        default=os.getenv("CLEANUP_SCHEDULE", "30 3 * * *"),
        help="Cron schedule (env: CLEANUP_SCHEDULE).",
    )
    parser.add_argument(
        "--time-zone",
        default=os.getenv("CLEANUP_TIME_ZONE", "Asia/Kolkata"),
        help="Scheduler time zone (env: CLEANUP_TIME_ZONE).",
    )
    parser.add_argument(
        "--secret-name",
        default=os.getenv("HF_TOKEN_SECRET_NAME", "media-toolbox-hf-token"),
        help="Secret Manager secret name (env: HF_TOKEN_SECRET_NAME).",
    )
    parser.add_argument(
        "--runtime-account",
        default=os.getenv("GCP_RUNTIME_ACCOUNT", "media-toolbox-cleanup"),
        help="Runtime service-account name (env: GCP_RUNTIME_ACCOUNT).",
    )
    parser.add_argument(
        "--scheduler-account",
        default=os.getenv("GCP_SCHEDULER_ACCOUNT", "media-toolbox-scheduler"),
        help="Scheduler service-account name (env: GCP_SCHEDULER_ACCOUNT).",
    )
    return parser.parse_args()


def run(
    command: Sequence[str],
    *,
    input_text: str | None = None,
    stdout: int | None = None,
    stderr: int | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command without invoking a shell."""
    return subprocess.run(
        list(command),
        check=check,
        input=input_text,
        text=True,
        stdout=stdout,
        stderr=stderr,
    )


def output(command: Sequence[str], *, suppress_stderr: bool = False) -> str:
    result = run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL if suppress_stderr else None,
    )
    return result.stdout.strip()


def succeeds(command: Sequence[str]) -> bool:
    return (
        run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def require_commands(*commands: str) -> None:
    missing = [command for command in commands if shutil.which(command) is None]
    if missing:
        raise SystemExit(f"Required command is missing: {', '.join(missing)}")


def create_source_archive(destination: Path) -> None:
    if not FUNCTION_SOURCE.is_dir():
        raise SystemExit(f"Function source directory is missing: {FUNCTION_SOURCE}")

    files = [
        path
        for path in sorted(FUNCTION_SOURCE.rglob("*"))
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    ]
    if not files:
        raise SystemExit(f"Function source directory is empty: {FUNCTION_SOURCE}")

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(FUNCTION_SOURCE))


def add_secret_version(secret_name: str, project_id: str, token: str) -> None:
    run(
        [
            "gcloud",
            "secrets",
            "versions",
            "add",
            secret_name,
            "--data-file=-",
            f"--project={project_id}",
        ],
        input_text=token,
        stdout=subprocess.DEVNULL,
    )


def cleanup_staging_bucket(staging_bucket: str, project_id: str) -> bool:
    """Best-effort removal of the temporary source bucket."""
    describe = [
        "gcloud",
        "storage",
        "buckets",
        "describe",
        staging_bucket,
        f"--project={project_id}",
    ]
    if not succeeds(describe):
        return True

    run(
        [
            "gcloud",
            "storage",
            "rm",
            "--recursive",
            f"{staging_bucket}/**",
            f"--project={project_id}",
            "--quiet",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    run(
        [
            "gcloud",
            "storage",
            "buckets",
            "delete",
            staging_bucket,
            f"--project={project_id}",
            "--quiet",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return not succeeds(describe)


def interrupted(signum: int, _frame: FrameType | None) -> NoReturn:
    raise KeyboardInterrupt(f"received signal {signum}")


def deploy(args: argparse.Namespace) -> str:
    project_number = output(
        [
            "gcloud",
            "projects",
            "describe",
            args.project_id,
            "--format=value(projectNumber)",
        ]
    )
    deployer_account = output(
        ["gcloud", "config", "get-value", "account"], suppress_stderr=True
    )
    if not deployer_account or deployer_account == "(unset)":
        raise SystemExit("No active gcloud account. Run: gcloud auth login")

    runtime_service_account = (
        f"{args.runtime_account}@{args.project_id}.iam.gserviceaccount.com"
    )
    scheduler_service_account = (
        f"{args.scheduler_account}@{args.project_id}.iam.gserviceaccount.com"
    )
    staging_bucket = (
        f"gs://{args.project_id}-mt-cleanup-{project_number}-{int(time.time())}-{os.getpid()}"
    )

    with tempfile.TemporaryDirectory(prefix="media-toolbox-cleanup-deploy-") as temp:
        source_archive = Path(temp) / "function.zip"
        create_source_archive(source_archive)
        staging_created = False
        deployment_succeeded = False

        try:
            print(f"Enabling Google Cloud APIs in {args.project_id}...")
            run(
                [
                    "gcloud",
                    "services",
                    "enable",
                    "artifactregistry.googleapis.com",
                    "cloudbuild.googleapis.com",
                    "cloudfunctions.googleapis.com",
                    "cloudscheduler.googleapis.com",
                    "run.googleapis.com",
                    "secretmanager.googleapis.com",
                    "storage.googleapis.com",
                    f"--project={args.project_id}",
                    "--quiet",
                ]
            )

            accounts = (
                (
                    args.runtime_account,
                    runtime_service_account,
                    "Media Toolbox bucket cleanup",
                ),
                (
                    args.scheduler_account,
                    scheduler_service_account,
                    "Media Toolbox cleanup scheduler",
                ),
            )
            for account_name, account_email, display_name in accounts:
                if not succeeds(
                    [
                        "gcloud",
                        "iam",
                        "service-accounts",
                        "describe",
                        account_email,
                        f"--project={args.project_id}",
                    ]
                ):
                    run(
                        [
                            "gcloud",
                            "iam",
                            "service-accounts",
                            "create",
                            account_name,
                            f"--display-name={display_name}",
                            f"--project={args.project_id}",
                        ]
                    )

            for account_email in (runtime_service_account, scheduler_service_account):
                run(
                    [
                        "gcloud",
                        "iam",
                        "service-accounts",
                        "add-iam-policy-binding",
                        account_email,
                        f"--member=user:{deployer_account}",
                        "--role=roles/iam.serviceAccountUser",
                        f"--project={args.project_id}",
                        "--quiet",
                    ],
                    stdout=subprocess.DEVNULL,
                )

            if not succeeds(
                [
                    "gcloud",
                    "secrets",
                    "describe",
                    args.secret_name,
                    f"--project={args.project_id}",
                ]
            ):
                run(
                    [
                        "gcloud",
                        "secrets",
                        "create",
                        args.secret_name,
                        "--replication-policy=automatic",
                        f"--project={args.project_id}",
                    ]
                )

            env_token = os.getenv("HF_TOKEN", "")
            if env_token:
                add_secret_version(args.secret_name, args.project_id, env_token)
            else:
                enabled_version = output(
                    [
                        "gcloud",
                        "secrets",
                        "versions",
                        "list",
                        args.secret_name,
                        f"--project={args.project_id}",
                        "--filter=state=ENABLED",
                        "--limit=1",
                        "--format=value(name)",
                    ]
                )
                if not enabled_version:
                    cached_token = output(["hf", "auth", "token", "--quiet"])
                    if not cached_token:
                        raise SystemExit(
                            "No HF token is available. Run `hf auth login` or set "
                            "HF_TOKEN."
                        )
                    add_secret_version(args.secret_name, args.project_id, cached_token)

            run(
                [
                    "gcloud",
                    "secrets",
                    "add-iam-policy-binding",
                    args.secret_name,
                    f"--member=serviceAccount:{runtime_service_account}",
                    "--role=roles/secretmanager.secretAccessor",
                    f"--project={args.project_id}",
                    "--quiet",
                ],
                stdout=subprocess.DEVNULL,
            )

            run(
                [
                    "gcloud",
                    "storage",
                    "buckets",
                    "create",
                    staging_bucket,
                    f"--location={args.region}",
                    "--uniform-bucket-level-access",
                    f"--project={args.project_id}",
                    "--quiet",
                ]
            )
            staging_created = True
            run(
                [
                    "gcloud",
                    "storage",
                    "cp",
                    str(source_archive),
                    f"{staging_bucket}/function.zip",
                    f"--project={args.project_id}",
                    "--quiet",
                ]
            )

            print(f"Deploying {args.function_name} to {args.region}...")
            run(
                [
                    "gcloud",
                    "functions",
                    "deploy",
                    args.function_name,
                    "--gen2",
                    f"--region={args.region}",
                    "--runtime=python312",
                    f"--source={staging_bucket}/function.zip",
                    "--entry-point=cleanup_media_bucket",
                    "--trigger-http",
                    "--no-allow-unauthenticated",
                    f"--service-account={runtime_service_account}",
                    (
                        f"--set-env-vars=HF_BUCKET_ID={args.bucket_id},"
                        f"RETENTION_DAYS={args.retention_days}"
                    ),
                    f"--set-secrets=HF_TOKEN={args.secret_name}:latest",
                    "--memory=256Mi",
                    "--timeout=540s",
                    "--concurrency=1",
                    "--min-instances=0",
                    "--max-instances=1",
                    f"--project={args.project_id}",
                    "--quiet",
                ]
            )

            function_url = output(
                [
                    "gcloud",
                    "functions",
                    "describe",
                    args.function_name,
                    "--gen2",
                    f"--region={args.region}",
                    f"--project={args.project_id}",
                    "--format=value(url)",
                ]
            )
            if not function_url:
                raise RuntimeError("The deployed function did not report a URL")

            run(
                [
                    "gcloud",
                    "functions",
                    "add-invoker-policy-binding",
                    args.function_name,
                    f"--region={args.region}",
                    f"--project={args.project_id}",
                    f"--member=serviceAccount:{scheduler_service_account}",
                    "--quiet",
                ],
                stdout=subprocess.DEVNULL,
            )

            scheduler_exists = succeeds(
                [
                    "gcloud",
                    "scheduler",
                    "jobs",
                    "describe",
                    args.schedule_name,
                    f"--location={args.region}",
                    f"--project={args.project_id}",
                ]
            )
            run(
                [
                    "gcloud",
                    "scheduler",
                    "jobs",
                    "update" if scheduler_exists else "create",
                    "http",
                    args.schedule_name,
                    f"--location={args.region}",
                    f"--schedule={args.schedule}",
                    f"--time-zone={args.time_zone}",
                    f"--uri={function_url}",
                    "--http-method=GET",
                    f"--oidc-service-account-email={scheduler_service_account}",
                    f"--oidc-token-audience={function_url}",
                    "--attempt-deadline=600s",
                    f"--project={args.project_id}",
                    "--quiet",
                ]
            )
            deployment_succeeded = True
            return function_url
        finally:
            if staging_created:
                removed = cleanup_staging_bucket(staging_bucket, args.project_id)
                if removed:
                    print(f"Temporary source bucket removed: {staging_bucket}")
                elif deployment_succeeded:
                    raise RuntimeError(
                        "Function deployed, but the temporary source bucket could "
                        f"not be removed: {staging_bucket}"
                    )
                else:
                    print(
                        "WARNING: deployment failed and the temporary source bucket "
                        f"could not be removed: {staging_bucket}",
                        file=sys.stderr,
                    )


def main() -> None:
    args = parse_args()
    require_commands("gcloud", "hf")

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        function_url = deploy(args)
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)

    print(f"Cleanup function deployed: {function_url}")
    print(
        f"Daily schedule: {args.schedule} ({args.time_zone}), "
        f"retention: {args.retention_days} days"
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit("Deployment interrupted") from None
