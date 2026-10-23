import pytest

from apps.jobs.constants import AttemptStatus, JobStatus
from apps.jobs.handlers.email import send_email
from apps.jobs.handlers.files import process_file
from apps.jobs.handlers.reports import generate_report
from apps.jobs.models import Job, JobAttempt
from apps.jobs.registry import get_handler, is_job_type_registered
from apps.worker.engine import WorkerEngine, WorkerOptions


def test_registry_contains_default_handlers():
    """Verifies that all standard handlers are registered."""
    assert is_job_type_registered("reports.generate") is True
    assert is_job_type_registered("email.send") is True
    assert is_job_type_registered("files.process") is True
    assert get_handler("unregistered.task") is None


def test_reports_handler_success():
    """Verifies reports.generate produces structured metrics."""
    result = generate_report({"year": 2026, "month": "November", "format": "pdf"})
    assert result["report_name"] == "Financial-Summary-2026-November.pdf"
    assert result["rows_processed"] == 1520
    assert result["format"] == "pdf"


def test_reports_handler_invalid_format_raises_error():
    """Verifies reports.generate rejects invalid formats."""
    with pytest.raises(ValueError, match="Unsupported report format 'exe'"):
        generate_report({"format": "exe"})


def test_email_handler_success():
    """Verifies email.send returns a valid delivery status and message_id."""
    result = send_email({"to": "investor@venture.com", "subject": "Q4 Results"})
    assert result["delivery_status"] == "sent"
    assert result["recipient"] == "investor@venture.com"
    assert result["message_id"].startswith("msg-")


def test_email_handler_missing_recipient_raises_error():
    """Verifies email.send requires a recipient."""
    with pytest.raises(ValueError, match="missing required recipient"):
        send_email({"subject": "Hello"})


def test_files_handler_success():
    """Verifies files.process calculates word count and SHA256 checksum."""
    text = "The quick brown fox jumps over the lazy dog"
    result = process_file({"filename": "sample.txt", "content": text})

    assert result["filename"] == "sample.txt"
    assert result["word_count"] == 9
    assert len(result["sha256"]) == 64  # Standard SHA-256 hex length


@pytest.mark.django_db
def test_worker_executes_real_handler_and_stores_result():
    """Verifies end-to-end execution of a real handler by WorkerEngine."""
    job = Job.objects.create(
        job_type="reports.generate",
        payload={"year": 2026, "month": "October", "format": "csv"},
    )
    job.transition_to(JobStatus.CLAIMED)
    job.save()

    engine = WorkerEngine(options=WorkerOptions(worker_id="test-worker"))
    success = engine.execute_job(job)

    assert success is True
    job.refresh_from_db()
    assert job.status == JobStatus.COMPLETED
    assert job.result["report_name"] == "Financial-Summary-2026-October.csv"
    assert job.result["format"] == "csv"

    # Verify attempt log
    attempt = JobAttempt.objects.filter(job=job).first()
    assert attempt.status == AttemptStatus.SUCCESS
    assert attempt.duration_ms > 0


@pytest.mark.django_db
def test_worker_captures_error_and_traceback():
    """Verifies that when a handler raises an exception, the worker captures the traceback."""
    # Invalid email without @
    job = Job.objects.create(
        job_type="email.send",
        payload={"to": "not-an-email"},
    )
    job.transition_to(JobStatus.CLAIMED)
    job.save()

    engine = WorkerEngine(options=WorkerOptions(worker_id="test-worker"))
    success = engine.execute_job(job)

    assert success is False
    job.refresh_from_db()
    assert job.status == JobStatus.FAILED
    assert "Invalid email address format" in job.error_message
    assert "Traceback (most recent call last):" in job.error_traceback

    # Verify attempt log captured the traceback
    attempt = JobAttempt.objects.filter(job=job).first()
    assert attempt.status == AttemptStatus.FAILURE
    assert "Invalid email address format" in attempt.error_message
    assert "Traceback (most recent call last):" in attempt.error_traceback