"""Billing, credit and usage domain."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blogops.domain.billing.service import BillingService

__all__ = ["BillingService"]


def __getattr__(name: str) -> object:
    """Load the service lazily so model discovery cannot recurse into services."""

    if name == "BillingService":
        from blogops.domain.billing.service import BillingService

        return BillingService
    raise AttributeError(name)
