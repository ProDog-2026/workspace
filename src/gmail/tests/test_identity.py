"""Offline OIDC regressions using freshly generated signing keys, never real tokens."""
import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from prodog_gmail.errors import ConnectorError
from prodog_gmail.identity import nonce_for, verify_identity


def encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def fixture_token(key, changes=None, header_changes=None):
    header = {"kid": "synthetic", "alg": "RS256", **(header_changes or {})}
    claims = {
        "iss": "https://accounts.google.com", "aud": "synthetic-client",
        "iat": 900, "exp": 1100, "sub": "google-user-a",
        "nonce": nonce_for("synthetic-verifier"), "email": "a@example.invalid",
        "email_verified": True,
        "at_hash": encode(hashlib.sha256(b"synthetic-access").digest()[:16]),
        **(changes or {}),
    }
    signing = encode(json.dumps(header).encode()) + "." + encode(json.dumps(claims).encode())
    token = signing + "." + encode(key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256()))
    numbers = key.public_key().public_numbers()
    jwks = {"keys": [{"kid": "synthetic", "kty": "RSA", "use": "sig",
                       "n": encode(numbers.n.to_bytes(256, "big")),
                       "e": encode(numbers.e.to_bytes(3, "big"))}]}
    return token, jwks


def verify(token, jwks, **changes):
    return verify_identity(token, jwks, **{
        "client_id": "synthetic-client", "nonce": nonce_for("synthetic-verifier"),
        "access_token": "synthetic-access", "email": "a@example.invalid", "now": 1000,
        **changes,
    })


def test_signed_identity_returns_stable_subject(signing_key):
    token, jwks = fixture_token(signing_key)
    assert verify(token, jwks) == "google-user-a"


@pytest.mark.parametrize("changes", [
    {"iss": "https://attacker.invalid"}, {"aud": "another-client"},
    {"azp": "another-client"}, {"exp": 1000}, {"exp": float('nan')},
    {"iat": 1100}, {"iat": float('nan')}, {"nonce": "other-flow"},
    {"at_hash": "other-access"}, {"email_verified": False},
    {"email_verified": "true"}, {"email": "b@example.invalid"},
    {"sub": ""}, {"sub": None}, {"sub": "x" * 256},
])
def test_rejects_wrong_identity_bindings(signing_key, changes):
    token, jwks = fixture_token(signing_key, changes)
    with pytest.raises(ConnectorError, match="invalid_identity"):
        verify(token, jwks)


@pytest.mark.parametrize("header", [{"alg": "none"}, {"alg": "HS256"}, {"kid": "unknown"}])
def test_rejects_algorithm_and_key_substitution(signing_key, header):
    token, jwks = fixture_token(signing_key, header_changes=header)
    with pytest.raises(ConnectorError):
        verify(token, jwks)


def test_rejects_forged_signature(signing_key):
    token, jwks = fixture_token(signing_key)
    head, payload, _ = token.split('.')
    with pytest.raises(ConnectorError):
        verify(head + '.' + payload + '.' + encode(b'forged'), jwks)


def test_different_flow_or_gmail_owner_is_rejected(signing_key):
    token, jwks = fixture_token(signing_key)
    for changes in ({"nonce": nonce_for("other-verifier")},
                    {"access_token": "other-access"}, {"email": "b@example.invalid"}):
        with pytest.raises(ConnectorError):
            verify(token, jwks, **changes)
