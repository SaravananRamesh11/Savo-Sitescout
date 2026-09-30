"""Photo storage behind one interface: Cloudflare R2 (private bucket + presigned GET URLs) or, when the R2
variables are not set, a gitignored local directory (dev/tests). The database only ever stores `storage_key`.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

from app.core import config

LOCAL_DIR = Path(__file__).resolve().parents[2] / "uploads"


class StorageError(RuntimeError):
    pass


def r2_configured() -> bool:
    return all([config.R2_ACCOUNT_ID, config.R2_ACCESS_KEY_ID, config.R2_SECRET_ACCESS_KEY, config.R2_BUCKET])


def backend_name() -> str:
    return "r2" if r2_configured() else "local"


_client = None


def _r2():
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config

        _client = boto3.client(
            "s3",
            endpoint_url=f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=config.R2_ACCESS_KEY_ID,
            aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
            region_name="auto",
            config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"},
                          connect_timeout=10, read_timeout=30),
        )
    return _client


def put(key: str, data: bytes, content_type: str) -> None:
    if r2_configured():
        try:
            _r2().put_object(Bucket=config.R2_BUCKET, Key=key, Body=data, ContentType=content_type)
        except Exception as exc:  # noqa: BLE001 - never leak credentials in the message
            raise StorageError(f"Photo storage upload failed ({type(exc).__name__})") from None
        return
    path = LOCAL_DIR / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def delete(key: str) -> None:
    try:
        if r2_configured():
            _r2().delete_object(Bucket=config.R2_BUCKET, Key=key)
        else:
            (LOCAL_DIR / key).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 - best effort; the DB row is the source of truth
        pass


def url_for(key: str) -> str:
    """Short-lived presigned GET URL (R2) or an API-served path (local fallback)."""
    if r2_configured():
        try:
            return _r2().generate_presigned_url(
                "get_object", Params={"Bucket": config.R2_BUCKET, "Key": key}, ExpiresIn=config.R2_SIGNED_URL_TTL_S)
        except Exception:  # noqa: BLE001
            return ""
    return f"/api/photos/local/{key}"


def read(key: str) -> bytes:
    """Bytes of a stored photo (R2 or the local fallback). Used to embed photos in the Decision Pack PDF."""
    if r2_configured():
        try:
            return _r2().get_object(Bucket=config.R2_BUCKET, Key=key)["Body"].read()
        except Exception as exc:  # noqa: BLE001 - never leak credentials in the message
            raise StorageError(f"Photo storage read failed ({type(exc).__name__})") from None
    return read_local(key)[0]


def read_local(key: str) -> tuple[bytes, str]:
    path = (LOCAL_DIR / key).resolve()
    if LOCAL_DIR.resolve() not in path.parents or not path.exists():
        raise StorageError("not found")
    return path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream"


_MAGIC = [(b"\xff\xd8\xff", "image/jpeg", "jpg"), (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
          (b"RIFF", "image/webp", "webp")]


def sniff_image(data: bytes) -> tuple[str, str] | None:
    """Return (content_type, extension) from the file's magic bytes, ignoring the client-declared type."""
    for magic, ctype, ext in _MAGIC:
        if data.startswith(magic):
            if ctype == "image/webp" and data[8:12] != b"WEBP":
                continue
            return ctype, ext
    return None
