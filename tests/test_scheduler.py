from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.jobs.constants import JobStatus
from apps.jobs.models import Job
from apps.scheduler.engine import SchedulerEngine, SchedulerOptions
from apps.scheduler.models import PeriodicTask
from apps.worker.engine import WorkerEngine


@pytest.mark.django_db
def test_periodic_task_interval_calculation():
    """Verifies that an interval-based periodic task computes next_run_at correctly."""
    now = timezone.now()
    task = PeriodicTask.objects.create(
        name="test-interval-task",
        job_type="maintenance.cleanup",
        interval_seconds=300,  # 5 minutes
    )

    assert task.next_run_at >= now + timedelta(seconds=299)
    assert task.next_run_at <= now + timedelta(seconds=301)


@pytest.mark.django_db
def test_periodic_task_cron_calculation():
    """Verifies that a cron-based task calculates future execution time."""
    now = timezone.now()
    # Runs at minute 0 of every hour
    task = PeriodicTask.objects.create(
        name="test-cron-hourly",
        job_type="reports.generate",
        cron_expression="0 * * * *",
    )

    assert task.next_run_at > now
    assert task.next_run_at.minute == 0


def test_periodic_task_validation_requires_one_schedule_type():
    """Verifies that creating a PeriodicTask without any schedule raises ValidationError."""
    task = PeriodicTask(
        name="invalid-task",
        job_type="email.send",
    )
    with pytest.raises(ValidationError, match="You must specify either"):
        task.clean()


def test_periodic_task_rejects_invalid_cron():
    """Verifies that invalid cron syntax is rejected."""
    task = PeriodicTask(
        name="bad-cron",
        job_type="email.send",
        cron_expression="invalid cron string",
    )
    with pytest.raises(ValidationError, match="Invalid cron expression"):
        task.clean()


@pytest.mark.django_db
def test_scheduler_enqueues_due_task():
    """Verifies that SchedulerEngine enqueues due tasks and advances next_run_at."""
    past_time = timezone.now() - timedelta(seconds=10)
    task = PeriodicTask.objects.create(
        name="due-task-1",
        job_type="maintenance.cleanup",
        payload={"target": "sessions"},
        interval_seconds=60,
        next_run_at=past_time,
    )

    engine = SchedulerEngine(options=SchedulerOptions(burst=True))
    enqueued = engine.enqueue_due_tasks()

    assert enqueued == 1

    # Verify a new Job record was created in PostgreSQL
    job = Job.objects.filter(job_type="maintenance.cleanup").first()
    assert job is not None
    assert job.status == JobStatus.PENDING
    assert job.payload == {"target": "sessions"}

    # Verify PeriodicTask counters were updated
    task.refresh_from_db()
    assert task.total_run_count == 1
    assert task.last_run_at is not None
    assert task.next_run_at > timezone.now()


@pytest.mark.django_db
def test_scheduler_idempotency_prevents_duplicate_enqueues():
    """
    Verifies that running two scheduler sweeps for the exact same tick does
    not enqueue duplicate jobs.
    """
    due_time = timezone.now() - timedelta(seconds=1)
    task = PeriodicTask.objects.create(
        name="dedup-task",
        job_type="maintenance.cleanup",
        interval_seconds=60,
        next_run_at=due_time,
    )

    engine = SchedulerEngine(options=SchedulerOptions(burst=True))
    # First sweep enqueues the task
    engine.enqueue_due_tasks()
    assert Job.objects.filter(job_type="maintenance.cleanup").count() == 1

    # Simulate clock race or retry: reset task back to the original tick
    task.next_run_at = due_time
    task.save()

    # Second sweep with same tick must NOT duplicate the job
    engine.enqueue_due_tasks()
    assert Job.objects.filter(job_type="maintenance.cleanup").count() == 1


@pytest.mark.django_db
def test_inactive_periodic_tasks_are_ignored():
    """Verifies that is_active=False tasks are never enqueued."""
    past_time = timezone.now() - timedelta(minutes=5)
    PeriodicTask.objects.create(
        name="inactive-task",
        job_type="reports.generate",
        interval_seconds=60,
        is_active=False,
        next_run_at=past_time,
    )

    engine = SchedulerEngine(options=SchedulerOptions(burst=True))
    enqueued = engine.enqueue_due_tasks()

    assert enqueued == 0
    assert Job.objects.filter(job_type="reports.generate").count() == 0


@pytest.mark.django_db
def test_delayed_job_execution_timing():
    """
    Verifies that a job scheduled in the future is not claimable by workers
    until its scheduled_at timestamp arrives.
    """
    future_time = timezone.now() + timedelta(hours=1)
    delayed_job = Job.objects.create(
        job_type="email.send",
        payload={"to": "later@company.com"},
        scheduled_at=future_time,
    )

    # Worker polls queue: delayed job must be skipped
    claimed = WorkerEngine.claim_next_job(worker_id="w-test")
    assert claimed is None

    # Simulate time passing: move scheduled_at into the past
    delayed_job.scheduled_at = timezone.now() - timedelta(seconds=5)
    delayed_job.save()

    # Worker polls again: now the job is claimed
    claimed_now = WorkerEngine.claim_next_job(worker_id="w-test")
    assert claimed_now is not None
    assert claimed_now.id == delayed_job.id
