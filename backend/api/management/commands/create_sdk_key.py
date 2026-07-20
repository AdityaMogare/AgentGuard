from django.core.management.base import BaseCommand

from api.auth_models import SDKApiKey
from api.auth_utils import generate_sdk_key


class Command(BaseCommand):
    help = "Create a hashed SDK API key (plaintext printed once)."

    def add_arguments(self, parser):
        parser.add_argument("--name", default="ingest", help="Key label")
        parser.add_argument(
            "--scopes",
            default="spans:write,agents:read,alerts:write",
            help="Comma-separated scopes",
        )

    def handle(self, *args, **options):
        scopes = [s.strip() for s in options["scopes"].split(",") if s.strip()]
        plaintext, prefix, key_hash = generate_sdk_key()
        row = SDKApiKey.objects.create(
            name=options["name"],
            key_prefix=prefix,
            key_hash=key_hash,
            scopes=scopes,
        )
        self.stdout.write(self.style.SUCCESS(f"Created SDK key id={row.id}"))
        self.stdout.write(f"prefix: {prefix}")
        self.stdout.write(f"scopes: {scopes}")
        self.stdout.write(self.style.WARNING("Store this key now — it will not be shown again:"))
        self.stdout.write(plaintext)
