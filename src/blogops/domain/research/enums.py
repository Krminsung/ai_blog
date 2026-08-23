"""Research and evidence vocabulary."""

from enum import StrEnum


class ResearchArtifactKind(StrEnum):
    WEB_RESULT = "WEB_RESULT"
    KNOWLEDGE_SOURCE = "KNOWLEDGE_SOURCE"
    USER_FACT = "USER_FACT"
    FILE = "FILE"
    TRANSCRIPT = "TRANSCRIPT"
    FEED_ITEM = "FEED_ITEM"


class SourceQualityGrade(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class SourceSelection(StrEnum):
    AUTO_SELECTED = "AUTO_SELECTED"
    USER_SELECTED = "USER_SELECTED"
    EXCLUDED = "EXCLUDED"


class ClaimKind(StrEnum):
    FACT = "FACT"
    NUMBER = "NUMBER"
    DATE = "DATE"
    PRICE = "PRICE"
    POLICY = "POLICY"
    EXPERIENCE = "EXPERIENCE"
    OPINION = "OPINION"


class ClaimStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    USER_VERIFIED = "USER_VERIFIED"
    CONFLICTED = "CONFLICTED"
    UNSUPPORTED = "UNSUPPORTED"
    NEEDS_REVALIDATION = "NEEDS_REVALIDATION"


class CitationStyle(StrEnum):
    LINK = "LINK"
    FOOTNOTE = "FOOTNOTE"
    INLINE = "INLINE"


class ResearchDecisionKind(StrEnum):
    SELECT_SOURCE = "SELECT_SOURCE"
    EXCLUDE_SOURCE = "EXCLUDE_SOURCE"
    REPLACE_SOURCE = "REPLACE_SOURCE"
    VERIFY_USER_FACT = "VERIFY_USER_FACT"
    REMOVE_UNSUPPORTED_CLAIM = "REMOVE_UNSUPPORTED_CLAIM"
    APPROVE_SOURCE_SET = "APPROVE_SOURCE_SET"
