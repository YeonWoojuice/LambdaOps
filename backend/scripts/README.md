# M3 Lambda ZIP

For the separate administrator-only DSQL setup tool, see
[DSQL initialization and runtime permissions](dsql-bootstrap.md). That script is
not included in the Lambda ZIP.

## Controlled Incident for an authenticated read demo

`seed_demo_incident.py` inserts one clearly marked `DEMO` Incident directly into
the deployed DSQL cluster. It is an administrator test input, **not** a
CloudWatch Alarm or EventBridge delivery. Use it only to demonstrate that the
authenticated LambdaOps browser can read an actual stored Incident.

Run it from a trusted checkout with `backend/requirements.txt` installed. First
preview the target and unique ID:

```sh
python backend/scripts/seed_demo_incident.py \
  --terraform-root infra/environments/dev/m3
```

Review the preview JSON. To write exactly that test input, repeat the command
with its `seed_id` and `transitioned_at`, plus the displayed account, region,
and cluster ID:

```sh
python backend/scripts/seed_demo_incident.py \
  --terraform-root infra/environments/dev/m3 \
  --seed-id PREVIEW_SEED_ID --transitioned-at PREVIEW_TRANSITIONED_AT \
  --expected-account-id PREVIEW_ACCOUNT_ID \
  --expected-region PREVIEW_REGION \
  --expected-cluster-id PREVIEW_CLUSTER_ID --apply
```

The script checks the current AWS account and ACTIVE cluster before opening a
TLS verified administrator connection. It uses the existing M3 storage path and
prints no credentials or database token. Keep the returned Incident ID for
list/detail verification. The video must state that the Incident was seeded as
a controlled test; it does not prove automatic Alarm ingestion.

From the repository root, build with Docker and an explicit output directory:

```sh
./backend/scripts/build_lambda_zip.sh ./build/m3
```

The script targets the Python 3.12 Lambda Linux x86_64 runtime. It installs
`backend/requirements.txt` (including boto3, binary psycopg 3 and certifi), copies only
the `lambdaops_m3` Python modules, creates `m3-core.zip`, then imports both
handlers, the DSQL adapter, boto3 and the binary driver from that ZIP. It also
checks the packaged TLS CA bundle. It writes
`m3-core.zip.sha256` and `requirements-resolved.txt` beside the ZIP, and
checks the SHA-256 after moving the archive to the output directory. The ZIP
has `lambdaops_m3/` and dependency directories at its root. Tests, fixtures,
the Pen UI, and other repository files are excluded.

Pass the same absolute `build/m3/m3-core.zip` path to both
`ingest_package_zip_path` and `read_package_zip_path` in the M3 Terraform
module. Build the ZIP before `terraform plan`. Save its SHA-256 and resolved
dependency versions with the reviewed deployment. The image can be pinned by
setting `LAMBDA_PYTHON_IMAGE` to an approved image digest.
