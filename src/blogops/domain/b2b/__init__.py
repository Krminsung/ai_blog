"""Agency and client-portal domain."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blogops.domain.b2b.service import B2BService

__all__ = ["B2BService"]


def __getattr__(name: str) -> object:
    """Load the service lazily so model discovery cannot recurse into services."""

    if name == "B2BService":
        from blogops.domain.b2b.service import B2BService

        return B2BService
    raise AttributeError(name)
