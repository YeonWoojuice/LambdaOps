#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 OUTPUT_DIRECTORY" >&2
  echo "Creates m3-core.zip, its SHA-256 file, and resolved dependencies there." >&2
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi
if [[ "$1" == "-h" || "$1" == "--help" ]]; then
  usage
  exit 0
fi

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo "Docker CLI and a running, accessible Docker daemon are required." >&2
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
backend_dir="$(cd "$script_dir/.." && pwd -P)"
if [[ ! -f "$backend_dir/requirements.txt" || ! -f "$backend_dir/lambdaops_m3/ingest_handler.py" ]]; then
  echo "Expected requirements.txt and lambdaops_m3 beside scripts/." >&2
  exit 1
fi

mkdir -p -- "$1"
output_dir="$(cd "$1" && pwd -P)"
stage_dir="$(mktemp -d "$output_dir/.m3-build.XXXXXXXX")"
cleanup() {
  if [[ -n "${stage_dir:-}" && -d "$stage_dir" ]]; then
    rm -rf -- "$stage_dir"
  fi
}
trap cleanup EXIT

image="${LAMBDA_PYTHON_IMAGE:-public.ecr.aws/lambda/python:3.12}"
docker run --rm \
  --platform linux/amd64 \
  --entrypoint /bin/sh \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$backend_dir:/src:ro" \
  -v "$stage_dir:/out:rw" \
  "$image" -eu -c 'python /src/scripts/package_lambda.py'

mv -f -- "$stage_dir/m3-core.zip" "$output_dir/m3-core.zip"
mv -f -- "$stage_dir/m3-core.zip.sha256" "$output_dir/m3-core.zip.sha256"
mv -f -- "$stage_dir/requirements-resolved.txt" "$output_dir/requirements-resolved.txt"

if command -v sha256sum >/dev/null 2>&1; then
  (cd "$output_dir" && sha256sum -c m3-core.zip.sha256)
elif command -v shasum >/dev/null 2>&1; then
  (cd "$output_dir" && shasum -a 256 -c m3-core.zip.sha256)
else
  echo "No host SHA-256 verifier found; the builder already checked the digest." >&2
fi

echo "Deployment ZIP: $output_dir/m3-core.zip"
echo "Dependency versions: $output_dir/requirements-resolved.txt"
