"""
app/services/storage.py
---------------------------
Weak Area #2: "No Real Storage Layer". Project source files were living
directly on the API server's local disk (app.config.settings.jobs_root),
which doesn't survive a redeploy and doesn't work once there's more than
one API/worker instance.

This module is a small storage abstraction with two backends:
    - LocalFilesystemStorage  -- unchanged dev behavior, zero setup
    - S3CompatibleStorage     -- AWS S3, MinIO, or Cloudflare R2, all of
                                 which speak the same S3 API. Point
                                 s3_endpoint_url at MinIO/R2; leave it
                                 unset for real AWS S3.

Every consumer (projects router, Celery tasks) talks to `get_storage()`
and a logical `key` (a path-like string such as
"projects/proj_ab12cd/source/alu.sv") -- never to a raw filesystem Path or
an S3 client directly. EDA tools (Yosys/Verilator/Icarus) still need real
files on local disk to run against, so the actual workflow is:
    1. materialize_project() downloads every key under a project's prefix
       into a fresh local scratch directory
    2. the tool runs against that scratch directory like before
    3. any output/changed file is uploaded back with put_file()
    4. the scratch directory is deleted -- it's disposable, the storage
       backend is the durable copy
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path

from app.config import settings


class StorageError(Exception):
    pass


class StorageBackend(ABC):
    @abstractmethod
    def put_file(self, key: str, local_path: Path) -> None: ...

    @abstractmethod
    def put_bytes(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def get_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def get_to_path(self, key: str, dest_path: Path) -> None: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def list_keys(self, prefix: str) -> list[str]: ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> None: ...

    def materialize_prefix(self, prefix: str, local_dir: Path) -> list[Path]:
        """
        Downloads every key under `prefix` into local_dir, preserving the
        path structure relative to prefix. Returns the list of local paths
        written -- the common entry point tasks use to get a real,
        tool-runnable directory out of whatever backend is configured.
        """
        local_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for key in self.list_keys(prefix):
            rel = key[len(prefix):].lstrip("/")
            if not rel:
                continue
            dest = local_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            self.get_to_path(key, dest)
            written.append(dest)
        return written


class LocalFilesystemStorage(StorageBackend):
    """Dev-default backend. `key` maps directly to a path under storage_root."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise StorageError(f"Invalid storage key (path escapes root): {key}")
        return path

    def put_file(self, key: str, local_path: Path) -> None:
        dest = self._resolve(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(local_path, dest)

    def put_bytes(self, key: str, data: bytes) -> None:
        dest = self._resolve(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def get_bytes(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.is_file():
            raise StorageError(f"Key not found: {key}")
        return path.read_bytes()

    def get_to_path(self, key: str, dest_path: Path) -> None:
        src = self._resolve(key)
        if not src.is_file():
            raise StorageError(f"Key not found: {key}")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest_path)

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()

    def list_keys(self, prefix: str) -> list[str]:
        base = self._resolve(prefix)
        if not base.exists():
            return []
        if base.is_file():
            return [prefix]
        return [
            f"{prefix.rstrip('/')}/{p.relative_to(base)}"
            for p in base.rglob("*")
            if p.is_file()
        ]

    def delete_prefix(self, prefix: str) -> None:
        base = self._resolve(prefix)
        if base.is_dir():
            shutil.rmtree(base, ignore_errors=True)
        elif base.is_file():
            base.unlink(missing_ok=True)


class S3CompatibleStorage(StorageBackend):
    """
    Works against AWS S3, MinIO, or Cloudflare R2 -- all three expose the
    same S3 API, the only difference is `endpoint_url` (None for real AWS,
    set to the MinIO/R2 endpoint otherwise) and how credentials are issued.
    """

    def __init__(self) -> None:
        import boto3  # local import: keeps boto3 optional when storage_backend == "local"

        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        self._bucket = settings.s3_bucket

    def put_file(self, key: str, local_path: Path) -> None:
        self._client.upload_file(str(local_path), self._bucket, key)

    def put_bytes(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data)

    def get_bytes(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        return response["Body"].read()

    def get_to_path(self, key: str, dest_path: Path) -> None:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self._bucket, key, str(dest_path))

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError:
            return False

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    def delete_prefix(self, prefix: str) -> None:
        keys = self.list_keys(prefix)
        if not keys:
            return
        objects = [{"Key": k} for k in keys]
        # S3 delete_objects caps at 1000 keys per call.
        for i in range(0, len(objects), 1000):
            self._client.delete_objects(Bucket=self._bucket, Delete={"Objects": objects[i: i + 1000]})


@lru_cache(maxsize=1)
def get_storage() -> StorageBackend:
    """Singleton, picked by settings.storage_backend ('local' or 's3')."""
    if settings.storage_backend == "s3":
        return S3CompatibleStorage()
    return LocalFilesystemStorage(settings.storage_root)
