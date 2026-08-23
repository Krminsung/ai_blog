import pytest

from blogops.core.errors import AppError
from blogops.domain.knowledge.parsing import ExtractedBlock, mask_pii, semantic_chunks
from blogops.domain.knowledge.security import validate_resolved_addresses, validate_source_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.1/private",
        "http://localhost/",
        "ftp://example.com/file",
        "https://user:password@example.com/",
    ],
)
def test_private_or_credentialed_source_urls_are_rejected(url: str) -> None:
    with pytest.raises(AppError):
        validate_source_url(url)


def test_public_source_url_is_normalized_without_fragment() -> None:
    result = validate_source_url("HTTPS://Example.COM/path?q=1#private-fragment")
    assert result.normalized == "https://example.com/path?q=1"


def test_dns_resolution_requires_every_address_to_be_public() -> None:
    with pytest.raises(AppError):
        validate_resolved_addresses(["203.0.113.10", "127.0.0.1"])


def test_semantic_chunks_mask_pii_and_keep_locators() -> None:
    blocks = (
        ExtractedBlock("문의는 person@example.com 또는 010-1234-5678로 주세요.", {"page": 1}),
    )
    chunks = semantic_chunks(blocks)

    assert chunks[0].pii_masked is True
    assert "person@example.com" not in chunks[0].text
    assert chunks[0].locator["start"] == {"page": 1}


def test_direct_masking_reports_if_content_changed() -> None:
    masked, changed = mask_pii("일반 문장")
    assert masked == "일반 문장"
    assert changed is False
