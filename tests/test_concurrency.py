from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.db import connections
from django.utils import timezone

from apps.jobs.constants import AttemptStatus, JobStatus
from apps.jobs.models import Job, JobAttempt
from apps.worker.engine import WorkerEngine, WorkerOptions
from apps.worker.reaper import ReaperService


@pytest.mark.django_db
def test_reaper_recovers_crashed_worker_job_to_retrying():
    """
    Fault-injection test:
    1. Worker-1 claims a job and starts running it, then crashes (lease expires in the past).
    2. Reaper sweeps the database and transitions the zombie job to RETRYING.
    3. Worker-2 claims the recovered job and executes it to COMPLETED.
    """
    expired_time = timezone.now() - timedelta(minutes=2)
    job = Job.objects.create(
        job_type="reports.generate",
        payload={"year": 2026, "month": "December", "format": "json"},
        status=JobStatus.RUNNING,
        claimed_by="crashed-worker-01",
        lease_expires_at=expired_time,
        attempt_count=1,
        max_attempts=3,
    )

    # 1. Run Reaper
    reaped_count = ReaperService.reap_expired_leases()
    assert reaped_count == 1

    job.refresh_from_db()
    assert job.status == JobStatus.RETRYING
    assert job.claimed_by is None
    assert job.lease_expires_at is None
    assert "crashed-worker-01" in job.error_message

    # Verify attempt #1 was logged as ABANDONED
    attempt_1 = JobAttempt.objects.get(job=job, attempt_number=1)
    assert attempt_1.status == AttemptStatus.ABANDONED

    # 2. Healthy Worker-2 claims and completes the recovered job
    worker_2 = WorkerEngine(
        options=WorkerOptions(worker_id="healthy-worker-02", burst=True)
    )
    processed = worker_2.run()
    assert processed == 1

    job.refresh_from_db()
    assert job.status == JobStatus.COMPLETED
    assert job.attempt_count == 2


@pytest.mark.django_db
def test_reaper_moves_exhausted_zombie_job_to_dead_letter():
    """
    Verifies that if a job crashes on its final allowed attempt (attempt_count == max_attempts),
    the Reaper moves it permanently to EXHAUSTED instead of retrying forever.
    """
    expired_time = timezone.now() - timedelta(minutes=5)
    job = Job.objects.create(
        job_type="email.send",
        payload={"to": "user@company.com"},
        status=JobStatus.RUNNING,
        claimed_by="crashed-worker-99",
        lease_expires_at=expired_time,
        attempt_count=3,
        max_attempts=3,
    )

    reaped_count = ReaperService.reap_expired_leases()
    assert reaped_count == 1

    job.refresh_from_db()
    assert job.status == JobStatus.EXHAUSTED
    assert job.completed_at is not None
    assert "Max attempts (3) exhausted after worker crash" in job.error_message


@pytest.mark.django_db
def test_worker_lease_renewal_prevents_reaping():
    """
    Verifies that calling WorkerEngine.renew_lease pushes lease_expires_at into the future,
    protecting the active job from being reaped.
    """
    near_expiry = timezone.now() + timedelta(seconds=5)
    job = Job.objects.create(
        job_type="files.process",
        payload={"filename": "large.txt", "content": "hello world"},
        status=JobStatus.RUNNING,
        claimed_by="active-worker-1",
        lease_expires_at=near_expiry,
    )

    renewed = WorkerEngine.renew_lease(
        job=job,
        worker_id="active-worker-1",
        lease_duration_seconds=300,
    )
    assert renewed is True

    job.refresh_from_db()
    assert job.lease_expires_at > timezone.now() + timedelta(seconds=250)

    # Reaper sweep must not touch the renewed job
    assert ReaperService.reap_expired_leases() == 0


@pytest.mark.django_db(transaction=True)
def test_multi_worker_parallel_execution_no_duplicates():
    """
    Stress test: Enqueues 10 jobs and launches 4 concurrent WorkerEngine threads in burst mode.
    Verifies that all 10 jobs are processed to COMPLETED with zero duplicate executions.
    """
    for i in range(10):
        Job.objects.create(
            job_type="files.process",
            payload={"filename": f"doc_{i}.txt", "content": f"content block {i}"},
        )

    def run_worker_thread(idx: int) -> int:
        try:
            connections.close_all()
            engine = WorkerEngine(
                options=WorkerOptions(
                    worker_id=f"parallel-worker-{idx}",
                    burst=True,
                )
            )
            return engine.run()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(run_worker_thread, i) for i in range(4)]
        counts = [f.result() for f in futures]

    # Total jobs processed across all 4 workers must equal 10
    assert sum(counts) == 10
    assert Job.objects.filter(status=JobStatus.COMPLETED).count() == 10
    assert JobAttempt.objects.count() == 10
