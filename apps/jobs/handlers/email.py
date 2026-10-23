"""
Email dispatch job handler with error classification support.
"""

import uuid
from typing import Any

from apps.jobs.exceptions import NonRetryableJobError, RetryableJobError
from apps.jobs.registry import register_job


@register_job("email.send")
def send_email(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Simulates sending an email notification.
    Payload expectations:
    - to: str (recipient email address)
    - subject: str (email subject line)
    - simulate_timeout: bool (optional: triggers RetryableJobError)
    """
    recipient = payload.get("to") or payload.get("recipient")
    if not recipient:
        raise NonRetryableJobError("Payload missing required recipient email ('to' or 'recipient').")

    if "@" not in recipient:
        raise NonRetryableJobError(f"Invalid email address format: '{recipient}'.")

    # Simulate transient network / rate-limiting outage if requested
    if payload.get("simulate_timeout"):
        raise RetryableJobError("Connection to downstream SMTP server timed out after 5000ms.")

    subject = payload.get("subject", "(No Subject)")

    return {
        "delivery_status": "sent",
        "message_id": f"msg-{uuid.uuid4().hex[:12]}",
        "recipient": recipient,
        "subject": subject,
    }