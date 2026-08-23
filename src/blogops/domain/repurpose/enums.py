"""Stable repurposing vocabulary from REP-001 through REP-020."""

from enum import StrEnum


class RepurposeKind(StrEnum):
    INSTAGRAM_CAPTION = "INSTAGRAM_CAPTION"
    THREADS_X = "THREADS_X"
    LINKEDIN = "LINKEDIN"
    FACEBOOK = "FACEBOOK"
    NEWSLETTER = "NEWSLETTER"
    AD_COPY = "AD_COPY"
    SEARCH_AD_COPY = "SEARCH_AD_COPY"
    SHORT_VIDEO_SCRIPT = "SHORT_VIDEO_SCRIPT"
    YOUTUBE_DESCRIPTION = "YOUTUBE_DESCRIPTION"
    CARD_NEWS_COPY = "CARD_NEWS_COPY"
    REVIEW_RESPONSE = "REVIEW_RESPONSE"
    FAQ = "FAQ"
    SUPPORT_SCRIPT = "SUPPORT_SCRIPT"
    PRESS_RELEASE_SUMMARY = "PRESS_RELEASE_SUMMARY"


class RepurposeJobOperation(StrEnum):
    SINGLE = "SINGLE"
    BULK = "BULK"


class ChannelTemplateStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    DEPRECATED = "DEPRECATED"


class RepurposeApprovalDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_CHANGES = "REQUEST_CHANGES"


class RepurposeExportFormat(StrEnum):
    CSV = "CSV"
    TXT = "TXT"
    MARKDOWN = "MARKDOWN"


class RepurposeCommandKind(StrEnum):
    CANCEL = "CANCEL"
    RETRY = "RETRY"


class DeliveryState(StrEnum):
    REQUESTED = "REQUESTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
