"""S3-compatible object storage boundary for direct multipart uploads."""

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
class UploadGrant:
    object_key: str
    upload_url: str
    expires_in: int


class ObjectStorage(Protocol):
    async def initiate_upload(
        self, *, workspace_id: UUID, source_id: UUID, filename: str, content_type: str
    ) -> UploadGrant: ...

    async def head(self, object_key: str) -> dict[str, object]: ...

    async def get_bytes(self, object_key: str, *, max_bytes: int) -> bytes: ...

    async def put_bytes(self, object_key: str, content: bytes, *, content_type: str) -> None: ...

    async def delete(self, object_key: str) -> None: ...


class S3ObjectStorage:
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

    def _client(self):  # type intentionally supplied by aioboto3 at runtime
        return self.session.client(
            "s3",
            endpoint_url=self.endpoint_url,
            region_name=self.region,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
        )

    async def initiate_upload(
        self, *, workspace_id: UUID, source_id: UUID, filename: str, content_type: str
    ) -> UploadGrant:
        safe_name = _SAFE_FILENAME.sub("_", filename).strip("._") or "upload.bin"
        object_key = f"workspaces/{workspace_id}/knowledge/{source_id}/{uuid4().hex}-{safe_name}"
        async with self._client() as client:
            upload_url = await client.generate_presigned_url(
                "put_object",
                Params={"Bucket": self.bucket, "Key": object_key, "ContentType": content_type},
                ExpiresIn=self.presign_ttl,
            )
        return UploadGrant(object_key, upload_url, self.presign_ttl)

    async def head(self, object_key: str) -> dict[str, object]:
        async with self._client() as client:
            return await client.head_object(Bucket=self.bucket, Key=object_key)

    async def get_bytes(self, object_key: str, *, max_bytes: int) -> bytes:
        async with self._client() as client:
            response = await client.get_object(Bucket=self.bucket, Key=object_key)
            declared_size = int(response.get("ContentLength", -1))
            if declared_size < 0 or declared_size > max_bytes:
                raise AppError("FILE_SIZE_INVALID", "파일 크기가 허용 범위를 벗어났습니다.", 422)
            body = response["Body"]
            try:
                content = await body.read(max_bytes + 1)
            finally:
                body.close()
            if len(content) > max_bytes or len(content) != declared_size:
                raise AppError("FILE_SIZE_INVALID", "파일 크기가 허용 범위를 벗어났습니다.", 422)
            return bytes(content)

    async def put_bytes(self, object_key: str, content: bytes, *, content_type: str) -> None:
        async with self._client() as client:
            await client.put_object(
                Bucket=self.bucket,
                Key=object_key,
                Body=content,
                ContentType=content_type,
            )

    async def delete(self, object_key: str) -> None:
        async with self._client() as client:
            await client.delete_object(Bucket=self.bucket, Key=object_key)


@lru_cache(maxsize=1)
def get_object_storage() -> ObjectStorage:
    settings = get_settings()
    if settings.s3_access_key_id is None or settings.s3_secret_access_key is None:
        raise AppError(
            "OBJECT_STORAGE_NOT_CONFIGURED",
            "파일 저장소가 구성되지 않았습니다.",
            503,
        )
    return S3ObjectStorage(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        bucket=settings.s3_bucket,
        access_key_id=settings.s3_access_key_id.get_secret_value(),
        secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        presign_ttl=settings.knowledge_presign_ttl_seconds,
    )
