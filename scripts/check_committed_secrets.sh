#!/usr/bin/env bash
set -euo pipefail

if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "Tracked .env file detected. Remove it from Git." >&2
  exit 1
fi

secret_assignment_regex='(JWT_SECRET|POSTGRES_PASSWORD|MINIO_ROOT_PASSWORD|MINIO_ACCESS_KEY|MINIO_SECRET_KEY|GRAFANA_ADMIN_PASSWORD)=([^<$][^[:space:]]{7,})'
private_key_regex='-----BEGIN (RSA |EC |OPENSSH |DSA |PRIVATE )?PRIVATE KEY-----'
aws_key_regex='AKIA[0-9A-Z]{16}'

tracked_files="$(git ls-files)"

if printf '%s\n' "$tracked_files" | grep -Ev '^(\.env\.example|docs/|scripts/check_committed_secrets\.sh)$' | xargs grep -EIn "$secret_assignment_regex" >/tmp/pdf-extract-secret-scan.txt 2>/dev/null; then
  echo "Potential committed secret assignment found:" >&2
  cat /tmp/pdf-extract-secret-scan.txt >&2
  exit 1
fi

if printf '%s\n' "$tracked_files" | xargs grep -EIn "$private_key_regex|$aws_key_regex" >/tmp/pdf-extract-secret-scan.txt 2>/dev/null; then
  echo "Potential committed private key or cloud credential found:" >&2
  cat /tmp/pdf-extract-secret-scan.txt >&2
  exit 1
fi

echo "No committed secrets detected."
