"""Public API and webhook domain."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blogops.domain.developer.service import DeveloperService

__all__ = ["DeveloperService"]


def __getattr__(name: str) -> object:
    """Load the service lazily so model discovery cannot recurse into services."""

    if name == "DeveloperService":
        from blogops.domain.developer.service import DeveloperService

        return DeveloperService
    raise AttributeError(name)
