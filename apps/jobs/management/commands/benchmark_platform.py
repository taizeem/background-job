"""
Management command to benchmark job submission throughput, worker concurrency
throughput, and p50/p95/p99 execution latencies.
"""

import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from django.core.management.base import BaseCommand
from django.db import connections

from apps.jobs.models import Job, JobAttempt
from apps.jobs.services.job_service import JobService
from apps.worker.engine import WorkerEngine, WorkerOptions


@dataclass(frozen=True)
class BenchmarkReport:
    """Structured results from a platform benchmark run."""

    total_jobs: int
    worker_threads: int
    enqueue_duration_s: float
    enqueue_rate_per_s: float
    process_duration_s: float
    process_rate_per_s: float
    p50_ms: float
    p95_ms: float
    p99_ms: float


def compute_percentile(sorted_values: list[float], percentile: float) -> float:
    """Computes the given percentile (0-100) from a pre-sorted list of floats."""
    if not sorted_values:
        return 0.0
    index = max(0, math.ceil((percentile / 100.0) * len(sorted_values)) - 1)
    return round(sorted_values[index], 3)


def run_benchmark(total_jobs: int = 100, worker_threads: int = 4) -> BenchmarkReport:
    """
    Executes an isolated benchmark of job submission and parallel worker processing.
    """
    created_ids = []

    # 1. Benchmark Job Submission (Enqueue)
    enqueue_start = time.monotonic()
    for i in range(total_jobs):
        res = JobService.create_job(
            job_type="files.process",
            payload={
                "filename": f"bench_{i}.txt",
                "content": f"Benchmarking distributed job queue payload #{i}",
            },
        )
        created_ids.append(res.job.id)
    enqueue_duration_s = max(0.001, time.monotonic() - enqueue_start)
    enqueue_rate = round(total_jobs / enqueue_duration_s, 2)

    # 2. Benchmark Parallel Worker Execution
    def worker_task(idx: int) -> int:
        try:
            connections.close_all()
            engine = WorkerEngine(
                options=WorkerOptions(
                    worker_id=f"bench-worker-{idx}",
                    burst=True,
                )
            )
            return engine.run()
        finally:
            connections.close_all()

    process_start = time.monotonic()
    with ThreadPoolExecutor(max_workers=worker_threads) as executor:
        futures = [executor.submit(worker_task, w) for w in range(worker_threads)]
        for f in futures:
            f.result()
    process_duration_s = max(0.001, time.monotonic() - process_start)
    process_rate = round(total_jobs / process_duration_s, 2)

    # 3. Collect Execution Durations and Compute Percentiles
    durations = list(
        JobAttempt.objects.filter(job_id__in=created_ids, duration_ms__isnull=False)
        .order_by("duration_ms")
        .values_list("duration_ms", flat=True)
    )

    p50 = compute_percentile(durations, 50)
    p95 = compute_percentile(durations, 95)
    p99 = compute_percentile(durations, 99)

    # Clean up benchmark jobs
    Job.objects.filter(id__in=created_ids).delete()

    return BenchmarkReport(
        total_jobs=total_jobs,
        worker_threads=worker_threads,
        enqueue_duration_s=round(enqueue_duration_s, 3),
        enqueue_rate_per_s=enqueue_rate,
        process_duration_s=round(process_duration_s, 3),
        process_rate_per_s=process_rate,
        p50_ms=p50,
        p95_ms=p95,
        p99_ms=p99,
    )


class Command(BaseCommand):
    help = "Runs a throughput and latency benchmark on the job queue and worker engine."

    def add_arguments(self, parser):
        parser.add_argument(
            "--jobs",
            type=int,
            default=100,
            help="Total number of jobs to enqueue and process (default: 100)",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=4,
            help="Number of concurrent worker threads (default: 4)",
        )

    def handle(self, *args, **options):
        total_jobs = options["jobs"]
        workers = options["workers"]

        self.stdout.write(
            self.style.WARNING(
                f"Starting benchmark: {total_jobs} jobs across {workers} concurrent workers..."
            )
        )
        report = run_benchmark(total_jobs=total_jobs, worker_threads=workers)

        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS("PLATFORM PERFORMANCE BENCHMARK RESULTS"))
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(f"Total Jobs Enqueued & Processed : {report.total_jobs}")
        self.stdout.write(f"Concurrent Worker Threads       : {report.worker_threads}")
        self.stdout.write("-" * 60)
        self.stdout.write(
            f"Submission (Enqueue) Throughput : {report.enqueue_rate_per_s} jobs/sec "
            f"({report.enqueue_duration_s}s)"
        )
        self.stdout.write(
            f"Worker Processing Throughput    : {report.process_rate_per_s} jobs/sec "
            f"({report.process_duration_s}s)"
        )
        self.stdout.write("-" * 60)
        self.stdout.write(f"Handler Execution Latency p50   : {report.p50_ms} ms")
        self.stdout.write(f"Handler Execution Latency p95   : {report.p95_ms} ms")
        self.stdout.write(f"Handler Execution Latency p99   : {report.p99_ms} ms")
        self.stdout.write(self.style.SUCCESS("=" * 60))