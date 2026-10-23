"""
Report generation job handler.
"""

from typing import Any

from apps.jobs.registry import register_job


@register_job("reports.generate")
def generate_report(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Simulates generating a business report.
    Payload expectations:
    - year: int (optional, default 2026)
    - month: str (optional, default "All")
    - format: str (optional: "pdf", "csv", "json")
    """
    year = payload.get("year", 2026)
    month = payload.get("month", "All")
    output_format = payload.get("format", "json").lower()

    if output_format not in ("pdf", "csv", "json"):
        raise ValueError(
            f"Unsupported report format '{output_format}'. Must be pdf, csv, or json."
        )

    # Simulated report metrics
    simulated_rows = 1520
    return {
        "report_name": f"Financial-Summary-{year}-{month}.{output_format}",
        "rows_processed": simulated_rows,
        "format": output_format,
        "generated_by": "reports.generate",
    }
