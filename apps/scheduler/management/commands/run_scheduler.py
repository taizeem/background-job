from django.core.management.base import BaseCommand

from apps.scheduler.engine import SchedulerEngine, SchedulerOptions


class Command(BaseCommand):
    help = "Runs the background scheduler daemon that triggers periodic tasks."

    def add_arguments(self, parser):
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=1.0,
            help="Seconds to wait between schedule discovery sweeps (default: 1.0)",
        )
        parser.add_argument(
            "--burst",
            action="store_true",
            help="Run a single sweep of due tasks and exit immediately",
        )

    def handle(self, *args, **options):
        scheduler_options = SchedulerOptions(
            poll_interval=options["poll_interval"],
            burst=options["burst"],
        )
        engine = SchedulerEngine(options=scheduler_options)
        self.stdout.write(
            self.style.SUCCESS(
                f"Scheduler is active and monitoring periodic tasks (poll_interval={scheduler_options.poll_interval}s). Press Ctrl+C to stop."
            )
        )
        engine.run()
