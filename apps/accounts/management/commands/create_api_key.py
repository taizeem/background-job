from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.accounts.models import ApiKey

User = get_user_model()


class Command(BaseCommand):
    help = "Generates a new cryptographic API key for a specified username."

    def add_arguments(self, parser):
        parser.add_argument(
            "username", type=str, help="Username of the user owning the key"
        )
        parser.add_argument(
            "--name",
            type=str,
            default="Default Key",
            help="Descriptive label for this key",
        )
        parser.add_argument(
            "--max-active-jobs",
            type=int,
            default=10,
            help="Maximum active concurrent jobs allowed for this key (default: 10)",
        )

    def handle(self, *args, **options):
        username = options["username"]
        user = User.objects.filter(username=username).first()

        if not user:
            self.stderr.write(self.style.ERROR(f"User '{username}' does not exist."))
            return

        api_key, raw_secret = ApiKey.generate(
            user=user,
            name=options["name"],
            max_active_jobs=options["max_active_jobs"],
        )

        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS(f"API Key created for user '{username}'!"))
        self.stdout.write(self.style.SUCCESS(f"Key Name:        {api_key.name}"))
        self.stdout.write(
            self.style.SUCCESS(f"Key Prefix:      {api_key.key_prefix}...")
        )
        self.stdout.write(
            self.style.SUCCESS(f"Max Active Jobs: {api_key.max_active_jobs}")
        )
        self.stdout.write(self.style.WARNING("-" * 60))
        self.stdout.write(self.style.WARNING(f"RAW SECRET KEY:  {raw_secret}"))
        self.stdout.write(
            self.style.WARNING("Copy this key now. It cannot be recovered later!")
        )
        self.stdout.write(self.style.SUCCESS("=" * 60))
