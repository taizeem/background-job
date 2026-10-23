from django.core.management.base import BaseCommand

from apps.worker.engine import WorkerEngine, WorkerOptions


class Command(BaseCommand):
    help = "Runs a background worker process that claims and executes queued jobs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--worker-id",
            type=str,
            default=None,
            help="Custom identifier for this worker instance (default: random UUID)",
        )
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=1.0,
            help="Seconds to wait between database polls when queue is empty (default: 1.0)",
        )
        parser.add_argument(
            "--lease-duration",
            type=int,
            default=300,
            help="Lease duration in seconds for claimed jobs (default: 300)",
        )
        parser.add_argument(
            "--burst",
            action="store_true",
            help="Run until the queue is empty, then exit immediately",
        )

    def handle(self, *args, **options):
        worker_options = WorkerOptions(
            worker_id=options["worker_id"],
            poll_interval=options["poll_interval"],
            lease_duration_seconds=options["lease_duration"],
            burst=options["burst"],
        )
        engine = WorkerEngine(options=worker_options)
        self.stdout.write(
            self.style.SUCCESS(
                f"Worker '{engine.worker_id}' is active and listening for jobs (poll_interval={worker_options.poll_interval}s). Press Ctrl+C to stop."
            )
        )
        engine.run()
