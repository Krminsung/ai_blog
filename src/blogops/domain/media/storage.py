"""Private S3-compatible quarantine and immutable media storage boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol
from uuid import UUID, uuid4

import aioboto3

from blogops.core.config import get_settings
from blogops.core.errors import AppError

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class PrivateUploadGrant:
    object_ref: str
    upload_url: str
    expires_in: int


class PrivateObjectStorage(Protocol):
    async def initiate_upload(
        self,
        *,
        workspace_id: UUID,
        namespace: str,
        owner_id: UUID,
        filename: str,
        content_type: str,
    ) -> PrivateUploadGrant: ...

    async def head(self, object_ref: str) -> dict[str, object]: ...

    async def get_bytes(self, object_ref: str, *, max_bytes: int) -> bytes: ...

    async def put_immutable(
        self,
        *,
        workspace_id: UUID,
        namespace: str,
        owner_id: UUID,
        content_hash: str,
        content: bytes,
        content_type: str,
    ) -> str: ...

    async def delete(self, object_ref: str) -> None: ...


class S3PrivateObjectStorage:
    def __init__(
        self,
        *,
        endpoint_url: str,
        region: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        presign_ttl: int,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.region = region
        self.bucket = bucket
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.presign_ttl = presign_ttl
        self.session = aioboto3.Session()

    def _client(self):  # type supplied by aioboto3 at runtime
        return self.session.client(
            "s3",
            endpoint_url=self.endpoint_url,
            region_name=self.region,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
        )

    async def initiate_upload(
        self,
        *,
        workspace_id: UUID,
        namespace: str,
        owner_id: UUID,
        filename: str,
        content_type: str,
    ) -> PrivateUploadGrant:
        safe_name = _SAFE_FILENAME.sub("_", filename).strip("._") or "upload.bin"
        object_ref = (
            f"workspaces/{workspace_id}/{namespace}/{owner_id}/quarantine/"
            f"{uuid4().hex}-{safe_name}"
        )
        async with self._client() as client:
            upload_url = await client.generate_presigned_url(
                "put_object",
                Params={"Bucket": self.bucket, "Key": object_ref, "ContentType": content_type},
                ExpiresIn=self.presign_ttl,
            )
        return PrivateUploadGrant(object_ref, upload_url, self.presign_ttl)

    async def head(self, object_ref: str) -> dict[str, object]:
        async with self._client() as client:
            return await client.head_object(Bucket=self.bucket, Key=object_ref)

    async def get_bytes(self, object_ref: str, *, max_bytes: int) -> bytes:
        async with self._client() as client:
            response = await client.get_object(Bucket=self.bucket, Key=object_ref)
            declared_size = int(response.get("ContentLength", -1))
            if declared_size < 0 or declared_size > max_bytes:
                raise AppError("MEDIA_FILE_SIZE_INVALID", "이미지 용량이 허용 범위를 벗어났습니다.", 422)
            body = response["Body"]
            try:
                content = await body.read(max_bytes + 1)
            finally:
                body.close()
            if len(content) != declared_size or len(content) > max_bytes:
                raise AppError("MEDIA_FILE_SIZE_INVALID", "이미지 용량이 허용 범위를 벗어났습니다.", 422)
            return bytes(content)

    async def put_immutable(
        self,
        *,
        workspace_id: UUID,
        namespace: str,
        owner_id: UUID,
        content_hash: str,
        content: bytes,
        content_type: str,
    ) -> str:
        extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(
            content_type,
            "bin",
        )
        object_ref = (
            f"workspaces/{workspace_id}/{namespace}/{owner_id}/versions/"
            f"{content_hash}.{extension}"
        )
        async with self._client() as client:
            await client.put_object(
                Bucket=self.bucket,
                Key=object_ref,
                Body=content,
                ContentType=content_type,
                Metadata={"sha256": content_hash},
            )
        return object_ref

    async def delete(self, object_ref: str) -> None:
        async with self._client() as client:
            await client.delete_object(Bucket=self.bucket, Key=object_ref)


@lru_cache(maxsize=1)
def get_private_object_storage() -> PrivateObjectStorage:
    settings = get_settings()
    if settings.s3_access_key_id is None or settings.s3_secret_access_key is None:
        raise AppError(
            code="OBJECT_STORAGE_NOT_CONFIGURED",
            message="파일 저장소가 구성되지 않았습니다.",
            status_code=503,
        )
    return S3PrivateObjectStorage(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        bucket=settings.s3_bucket,
        access_key_id=settings.s3_access_key_id.get_secret_value(),
        secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        presign_ttl=settings.knowledge_presign_ttl_seconds,
    )
