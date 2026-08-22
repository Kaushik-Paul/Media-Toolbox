#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
FUNCTION_SOURCE="$PROJECT_ROOT/main/cloud_cleanup"

PROJECT_ID="${GCP_PROJECT_ID:-adept-fountain-349605}"
REGION="${GCP_REGION:-asia-south1}"
FUNCTION_NAME="${GCP_FUNCTION_NAME:-media-toolbox-cleanup}"
SCHEDULE_NAME="${GCP_SCHEDULE_NAME:-media-toolbox-cleanup-daily}"
HF_BUCKET_ID="${HF_BUCKET_ID:-kaushikpaul/media-toolbox}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
SCHEDULE="${CLEANUP_SCHEDULE:-30 3 * * *}"
TIME_ZONE="${CLEANUP_TIME_ZONE:-Asia/Kolkata}"
SECRET_NAME="${HF_TOKEN_SECRET_NAME:-media-toolbox-hf-token}"
RUNTIME_ACCOUNT_NAME="${GCP_RUNTIME_ACCOUNT:-media-toolbox-cleanup}"
SCHEDULER_ACCOUNT_NAME="${GCP_SCHEDULER_ACCOUNT:-media-toolbox-scheduler}"

for command in gcloud hf zip mktemp; do
  command -v "$command" >/dev/null || {
    echo "Required command is missing: $command" >&2
    exit 1
  }
done

[[ "$RETENTION_DAYS" =~ ^[1-9][0-9]*$ ]] || {
  echo "RETENTION_DAYS must be a positive integer" >&2
  exit 1
}

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
DEPLOYER_ACCOUNT="$(gcloud config get-value account 2>/dev/null)"
RUNTIME_SERVICE_ACCOUNT="$RUNTIME_ACCOUNT_NAME@$PROJECT_ID.iam.gserviceaccount.com"
SCHEDULER_SERVICE_ACCOUNT="$SCHEDULER_ACCOUNT_NAME@$PROJECT_ID.iam.gserviceaccount.com"
STAGING_BUCKET="gs://${PROJECT_ID}-mt-cleanup-${PROJECT_NUMBER}-$(date +%s)-$$"
TEMP_DIR="$(mktemp -d /tmp/media-toolbox-cleanup-deploy.XXXXXX)"
SOURCE_ARCHIVE="$TEMP_DIR/function.zip"

cleanup_staging() {
  if gcloud storage buckets describe "$STAGING_BUCKET" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud storage rm --recursive "$STAGING_BUCKET/**" --project="$PROJECT_ID" --quiet >/dev/null 2>&1 || true
    gcloud storage buckets delete "$STAGING_BUCKET" --project="$PROJECT_ID" --quiet >/dev/null 2>&1 || true
  fi
  rm -rf -- "$TEMP_DIR"
}
trap cleanup_staging EXIT INT TERM

echo "Enabling Google Cloud APIs in $PROJECT_ID..."
gcloud services enable \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  cloudfunctions.googleapis.com \
  cloudscheduler.googleapis.com \
  run.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  --project="$PROJECT_ID" --quiet

if ! gcloud iam service-accounts describe "$RUNTIME_SERVICE_ACCOUNT" \
  --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$RUNTIME_ACCOUNT_NAME" \
    --display-name="Media Toolbox bucket cleanup" \
    --project="$PROJECT_ID"
fi
if ! gcloud iam service-accounts describe "$SCHEDULER_SERVICE_ACCOUNT" \
  --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SCHEDULER_ACCOUNT_NAME" \
    --display-name="Media Toolbox cleanup scheduler" \
    --project="$PROJECT_ID"
fi

if [[ -z "$DEPLOYER_ACCOUNT" || "$DEPLOYER_ACCOUNT" == "(unset)" ]]; then
  echo "No active gcloud account. Run: gcloud auth login" >&2
  exit 1
fi
for service_account in "$RUNTIME_SERVICE_ACCOUNT" "$SCHEDULER_SERVICE_ACCOUNT"; do
  gcloud iam service-accounts add-iam-policy-binding "$service_account" \
    --member="user:$DEPLOYER_ACCOUNT" \
    --role=roles/iam.serviceAccountUser \
    --project="$PROJECT_ID" --quiet >/dev/null
done

if ! gcloud secrets describe "$SECRET_NAME" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud secrets create "$SECRET_NAME" --replication-policy=automatic \
    --project="$PROJECT_ID"
fi

if [[ -n "${HF_TOKEN:-}" ]]; then
  printf '%s' "$HF_TOKEN" | gcloud secrets versions add "$SECRET_NAME" \
    --data-file=- --project="$PROJECT_ID" >/dev/null
elif ! gcloud secrets versions list "$SECRET_NAME" --project="$PROJECT_ID" \
  --filter='state=ENABLED' --limit=1 --format='value(name)' | grep -q .; then
  hf auth token --quiet | gcloud secrets versions add "$SECRET_NAME" \
    --data-file=- --project="$PROJECT_ID" >/dev/null
fi

gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
  --member="serviceAccount:$RUNTIME_SERVICE_ACCOUNT" \
  --role=roles/secretmanager.secretAccessor \
  --project="$PROJECT_ID" --quiet >/dev/null

(cd "$FUNCTION_SOURCE" && zip -qr "$SOURCE_ARCHIVE" .)
gcloud storage buckets create "$STAGING_BUCKET" \
  --location="$REGION" --uniform-bucket-level-access \
  --project="$PROJECT_ID" --quiet
gcloud storage cp "$SOURCE_ARCHIVE" "$STAGING_BUCKET/function.zip" \
  --project="$PROJECT_ID" --quiet

echo "Deploying $FUNCTION_NAME to $REGION..."
gcloud functions deploy "$FUNCTION_NAME" \
  --gen2 \
  --region="$REGION" \
  --runtime=python312 \
  --source="$STAGING_BUCKET/function.zip" \
  --entry-point=cleanup_media_bucket \
  --trigger-http \
  --no-allow-unauthenticated \
  --service-account="$RUNTIME_SERVICE_ACCOUNT" \
  --set-env-vars="HF_BUCKET_ID=$HF_BUCKET_ID,RETENTION_DAYS=$RETENTION_DAYS" \
  --set-secrets="HF_TOKEN=$SECRET_NAME:latest" \
  --memory=256Mi \
  --timeout=540s \
  --concurrency=1 \
  --min-instances=0 \
  --max-instances=1 \
  --project="$PROJECT_ID" \
  --quiet

FUNCTION_URL="$(gcloud functions describe "$FUNCTION_NAME" --gen2 \
  --region="$REGION" --project="$PROJECT_ID" --format='value(url)')"

gcloud functions add-invoker-policy-binding "$FUNCTION_NAME" \
  --region="$REGION" --project="$PROJECT_ID" \
  --member="serviceAccount:$SCHEDULER_SERVICE_ACCOUNT" --quiet >/dev/null

if gcloud scheduler jobs describe "$SCHEDULE_NAME" --location="$REGION" \
  --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$SCHEDULE_NAME" \
    --location="$REGION" \
    --schedule="$SCHEDULE" \
    --time-zone="$TIME_ZONE" \
    --uri="$FUNCTION_URL" \
    --http-method=GET \
    --oidc-service-account-email="$SCHEDULER_SERVICE_ACCOUNT" \
    --oidc-token-audience="$FUNCTION_URL" \
    --attempt-deadline=600s \
    --project="$PROJECT_ID" --quiet
else
  gcloud scheduler jobs create http "$SCHEDULE_NAME" \
    --location="$REGION" \
    --schedule="$SCHEDULE" \
    --time-zone="$TIME_ZONE" \
    --uri="$FUNCTION_URL" \
    --http-method=GET \
    --oidc-service-account-email="$SCHEDULER_SERVICE_ACCOUNT" \
    --oidc-token-audience="$FUNCTION_URL" \
    --attempt-deadline=600s \
    --project="$PROJECT_ID" --quiet
fi

echo "Cleanup function deployed: $FUNCTION_URL"
echo "Daily schedule: $SCHEDULE ($TIME_ZONE), retention: $RETENTION_DAYS days"
echo "Temporary source bucket removed: $STAGING_BUCKET"
