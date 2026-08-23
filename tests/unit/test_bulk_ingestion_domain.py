import hashlib
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from blogops.core.errors import AppError
from blogops.domain.bulk.enums import BulkInputKind
from blogops.domain.bulk.ingestion import (
    ExternalSnapshotRequest,
    iter_normalized_csv_rows,
    require_external_snapshot,
    verify_uploaded_bulk_snapshot,
)
from blogops.domain.bulk.schemas import BulkUploadComplete
from blogops.domain.knowledge.adapters import MalwareStatus


@dataclass(frozen=True)
class _ScanResult:
    status: MalwareStatus
    signature: str | None = None


class _Scanner:
    def __init__(self, status: MalwareStatus = MalwareStatus.CLEAN) -> None:
        self.status = status

    async def scan(self, content: bytes) -> _ScanResult:
        assert content
        return _ScanResult(self.status)


class _Storage:
    def __init__(self, *, content: bytes, content_type: str) -> None:
        self.content = content
        self.content_type = content_type
        self.deleted: list[str] = []
        self.promoted: list[str] = []

    async def head(self, object_ref: str) -> dict[str, object]:
        return {"ContentLength": len(self.content), "ContentType": self.content_type}

    async def get_bytes(self, object_ref: str, *, max_bytes: int) -> bytes:
        assert len(self.content) <= max_bytes
        return self.content

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
        assert namespace == "bulk"
        assert content == self.content
        assert content_type == self.content_type
        value = (
            f"workspaces/{workspace_id}/bulk/{owner_id}/versions/"
            f"{content_hash}.bin"
        )
        self.promoted.append(value)
        return value

    async def delete(self, object_ref: str) -> None:
        self.deleted.append(object_ref)


def _upload(
    workspace_id: UUID,
    upload_id: UUID,
    content: bytes,
    *,
    input_kind: BulkInputKind = BulkInputKind.CSV,
    content_type: str = "text/csv",
) -> BulkUploadComplete:
    return BulkUploadComplete(
        upload_id=upload_id,
        input_kind=input_kind,
        name="bulk.csv",
        object_ref=(
            f"workspaces/{workspace_id}/bulk/{upload_id}/quarantine/upload.csv"
        ),
        mime_type=content_type,
        size_bytes=len(content),
        expected_content_hash=hashlib.sha256(content).hexdigest(),
    )


@pytest.mark.asyncio
async def test_csv_upload_is_verified_scanned_promoted_and_materialized() -> None:
    workspace_id = uuid5(NAMESPACE_URL, "bulk-ingestion-workspace")
    upload_id = uuid5(NAMESPACE_URL, "bulk-ingestion-upload")
    content = "keyword,title\ncontact test@example.com, First \nsecond,Second\n".encode()
    storage = _Storage(content=content, content_type="text/csv")
    upload = _upload(workspace_id, upload_id, content)

    verified = await verify_uploaded_bulk_snapshot(
        workspace_id=workspace_id,
        upload=upload,
        storage=storage,
        scanner=_Scanner(),
        scanner_name="clamav",
        scanner_version="test",
        max_upload_bytes=1024,
    )

    assert verified.content_hash == hashlib.sha256(content).hexdigest()
    assert verified.row_count == 2
    assert verified.headers == ("keyword", "title")
    assert verified.malware_scan_status == "CLEAN"
    assert len(verified.malware_scan_result_hash) == 64
    assert storage.promoted == [verified.object_ref]
    assert storage.deleted == [upload.object_ref]

    first, second = iter_normalized_csv_rows(
        content,
        encoding=verified.encoding,
        delimiter=verified.delimiter,
        header_row=verified.header_row,
    )
    assert (first.row_no, second.row_no) == (1, 2)
    assert first.values == {"keyword": "contact [EMAIL]", "title": "First"}
    assert first.pii_masked is True
    assert first.input_hash != second.input_hash


@pytest.mark.asyncio
async def test_upload_cannot_escape_exact_workspace_upload_quarantine_prefix() -> None:
    workspace_id = uuid5(NAMESPACE_URL, "bulk-ingestion-workspace")
    upload_id = uuid5(NAMESPACE_URL, "bulk-ingestion-upload")
    content = b"keyword\nvalue\n"
    storage = _Storage(content=content, content_type="text/csv")
    upload = _upload(workspace_id, upload_id, content).model_copy(
        update={
            "object_ref": (
                f"workspaces/{workspace_id}/bulk/"
                f"{uuid5(NAMESPACE_URL, 'other-upload')}/quarantine/upload.csv"
            )
        }
    )

    with pytest.raises(AppError) as error:
        await verify_uploaded_bulk_snapshot(
            workspace_id=workspace_id,
            upload=upload,
            storage=storage,
            scanner=_Scanner(),
            scanner_name="clamav",
            scanner_version="test",
            max_upload_bytes=1024,
        )
    assert error.value.code == "BULK_QUARANTINE_OBJECT_INVALID"
    assert storage.promoted == []
    assert storage.deleted == []


@pytest.mark.asyncio
async def test_non_clean_scan_and_unconfigured_xlsx_parser_fail_closed() -> None:
    workspace_id = uuid5(NAMESPACE_URL, "bulk-ingestion-workspace")
    upload_id = uuid5(NAMESPACE_URL, "bulk-ingestion-upload")
    csv_content = b"keyword\nvalue\n"
    csv_storage = _Storage(content=csv_content, content_type="text/csv")
    with pytest.raises(AppError) as malware_error:
        await verify_uploaded_bulk_snapshot(
            workspace_id=workspace_id,
            upload=_upload(workspace_id, upload_id, csv_content),
            storage=csv_storage,
            scanner=_Scanner(MalwareStatus.UNAVAILABLE),
            scanner_name="clamav",
            scanner_version="test",
            max_upload_bytes=1024,
        )
    assert malware_error.value.code == "BULK_SCANNER_UNAVAILABLE"
    assert csv_storage.promoted == []
    assert csv_storage.deleted == []

    infected_storage = _Storage(content=csv_content, content_type="text/csv")
    with pytest.raises(AppError) as infected_error:
        await verify_uploaded_bulk_snapshot(
            workspace_id=workspace_id,
            upload=_upload(workspace_id, upload_id, csv_content),
            storage=infected_storage,
            scanner=_Scanner(MalwareStatus.INFECTED),
            scanner_name="clamav",
            scanner_version="test",
            max_upload_bytes=1024,
        )
    assert infected_error.value.code == "BULK_MALWARE_DETECTED"
    assert infected_storage.promoted == []
    assert infected_storage.deleted == [
        _upload(workspace_id, upload_id, csv_content).object_ref
    ]

    xlsx_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    xlsx_content = b"PK\x03\x04placeholder"
    xlsx_storage = _Storage(content=xlsx_content, content_type=xlsx_type)
    with pytest.raises(AppError) as parser_error:
        await verify_uploaded_bulk_snapshot(
            workspace_id=workspace_id,
            upload=_upload(
                workspace_id,
                upload_id,
                xlsx_content,
                input_kind=BulkInputKind.XLSX,
                content_type=xlsx_type,
            ),
            storage=xlsx_storage,
            scanner=_Scanner(),
            scanner_name="clamav",
            scanner_version="test",
            max_upload_bytes=1024,
        )
    assert parser_error.value.code == "BULK_XLSX_PARSER_UNAVAILABLE"
    assert xlsx_storage.promoted == []
    assert xlsx_storage.deleted == []


@pytest.mark.asyncio
async def test_external_connector_requires_configured_adapter_and_secret_reference() -> None:
    request = ExternalSnapshotRequest(
        input_kind=BulkInputKind.GOOGLE_SHEETS,
        source_locator="spreadsheet-id#Sheet1!A:Z",
        source_connection_ref="connection://google/customer",
        source_secret_ref="secret-manager://google/customer",
    )
    with pytest.raises(AppError) as error:
        await require_external_snapshot(request, adapter=None)
    assert error.value.code == "BULK_INPUT_ADAPTER_UNAVAILABLE"

    with pytest.raises(AppError) as secret_error:
        ExternalSnapshotRequest(
            input_kind=BulkInputKind.GOOGLE_SHEETS,
            source_locator="spreadsheet-id#Sheet1!A:Z",
            source_connection_ref=None,
            source_secret_ref="plaintext-password",
        )
    assert secret_error.value.code == "BULK_SECRET_REFERENCE_REQUIRED"
