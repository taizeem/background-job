"""
Locust HTTP load testing suite for the Background Job Processing Platform.
Run with: uv run locust -f loadtests/locustfile.py --host=http://127.0.0.1:8000
"""

import os
import uuid

from locust import HttpUser, between, task


class JobPlatformClient(HttpUser):
    """
    Simulates an external service submitting jobs and polling telemetry.
    Set LOCUST_API_KEY in your environment before running.
    """

    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        self.api_key = os.getenv("LOCUST_API_KEY", "")
        self.headers = {"X-API-Key": self.api_key}
        self.submitted_job_ids: list[str] = []

    @task(3)
    def submit_report_job(self) -> None:
        """Submits a report generation job (high frequency)."""
        payload = {
            "job_type": "reports.generate",
            "payload": {"year": 2026, "month": "October", "format": "json"},
            "priority": 20,
            "idempotency_key": f"locust-{uuid.uuid4()}",
        }
        with self.client.post(
            "/api/v1/jobs/",
            json=payload,
            headers=self.headers,
            catch_response=True,
        ) as response:
            if response.status_code in (200, 201):
                job_id = response.json().get("id")
                if job_id and len(self.submitted_job_ids) < 50:
                    self.submitted_job_ids.append(job_id)
                response.success()
            elif response.status_code == 429:
                # Expected when quota or rate limit triggers under heavy load
                response.success()
            else:
                response.failure(f"Unexpected status code: {response.status_code}")

    @task(2)
    def poll_job_status(self) -> None:
        """Polls the status of a previously submitted job."""
        if not self.submitted_job_ids:
            return
        job_id = self.submitted_job_ids[-1]
        self.client.get(
            f"/api/v1/jobs/{job_id}/",
            headers=self.headers,
            name="/api/v1/jobs/[id]/",
        )

    @task(1)
    def check_queue_metrics(self) -> None:
        """Queries the Redis-cached queue telemetry endpoint."""
        self.client.get("/api/v1/jobs/metrics/", headers=self.headers)