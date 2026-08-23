"""Stable identity and organization state vocabularies."""

from enum import StrEnum


class UserStatus(StrEnum):
    PENDING_EMAIL = "PENDING_EMAIL"
    ACTIVE = "ACTIVE"
    DORMANT = "DORMANT"
    DISABLED = "DISABLED"
    DELETION_PENDING = "DELETION_PENDING"
    DELETED = "DELETED"


class CredentialKind(StrEnum):
    PASSWORD = "PASSWORD"


class OneTimeTokenPurpose(StrEnum):
    EMAIL_VERIFICATION = "EMAIL_VERIFICATION"
    PASSWORD_RESET = "PASSWORD_RESET"


class ChallengePurpose(StrEnum):
    MFA_LOGIN = "MFA_LOGIN"
    MFA_DISABLE = "MFA_DISABLE"
    OWNERSHIP_TRANSFER = "OWNERSHIP_TRANSFER"


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    COMPROMISED = "COMPROMISED"
    EXPIRED = "EXPIRED"


class MFAFactorKind(StrEnum):
    TOTP = "TOTP"


class MFAFactorStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class WorkspaceStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DELETION_SCHEDULED = "DELETION_SCHEDULED"
    DELETED = "DELETED"


class MembershipStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REMOVED = "REMOVED"


class InvitationStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class ConnectionStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class FederationProtocol(StrEnum):
    OAUTH2 = "OAUTH2"
    OIDC = "OIDC"
    SAML2 = "SAML2"


class SCIMResourceType(StrEnum):
    USER = "USER"
    GROUP = "GROUP"


class AgencyClientStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REMOVED = "REMOVED"
