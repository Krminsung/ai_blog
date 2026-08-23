"""Low-cardinality Prometheus metrics."""

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter(
    "blogops_http_requests_total",
    "HTTP requests processed by the API",
    ("method", "route", "status"),
)
HTTP_REQUEST_DURATION = Histogram(
    "blogops_http_request_duration_seconds",
    "HTTP request duration",
    ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)
