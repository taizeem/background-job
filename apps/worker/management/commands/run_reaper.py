from django.core.management.base import BaseCommand

from apps.worker.reaper import ReaperService


class Command(BaseCommand):
    help = "Sweeps the database for expired worker leases and recovers abandoned jobs."

    def handle(self, *args, **options):
        reaped = ReaperService.reap_expired_leases()
        if reaped > 0:
            self.stdout.write(
                self.style.WARNING(
                    f"Reaper recovered {reaped} abandoned zombie job(s)."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("Reaper sweep complete: 0 expired leases found.")
            )
