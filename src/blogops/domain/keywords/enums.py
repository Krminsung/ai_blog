"""Stable keyword intelligence vocabulary."""

from enum import StrEnum


class ProviderKind(StrEnum):
    NAVER_DATALAB = "NAVER_DATALAB"
    NAVER_SEARCH_ADS = "NAVER_SEARCH_ADS"
    NAVER_BLOG_SEARCH = "NAVER_BLOG_SEARCH"
    NAVER_SHOPPING_INSIGHT = "NAVER_SHOPPING_INSIGHT"
    GOOGLE_SEARCH_CONSOLE = "GOOGLE_SEARCH_CONSOLE"
    GOOGLE_TRENDS_LICENSED = "GOOGLE_TRENDS_LICENSED"
    CONTRACT_DATA = "CONTRACT_DATA"
    USER_CSV = "USER_CSV"


class ProviderSourceClass(StrEnum):
    OFFICIAL = "OFFICIAL"
    LICENSED = "LICENSED"
    USER_PROVIDED = "USER_PROVIDED"


class ProviderCapability(StrEnum):
    RELATED_KEYWORDS = "RELATED_KEYWORDS"
    SEARCH_DEMAND = "SEARCH_DEMAND"
    TREND = "TREND"
    DEMOGRAPHICS = "DEMOGRAPHICS"
    REGION = "REGION"
    CPC = "CPC"
    COMPETITION = "COMPETITION"
    BLOG_RESULTS = "BLOG_RESULTS"
    SHOPPING_TREND = "SHOPPING_TREND"
    SITE_PERFORMANCE = "SITE_PERFORMANCE"
    LICENSED_SERP = "LICENSED_SERP"
    REALTIME = "REALTIME"


class CredentialOwner(StrEnum):
    CUSTOMER = "CUSTOMER"
    SERVICE = "SERVICE"


class ProviderConnectionState(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    CREDENTIAL_EXPIRED = "CREDENTIAL_EXPIRED"
    LICENSE_EXPIRED = "LICENSE_EXPIRED"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    ERROR = "ERROR"


class MetricValueKind(StrEnum):
    ABSOLUTE = "ABSOLUTE"
    RELATIVE = "RELATIVE"
    ESTIMATED = "ESTIMATED"
    USER_PROVIDED = "USER_PROVIDED"


class KeywordIntent(StrEnum):
    INFORMATIONAL = "INFORMATIONAL"
    COMPARISON = "COMPARISON"
    PURCHASE = "PURCHASE"
    LOCAL = "LOCAL"
    NAVIGATIONAL = "NAVIGATIONAL"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class IntentSource(StrEnum):
    RULE = "RULE"
    PROVIDER_SERP = "PROVIDER_SERP"
    MODEL = "MODEL"
    USER = "USER"


class ResearchInputKind(StrEnum):
    SEED = "SEED"
    COMPETITOR = "COMPETITOR"
    CSV = "CSV"
    PASTE = "PASTE"
    REFRESH = "REFRESH"


class ResearchJobState(StrEnum):
    QUEUED = "QUEUED"
    VALIDATING = "VALIDATING"
    RESEARCHING = "RESEARCHING"
    PARTIAL = "PARTIAL"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    FINAL_FAILED = "FINAL_FAILED"
    SUCCEEDED = "SUCCEEDED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"


class ResearchItemState(StrEnum):
    PENDING = "PENDING"
    EXCLUDED = "EXCLUDED"
    BLOCKED = "BLOCKED"
    RUNNING = "RUNNING"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    RETRYING = "RETRYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProviderCallState(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    FINAL_FAILED = "FINAL_FAILED"
    CACHE_HIT = "CACHE_HIT"
    BLOCKED = "BLOCKED"


class ClusterKind(StrEnum):
    KEYWORD = "KEYWORD"
    QUESTION = "QUESTION"
    SERP_INTENT = "SERP_INTENT"


class ClusterMethod(StrEnum):
    EXACT = "EXACT"
    SEMANTIC = "SEMANTIC"
    SEMANTIC_INTENT = "SEMANTIC_INTENT"
    SEMANTIC_SERP_INTENT = "SEMANTIC_SERP_INTENT"


class ClusterDecisionState(StrEnum):
    PROPOSED = "PROPOSED"
    MERGED = "MERGED"
    SPLIT = "SPLIT"
    ACCEPTED = "ACCEPTED"


class TrendDirection(StrEnum):
    SURGING = "SURGING"
    RISING = "RISING"
    STABLE = "STABLE"
    FALLING = "FALLING"
    VOLATILE = "VOLATILE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class CollectionKind(StrEnum):
    FAVORITES = "FAVORITES"
    PROJECT = "PROJECT"
    CAMPAIGN = "CAMPAIGN"


class ContentLinkTarget(StrEnum):
    URL = "URL"
    CONTENT_ITEM = "CONTENT_ITEM"


class ContentRecommendation(StrEnum):
    NEW = "NEW"
    UPDATE = "UPDATE"
    MERGE = "MERGE"
    SPLIT = "SPLIT"
    INTERNAL_LINK = "INTERNAL_LINK"


class AlertKind(StrEnum):
    SURGE = "SURGE"
    DECLINE = "DECLINE"
    COMPETITION = "COMPETITION"
    SEASONAL = "SEASONAL"


TERMINAL_JOB_STATES = frozenset(
    {
        ResearchJobState.SUCCEEDED,
        ResearchJobState.FINAL_FAILED,
        ResearchJobState.CANCELLED,
    }
)


ALLOWED_JOB_TRANSITIONS: dict[ResearchJobState, frozenset[ResearchJobState]] = {
    ResearchJobState.QUEUED: frozenset(
        {ResearchJobState.VALIDATING, ResearchJobState.CANCEL_REQUESTED}
    ),
    ResearchJobState.VALIDATING: frozenset(
        {
            ResearchJobState.RESEARCHING,
            ResearchJobState.FINAL_FAILED,
            ResearchJobState.CANCEL_REQUESTED,
        }
    ),
    ResearchJobState.RESEARCHING: frozenset(
        {
            ResearchJobState.PARTIAL,
            ResearchJobState.RETRYABLE_FAILED,
            ResearchJobState.FINAL_FAILED,
            ResearchJobState.SUCCEEDED,
            ResearchJobState.CANCEL_REQUESTED,
        }
    ),
    ResearchJobState.PARTIAL: frozenset(
        {ResearchJobState.QUEUED, ResearchJobState.SUCCEEDED, ResearchJobState.FINAL_FAILED}
    ),
    ResearchJobState.RETRYABLE_FAILED: frozenset(
        {ResearchJobState.QUEUED, ResearchJobState.FINAL_FAILED, ResearchJobState.CANCEL_REQUESTED}
    ),
    ResearchJobState.CANCEL_REQUESTED: frozenset({ResearchJobState.CANCELLED}),
    ResearchJobState.FINAL_FAILED: frozenset(),
    ResearchJobState.SUCCEEDED: frozenset(),
    ResearchJobState.CANCELLED: frozenset(),
}
