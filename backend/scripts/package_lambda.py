"""Run only inside the Python 3.12 Lambda Linux x86_64 build container."""

from __future__ import annotations

import hashlib
from importlib import metadata
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


SOURCE = Path("/src")
OUTPUT = Path("/out")
PACKAGE = OUTPUT / "package"
ARCHIVE = OUTPUT / "m3-core.zip"


def build() -> None:
    if sys.version_info[:2] != (3, 12) or sys.platform != "linux" or platform.machine() != "x86_64":
        raise RuntimeError("Build requires Linux x86_64 with Python 3.12")

    PACKAGE.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--no-compile",
            "--only-binary=:all:",
            "--requirement",
            str(SOURCE / "requirements.txt"),
            "--target",
            str(PACKAGE),
        ],
        check=True,
    )

    source_package = SOURCE / "lambdaops_m3"
    target_package = PACKAGE / "lambdaops_m3"
    target_package.mkdir()
    for source in sorted(source_package.glob("*.py")):
        shutil.copyfile(source, target_package / source.name)

    required = {
        "lambdaops_m3/__init__.py",
        "lambdaops_m3/contracts.py",
        "lambdaops_m3/ingest_handler.py",
        "lambdaops_m3/projection.py",
        "lambdaops_m3/read_handler.py",
        "lambdaops_m3/store.py",
        "lambdaops_m3/dsql_connection.py",
        "lambdaops_m3/dsql_projection.py",
        "lambdaops_m3/dsql_queries.py",
        "lambdaops_m3/dsql_store.py",
        "lambdaops_m3/query_contract.py",
    }
    files = sorted(
        path
        for path in PACKAGE.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    names = {path.relative_to(PACKAGE).as_posix() for path in files}
    if not required <= names:
        raise RuntimeError("The Lambda package is missing application modules")
    if not any(name.startswith("boto3/") for name in names) or not any(
        name.startswith("botocore/") for name in names
    ):
        raise RuntimeError("The Lambda package must include boto3 and botocore")
    if not all(any(name.startswith(package + "/") for name in names)
               for package in ("psycopg", "psycopg_binary", "certifi")):
        raise RuntimeError("The Lambda package must include the PostgreSQL driver and TLS roots")
    if any(name.startswith(("backend/", "tests/", "fixtures/")) or name.endswith(".pen") for name in names):
        raise RuntimeError("The package contains unrelated project files")

    with ZipFile(ARCHIVE, "w") as archive:
        for path in files:
            name = path.relative_to(PACKAGE).as_posix()
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())

    smoke_dir = OUTPUT / "smoke"
    smoke_dir.mkdir()
    with ZipFile(ARCHIVE) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("ZIP integrity check failed")
        archive.extractall(smoke_dir)

    smoke_code = """
from pathlib import Path
import boto3
import botocore
import certifi
import psycopg
import psycopg_binary
from lambdaops_m3 import ingest_handler, read_handler, dsql_store
root = Path('/out/smoke').resolve()
for module in (boto3, botocore, certifi, psycopg, psycopg_binary, ingest_handler, read_handler, dsql_store):
    if root not in Path(module.__file__).resolve().parents:
        raise RuntimeError(f'{module.__name__} was not imported from the ZIP')
if not callable(ingest_handler.lambda_handler) or not callable(read_handler.lambda_handler):
    raise RuntimeError('A Lambda handler is not callable')
if psycopg.pq.__impl__ != 'binary' or not Path(certifi.where()).is_file():
    raise RuntimeError('The packaged binary driver or TLS roots are unavailable')
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(smoke_dir)
    subprocess.run([sys.executable, "-B", "-c", smoke_code], cwd=OUTPUT, env=env, check=True)

    resolved = sorted(
        f"{dist.metadata['Name']}=={dist.version}"
        for dist in metadata.distributions(path=[str(PACKAGE)])
        if dist.metadata.get("Name")
    )
    (OUTPUT / "requirements-resolved.txt").write_text("\n".join(resolved) + "\n")
    digest = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    (OUTPUT / "m3-core.zip.sha256").write_text(f"{digest}  m3-core.zip\n")
    if hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() != digest:
        raise RuntimeError("SHA-256 verification failed")
    print(f"ZIP smoke test passed; SHA-256: {digest}")


if __name__ == "__main__":
    build()
