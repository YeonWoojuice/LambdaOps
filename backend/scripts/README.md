# M3 Lambda ZIP

From the repository root, build with Docker and an explicit output directory:

```sh
./backend/scripts/build_lambda_zip.sh ./build/m3
```

The script targets the Python 3.12 Lambda Linux x86_64 runtime. It installs
`backend/requirements.txt` (including boto3 and its dependencies), copies only
the `lambdaops_m3` Python modules, creates `m3-core.zip`, then imports both
handlers and boto3 from an extracted copy of that ZIP. It writes
`m3-core.zip.sha256` and `requirements-resolved.txt` beside the ZIP, and
checks the SHA-256 after moving the archive to the output directory. The ZIP
has `lambdaops_m3/` and dependency directories at its root. Tests, fixtures,
the Pen UI, and other repository files are excluded.

Pass the same absolute `build/m3/m3-core.zip` path to both
`ingest_package_zip_path` and `read_package_zip_path` in the M3 Terraform
module. Build the ZIP before `terraform plan`. Save its SHA-256 and resolved
dependency versions with the reviewed deployment. The image can be pinned by
setting `LAMBDA_PYTHON_IMAGE` to an approved image digest.
