import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.worker.engine import WorkerEngine, WorkerOptions


@pytest.mark.django_db
def test_correlation_id_generated_when_missing(api_client: APIClient):
    """
    Verifies that when a client does not send X-Correlation-ID,
    CorrelationIdMiddleware generates a UUID and returns it in the response header.
    """
    url = reverse("health-live")
    response = api_client.get(url)

    assert response.status_code == status.HTTP_200_OK
    assert "X-Correlation-ID" in response.headers
    assert len(response.headers["X-Correlation-ID"]) == 36


@pytest.mark.django_db
def test_correlation_id_preserved_when_provided(api_client: APIClient):
    """
    Verifies that when a client sends a custom X-Correlation-ID header,
    the exact same ID is echoed back in the response.
    """
    url = reverse("health-live")
    custom_trace_id = "trace-req-99887766"
    response = api_client.get(url, HTTP_X_CORRELATION_ID=custom_trace_id)

    assert response.status_code == status.HTTP_200_OK
    assert response.headers["X-Correlation-ID"] == custom_trace_id


@pytest.mark.django_db
def test_metrics_reports_active_workers_and_attempt_stats(api_client: APIClient):
    """
    Verifies that after a worker broadcasts a heartbeat, the metrics endpoint
    reports active_workers >= 1 and includes attempt_metrics.
    """
    engine = WorkerEngine(options=WorkerOptions(worker_id="observable-worker-01"))
    engine.send_heartbeat()

    url = reverse("job-metrics")
    response = api_client.get(url)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["active_workers"] >= 1
    assert "attempt_metrics" in data
    assert "avg_duration_ms" in data["attempt_metrics"]


@pytest.mark.django_db
def test_openapi_schema_and_swagger_ui_endpoints(api_client: APIClient):
    """
    Verifies that /api/schema/ returns a valid OpenAPI 3.0 specification
    and /api/docs/ renders the interactive Swagger UI.
    """
    schema_url = reverse("schema")
    schema_res = api_client.get(schema_url)
    assert schema_res.status_code == status.HTTP_200_OK
    assert b"Background Job Processing Platform API" in schema_res.content

    docs_url = reverse("swagger-ui")
    docs_res = api_client.get(docs_url)
    assert docs_res.status_code == status.HTTP_200_OK
    assert b"swagger-ui" in docs_res.content
    