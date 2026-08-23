"""Platform administration and notifications domain."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blogops.domain.admin.service import AdminService

__all__ = ["AdminService"]


def __getattr__(name: str) -> object:
    """Load the service lazily so model discovery cannot recurse into services."""

    if name == "AdminService":
        from blogops.domain.admin.service import AdminService

        return AdminService
    raise AttributeError(name)
