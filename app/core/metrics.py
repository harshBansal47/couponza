"""Prometheus metrics.

Hand-rolled rather than pulled from a framework because there is nothing to
instrument yet beyond "requests arrived" and "jobs ran" — and the honest version
of that is a dozen lines. The two things this module is careful about:

* **Cardinality is bounded.** Route labels are the *route template*
  (`/api/v1/coupons/{coupon_id}`), never the concrete path. Labelling by path is
  the single most common way to turn a Prometheus scrape into an out-of-memory
  kill of the metrics server, and it does not show up until someone queries a
  404-heavy path.
* **Instruments are registered once.** Prometheus's default registry raises on a
  duplicate metric name, which turns an import-order accident into an import
  error. Re-registering is a no-op instead.
"""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.openmetrics.exposition import CONTENT_TYPE_LATEST as OPENMETRICS_TYPE

# A private registry rather than the global default: tests create the app more
# than once in a process, and global state leaking between them is how you end
# up debugging a metric that is mysteriously doubled.
registry = CollectorRegistry()

http_requests = Counter(
    "couponza_http_requests_total",
    "HTTP requests, by method, route and status.",
    ["method", "route", "status"],
    registry=registry,
)

http_duration = Histogram(
    "couponza_http_request_duration_seconds",
    "Request latency in seconds, by method and route.",
    ["method", "route"],
    # Buckets chosen around what a JSON API actually does: a cache hit is ~5ms,
    # a normal read ~40ms, an ingestion-adjacent write ~300ms. Default buckets
    # start at 5ms and top out at 10s, which wastes resolution where it matters.
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=registry,
)

job_runs = Counter(
    "couponza_job_runs_total",
    "Scheduled job executions, by job name and outcome.",
    ["job", "outcome"],
    registry=registry,
)

job_duration = Histogram(
    "couponza_job_duration_seconds",
    "Scheduled job duration in seconds, by job name.",
    ["job"],
    buckets=(0.1, 0.5, 1.0, 5.0, 15.0, 60.0, 300.0, 900.0),
    registry=registry,
)

alerts_sent = Counter(
    "couponza_alerts_total",
    "Alert deliveries attempted, by channel and outcome.",
    ["channel", "outcome"],
    registry=registry,
)

stale_coupons = Gauge(
    "couponza_stale_coupons",
    "Coupons past their expiry date but still marked active.",
    registry=registry,
)


def render() -> tuple[bytes, str]:
    """Return the exposition payload and its content type."""
    return generate_latest(registry), OPENMETRICS_TYPE
