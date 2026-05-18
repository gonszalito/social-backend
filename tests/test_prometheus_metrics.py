"""Tests for API-M1-06 Prometheus histogram and /metrics endpoint."""
import pytest


def test_metrics_endpoint_returns_200(client):
    """GET /metrics returns 200 and valid Prometheus text format."""
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")


def test_metrics_shows_histogram_bucket_count_sum(client):
    """Pass criteria: curl /metrics shows _bucket, _count, and _sum."""
    # Generate a recorded request (excludes /metrics and /health)
    client.get("/docs")
    response = client.get("/metrics")
    body = response.text
    assert "_bucket" in body, "Expected request_latency_seconds_bucket in metrics"
    assert "_count" in body, "Expected request_latency_seconds_count in metrics"
    assert "_sum" in body, "Expected request_latency_seconds_sum in metrics"


def test_metrics_excludes_health_and_metrics_from_histogram(client):
    """Hitting only /health and /metrics should not create histogram entries."""
    client.get("/health")
    client.get("/metrics")
    body = client.get("/metrics").text
    # request_latency_seconds should have no samples (or only from prometheus_client internals)
    # The histogram we define won't have any obs if we only hit excluded paths
    lines = [l for l in body.split("\n") if "request_latency_seconds" in l and not l.startswith("#")]
    # With only /health and /metrics, our middleware records nothing - so no request_latency_seconds
    # lines from our histogram (except possibly the TYPE/HELP). Actually we might get default
    # prometheus_client metrics. Let me check - our REQUEST_LATENCY only gets observations
    # when we hit non-excluded paths. So after only /health and /metrics, there should be
    # no request_latency_seconds with our labels. The prometheus default registry might
    # have process_* and python_* metrics. Our histogram exists but has no observations.
    # generate_latest() will still output the histogram TYPE and HELP, but no time series
    # if there are no observations. Actually with prometheus_client, a Histogram with
    # no observations still gets the +Inf bucket and count/sum at 0 when you first
    # use it. We haven't used it. So there might be no output for our metric at all.
    # Let me simplify: just verify that after hitting /health and /metrics, the
    # request_latency_seconds entries (if any) don't have endpoint="/health" or
    # endpoint="/metrics".
    for line in lines:
        if "endpoint=" in line:
            assert 'endpoint="/health"' not in line, "Health should be excluded from histogram"
            assert 'endpoint="/metrics"' not in line, "Metrics should be excluded from histogram"


def test_metrics_histogram_has_required_labels(client):
    """Histogram series include method, endpoint, status_code labels."""
    client.get("/docs")
    body = client.get("/metrics").text
    assert 'method="GET"' in body
    assert 'endpoint="/docs"' in body
    assert 'status_code="200"' in body
