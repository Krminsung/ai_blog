"""Console entry points."""

import uvicorn

from blogops.core.config import get_settings


def run_api() -> None:
    """Start the API using the configured bind address."""
    settings = get_settings()
    uvicorn.run(
        "blogops.main:app",
        host=settings.api_host,
        port=settings.api_port,
        proxy_headers=settings.trust_proxy_headers,
    )
