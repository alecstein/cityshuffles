import os
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Generate private Web Push keys once; never replaces existing keys."

    def handle(self, *args, **kwargs):
        path = settings.WEB_PUSH_PRIVATE_KEY
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.exists():
            self.stdout.write("Existing push key preserved.")
            return
        key = ec.generate_private_key(ec.SECP256R1())
        data = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
            output.write(data)
        self.stdout.write("Push key generated in private secret storage.")
