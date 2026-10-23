"""
Email dispatch job handler.
"""

import uuid
from typing import Any

from apps.jobs.registry import register_job


@register_job("email.send")
def send_email(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Simulates sending an email notification.
    Payload expectations:
    - to: str (recipient email address)
    - subject: str (email subject line)
    - body: str (email content)
    """
    recipient = payload.get("to") or payload.get("recipient")
    if not recipient:
        raise ValueError(
            "Payload missing required recipient email ('to' or 'recipient')."
        )

    if "@" not in recipient:
        raise ValueError(f"Invalid email address format: '{recipient}'.")

    subject = payload.get("subject", "(No Subject)")

    return {
        "delivery_status": "sent",
        "message_id": f"msg-{uuid.uuid4().hex[:12]}",
        "recipient": recipient,
        "subject": subject,
    }
