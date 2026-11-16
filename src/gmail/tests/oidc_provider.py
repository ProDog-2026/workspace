"""Synthetic signed Google responses for offline OAuth integration tests."""
import base64
import hashlib
import json
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from prodog_gmail.identity import nonce_for

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def jwks():
    numbers = KEY.public_key().public_numbers()
    return {"keys": [{"kid": "synthetic", "kty": "RSA",
                       "n": encode(numbers.n.to_bytes(256, "big")),
                       "e": encode(numbers.e.to_bytes(3, "big"))}]}


def id_token(verifier, access, email, client_id="fake-client"):
    claims = {"iss": "https://accounts.google.com", "aud": client_id,
              "iat": time.time(), "exp": time.time() + 300,
              "nonce": nonce_for(verifier), "sub": "sub:" + email,
              "email": email, "email_verified": True,
              "at_hash": encode(hashlib.sha256(access.encode()).digest()[:16])}
    message = encode(json.dumps({"alg": "RS256", "kid": "synthetic"}).encode())
    message += "." + encode(json.dumps(claims).encode())
    return message + "." + encode(KEY.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256()))
