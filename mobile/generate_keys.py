"""Generate an application-server key pair for Web Push."""
from base64 import urlsafe_b64encode
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


if __name__ == "__main__":
    key = ec.generate_private_key(ec.SECP256R1())
    path = Path("vapid-private.pem")
    with path.open("xb") as out:
        out.write(key.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    public = key.public_key().public_bytes(serialization.Encoding.X962,
                                           serialization.PublicFormat.UncompressedPoint)
    print("VAPID_PRIVATE_KEY=" + str(path.resolve()))
    print("VAPID_PUBLIC_KEY=" + urlsafe_b64encode(public).decode().rstrip("="))
