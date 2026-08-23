"""Focused contracts for safe, durable official-API publishing and manual Naver handoff."""

from datetime import UTC, datetime
import pytest
from pydantic import ValidationError
from sqlalchemy import event

from blogops.api.v1.publishing import router
from blogops.core.errors import AppError
from blogops.domain.jobs.state import JobState
from blogops.domain.publishing.adapters import (
    BLOGGER_API_BASE,
    BLOGGER_SCOPE,
    GHOST_IMAGES_PATH,
    GHOST_POSTS_PATH,
    WORDPRESS_MEDIA_PATH,
    WORDPRESS_POSTS_PATH,
    _wordpress_payload,
    official_provider_registry,
)
from blogops.domain.publishing.enums import PublishingProvider, PublishVisibility
from blogops.domain.publishing.models import (
    NaverPublishPackage,
    PublishedMediaBinding,
    PublishedPost,
    PublishingConnection,
    PublishJob,
    _reject_immutable_publishing_row,
)
from blogops.domain.publishing.providers import (
    ProviderRegistry,
    PublishDocument,
    SecretMaterial,
)
from blogops.domain.publishing.rendering import render_for_cms
from blogops.domain.publishing.rules import (
    NAVER_MANUAL_POLICY_NOTICE,
    NAVER_MANUAL_POLICY_VERSION,
    RetryClass,
    classify_retry,
    redact_metadata,
    validate_naver_post,
    validate_schedule,
)
from blogops.domain.publishing.schemas import (
    PublishingConnectionCreate,
    PublishingConnectionRead,
)
from blogops.domain.publishing.security import validate_secret_ref, validate_site_url


def test_publish_parent_uses_authoritative_common_job_state() -> None:
    assert PublishJob.__table__.columns["state"].default.arg == JobState.QUEUED.value
    assert set(JobState).issuperset(
        {
            JobState.SCHEDULED,
            JobState.PUBLISHING,
            JobState.SUCCEEDED,
            JobState.PARTIAL,
            JobState.RETRYABLE_FAILED,
            JobState.FINAL_FAILED,
            JobState.CANCEL_REQUESTED,
            JobState.CANCELLED,
        }
    )


def test_idempotency_identity_and_request_hash_are_durable() -> None:
    columns = PublishJob.__table__.columns.keys()
    assert {
        "workspace_id",
        "requested_by",
        "operation",
        "idempotency_key",
        "request_hash",
        "idempotency_marker",
    }.issubset(columns)
    identities = {
        tuple(constraint.columns.keys())
        for constraint in PublishJob.__table__.constraints
        if getattr(constraint, "name", None) == "publish_job_idempotency"
    }
    assert identities == {
        ("workspace_id", "requested_by", "operation", "idempotency_key")
    }


def test_credentials_are_only_secret_refs_and_never_response_fields() -> None:
    assert "credential_secret_ref" in PublishingConnection.__table__.columns
    assert "credential_secret_ref" not in PublishingConnectionRead.model_fields
    assert "credential" not in PublishingConnectionRead.model_fields
    assert validate_secret_ref("aws-sm://prod/blogops/wordpress")
    with pytest.raises(AppError):
        validate_secret_ref("plain-application-password")
    material = SecretMaterial({"access_token": "do-not-show"})
    assert "do-not-show" not in repr(material)
    assert "REDACTED" in repr(material)


def test_provider_registry_fails_closed_without_configured_adapter() -> None:
    with pytest.raises(AppError) as raised:
        ProviderRegistry().require(PublishingProvider.WORDPRESS, "wordpress-rest-v2")
    assert raised.value.code == "PUBLISH_PROVIDER_UNAVAILABLE"


def test_builtin_registry_contains_only_supported_official_cms_contracts() -> None:
    registry = official_provider_registry()
    assert registry.require(
        PublishingProvider.WORDPRESS, "wordpress-rest-v2"
    ).official_contract == "wordpress-rest-v2"
    assert registry.require(
        PublishingProvider.GHOST, "ghost-admin-api"
    ).official_contract == "ghost-admin-api"
    assert registry.require(
        PublishingProvider.BLOGGER, "google-blogger-v3"
    ).official_contract == "google-blogger-v3"
    with pytest.raises(AppError):
        registry.require(PublishingProvider.CUSTOMER_CMS, "unreviewed")


def test_customer_cms_connection_rejects_dynamic_transport_templates() -> None:
    with pytest.raises(ValidationError):
        PublishingConnectionCreate(
            provider=PublishingProvider.CUSTOMER_CMS,
            name="Approved CMS",
            site_url="https://cms.example.com",
            site_timezone="UTC",
            official_contract="approved-cms-v1",
            api_version="v1",
            credential_secret_ref="vault://publishing/customer-cms",
            safe_config={"endpoint_template": "https://127.0.0.1/{path}"},
        )


def test_site_url_blocks_credentials_private_hosts_ports_and_non_https() -> None:
    assert validate_site_url("https://example.com/subsite").normalized == (
        "https://example.com/subsite"
    )
    for value in (
        "http://example.com",
        "https://user:password@example.com",
        "https://localhost",
        "https://127.0.0.1",
        "https://100.64.0.1",
        "https://example.com:8443",
        "https://example.com?endpoint=https://127.0.0.1",
    ):
        with pytest.raises(AppError):
            validate_site_url(value)


def test_retry_policy_is_only_network_429_or_5xx() -> None:
    assert classify_retry(network_error=True, status_code=None) is RetryClass.NETWORK
    assert classify_retry(network_error=False, status_code=429) is RetryClass.RATE_LIMIT
    assert classify_retry(network_error=False, status_code=503) is RetryClass.SERVER
    assert classify_retry(network_error=False, status_code=409) is RetryClass.FINAL
    assert classify_retry(network_error=False, status_code=401) is RetryClass.FINAL


def test_schedule_requires_exact_utc_local_timezone_and_dst_fold() -> None:
    valid = validate_schedule(
        scheduled_at_utc=datetime(2026, 1, 15, 15, 0, tzinfo=UTC),
        scheduled_local=datetime(2026, 1, 15, 10, 0),
        timezone_name="America/New_York",
        fold=None,
    )
    assert valid.scheduled_at_utc == datetime(2026, 1, 15, 15, 0, tzinfo=UTC)
    with pytest.raises(AppError) as ambiguous:
        validate_schedule(
            scheduled_at_utc=datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
            scheduled_local=datetime(2026, 11, 1, 1, 30),
            timezone_name="America/New_York",
            fold=None,
        )
    assert ambiguous.value.code == "PUBLISH_DST_FOLD_REQUIRED"
    with pytest.raises(AppError) as nonexistent:
        validate_schedule(
            scheduled_at_utc=datetime(2026, 3, 8, 7, 30, tzinfo=UTC),
            scheduled_local=datetime(2026, 3, 8, 2, 30),
            timezone_name="America/New_York",
            fold=0,
        )
    assert nonexistent.value.code == "PUBLISH_DST_NONEXISTENT_TIME"


def test_naver_is_immutable_manual_package_with_strict_confirmation_url() -> None:
    assert NAVER_MANUAL_POLICY_VERSION
    assert "직접 게시" in NAVER_MANUAL_POLICY_NOTICE
    assert validate_naver_post(
        "https://blog.naver.com/example/123456", "123456"
    ) == "https://blog.naver.com/example/123456"
    for value in (
        "http://blog.naver.com/example/123456",
        "https://m.blog.naver.com/example/123456",
        "https://blog.naver.com@example.com/example/123456",
        "https://blog.naver.com/../123456",
        "https://blog.naver.com/example/not-the-id",
        "https://blog.naver.com/example/extra/123456",
    ):
        with pytest.raises(AppError):
            validate_naver_post(value, "123456")
    assert event.contains(
        NaverPublishPackage, "before_update", _reject_immutable_publishing_row
    )


def test_renderer_escapes_content_and_reports_unsupported_blocks() -> None:
    rendered = render_for_cms(
        [
            {"id": "p1", "type": "paragraph", "text": "<script>alert(1)</script>"},
            {"id": "x1", "type": "vendor-widget", "text": "fallback"},
        ]
    )
    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html
    assert rendered.unsupported == [
        {"block_key": "x1", "type": "vendor-widget", "replacement": "paragraph"}
    ]
    tracked = render_for_cms(
        [
            {
                "id": "cta",
                "type": "cta",
                "payload": {"text": "Read", "url": "https://example.com/a?keep=1"},
            }
        ],
        tracking={"utm_source": "blogops"},
    )
    assert "keep=1&amp;utm_source=blogops" in tracked.html
    assert tracked.blocks[0]["original_url"] == "https://example.com/a?keep=1"
    credited = render_for_cms(
        [], attributions=["Photo: <Creator>", "Photo: <Creator>"]
    )
    assert "Photo: &lt;Creator&gt;" in credited.html
    assert credited.html.count("Photo: &lt;Creator&gt;") == 1


def test_step_metadata_redaction_removes_secret_like_values() -> None:
    redacted = redact_metadata(
        {
            "Authorization": "Bearer secret",
            "nested": {"password": "secret", "status": 201},
            "detail": "upstream said Bearer abc.def.ghi",
        }
    )
    assert redacted == {
        "Authorization": "[REDACTED]",
        "nested": {"password": "[REDACTED]", "status": 201},
        "detail": "upstream said [REDACTED]",
    }


def test_official_adapter_contract_paths_do_not_use_scraping() -> None:
    assert WORDPRESS_POSTS_PATH == "/wp-json/wp/v2/posts"
    assert WORDPRESS_MEDIA_PATH == "/wp-json/wp/v2/media"
    assert GHOST_POSTS_PATH == "/ghost/api/admin/posts/"
    assert GHOST_IMAGES_PATH == "/ghost/api/admin/images/upload/"
    assert BLOGGER_API_BASE == "https://www.googleapis.com"
    assert BLOGGER_SCOPE == "https://www.googleapis.com/auth/blogger"


def test_wordpress_payload_uses_safe_gutenberg_html_block_markup() -> None:
    payload = _wordpress_payload(
        PublishDocument(
            title="Title",
            html="<p>Body</p>",
            plain_text="Body",
            visibility=PublishVisibility.DRAFT,
            scheduled_at_utc=None,
            idempotency_marker="blogops-job",
            options={},
            media_urls={},
        ),
        creating=True,
    )
    assert "<!-- blogops:blogops-job -->" in payload["content"]
    assert "<!-- wp:html -->" in payload["content"]
    assert "<!-- /wp:html -->" in payload["content"]


def test_tenant_owned_content_references_are_composite_foreign_keys() -> None:
    for model in (PublishJob, PublishedPost, NaverPublishPackage):
        composite_targets = {
            tuple(element.target_fullname for element in constraint.elements)
            for constraint in model.__table__.foreign_key_constraints
            if len(constraint.elements) == 2
        }
        assert any(target[0].endswith(".workspace_id") for target in composite_targets)
    assert {"workspace_id", "content_version_id", "content_hash"}.issubset(
        PublishJob.__table__.columns.keys()
    )
    for model in (PublishJob, PublishedPost, NaverPublishPackage):
        exact_version_fks = {
            tuple(element.target_fullname for element in constraint.elements)
            for constraint in model.__table__.foreign_key_constraints
            if len(constraint.elements) == 3
        }
        assert (
            "content_versions.workspace_id",
            "content_versions.id",
            "content_versions.content_hash",
        ) in exact_version_fks
        exact_approval_fks = {
            tuple(element.target_fullname for element in constraint.elements)
            for constraint in model.__table__.foreign_key_constraints
            if len(constraint.elements) == 5
        }
        assert (
            "content_approval_requests.workspace_id",
            "content_approval_requests.id",
            "content_approval_requests.content_id",
            "content_approval_requests.content_version_id",
            "content_approval_requests.content_hash",
        ) in exact_approval_fks
    assert event.contains(
        PublishedMediaBinding, "before_delete", _reject_immutable_publishing_row
    )


def test_canonical_publishing_and_naver_routes_are_exposed() -> None:
    paths = {getattr(route, "path", "") for route in router.routes}
    assert {
        "/publishing/connections",
        "/publishing/connections/{connection_id}/diagnose",
        "/content/{content_id}/publish",
        "/content/{content_id}/publishing-preview",
        "/published-posts/{post_id}",
        "/content/{content_id}/naver-package",
        "/publishing/jobs/{job_id}",
        "/publishing/jobs/{job_id}/cancel",
        "/published-posts/{post_id}/reconcile",
    }.issubset(paths)
