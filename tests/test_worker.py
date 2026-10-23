from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.jobs.constants import AttemptStatus, JobPriority, JobStatus
from apps.jobs.models import Job, JobAttempt
from apps.worker.engine import WorkerEngine, WorkerOptions


@pytest.mark.django_db
def test_claim_next_job_success():
    """Verifies that an eligible PENDING job is claimed and its lease fields are set."""
    job = Job.objects.create(job_type="reports.generate")

    claimed_job = WorkerEngine.claim_next_job(
        worker_id="worker-test-1",
        lease_duration_seconds=120,
    )

    assert claimed_job is not None
    assert claimed_job.id == job.id
    assert claimed_job.status == JobStatus.CLAIMED
    assert claimed_job.claimed_by == "worker-test-1"
    assert claimed_job.lease_expires_at is not None


@pytest.mark.django_db
def test_claim_respects_priority_ordering():
    """Verifies that higher priority jobs are claimed before lower priority jobs."""
    low_job = Job.objects.create(
        job_type="email.send",
        priority=JobPriority.LOW,
    )
    high_job = Job.objects.create(
        job_type="email.send",
        priority=JobPriority.CRITICAL,
    )

    claimed_first = WorkerEngine.claim_next_job(worker_id="w1")
    assert claimed_first.id == high_job.id

    claimed_second = WorkerEngine.claim_next_job(worker_id="w1")
    assert claimed_second.id == low_job.id


@pytest.mark.django_db
def test_claim_ignores_future_scheduled_jobs():
    """Verifies that jobs scheduled in the future are not claimed."""
    future_time = timezone.now() + timedelta(hours=2)
    Job.objects.create(
        job_type="maintenance.cleanup",
        scheduled_at=future_time,
    )

    claimed = WorkerEngine.claim_next_job(worker_id="w1")
    assert claimed is None


@pytest.mark.django_db
def test_execute_job_completes_and_creates_attempt():
    """Verifies that executing a job transitions it to COMPLETED and creates a JobAttempt."""
    job = Job.objects.create(job_type="reports.generate", payload={"foo": "bar"})
    job.transition_to(JobStatus.CLAIMED)
    job.save()

    engine = WorkerEngine(options=WorkerOptions(worker_id="test-worker"))
    success = engine.execute_job(job)

    assert success is True
    job.refresh_from_db()
    assert job.status == JobStatus.COMPLETED
    assert job.result is not None
    assert job.completed_at is not None
    assert job.attempt_count == 1

    # Verify attempt audit row
    attempt = JobAttempt.objects.filter(job=job).first()
    assert attempt is not None
    assert attempt.attempt_number == 1
    assert attempt.status == AttemptStatus.SUCCESS
    assert attempt.duration_ms is not None


@pytest.mark.django_db
def test_worker_burst_mode():
    """Verifies that a worker in burst mode processes all ready jobs and exits."""
    Job.objects.create(job_type="email.send")
    Job.objects.create(job_type="reports.generate")

    engine = WorkerEngine(options=WorkerOptions(worker_id="burst-worker", burst=True))
    total_processed = engine.run()

    assert total_processed == 2
    assert Job.objects.filter(status=JobStatus.COMPLETED).count() == 2


@pytest.mark.django_db(transaction=True)
def test_concurrent_claiming_prevents_duplicate_claims():
    """
    Stress test: spawns 3 concurrent threads claiming 3 pending jobs simultaneously.
    Verifies that SELECT FOR UPDATE SKIP LOCKED guarantees each job is claimed by
    exactly one worker with zero duplicate claims.
    """
    # Create 3 distinct jobs
    job_ids = [Job.objects.create(job_type="email.send").id for _ in range(3)]

    claimed_results = []

    def claim_task(worker_name: str):
        from django.db import connections

        try:
            connections.close_all()
            job = WorkerEngine.claim_next_job(worker_id=worker_name)
            if job:
                claimed_results.append((worker_name, job.id))
        finally:
            # Explicitly close this thread's connection when finished
            connections.close_all()

    # Run 3 workers concurrently across threads
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(claim_task, f"concurrent-worker-{i}") for i in range(3)
        ]
        for f in futures:
            f.result()

    # Verify each job was claimed exactly once
    claimed_job_ids = [job_id for _, job_id in claimed_results]
    assert len(claimed_job_ids) == 3
    assert set(claimed_job_ids) == set(job_ids)
