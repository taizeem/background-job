"""
File processing and text analysis job handler.
"""

import hashlib
from typing import Any

from apps.jobs.registry import register_job


@register_job("files.process")
def process_file(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Processes and analyzes file text.
    Payload expectations:
    - filename: str
    - content: str (raw text content)
    """
    filename = payload.get("filename")
    if not filename:
        raise ValueError("Payload missing required field 'filename'.")

    content = payload.get("content", "")
    content_bytes = content.encode("utf-8")

    word_count = len(content.split())
    sha256_hash = hashlib.sha256(content_bytes).hexdigest()

    return {
        "filename": filename,
        "byte_size": len(content_bytes),
        "word_count": word_count,
        "sha256": sha256_hash,
    }
