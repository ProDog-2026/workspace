"""Google OIDC verification. Never accept an unverified JWT payload as identity."""

import base64
import hashlib
import hmac
import json
import math
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .errors import ConnectorError

JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"


def nonce_for(verifier: str) -> str:
    # The PKCE verifier is encrypted in the one-use, browser-bound OAuth flow.
    return hashlib.sha256(("oidc:" + verifier).encode("ascii")).hexdigest()


def _decode(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def verify_identity(token, jwks, *, client_id, nonce, access_token, email, now=None):
    """Validate the signature and all bindings before returning a stable Google sub."""
    try:
        if not isinstance(token, str) or not 0 < len(token) <= 16384:
            raise ValueError("token")
        encoded_header, encoded_claims, signature = token.split(".")
        header = json.loads(_decode(encoded_header))
        claims = json.loads(_decode(encoded_claims))
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ValueError("algorithm")
        keys = [key for key in jwks["keys"] if key.get("kid") == header["kid"]]
        if len(keys) != 1:
            raise ValueError("key")
        key = keys[0]
        if key.get("kty") != "RSA" or key.get("use", "sig") != "sig":
            raise ValueError("key type")
        public = rsa.RSAPublicNumbers(
            int.from_bytes(_decode(key["e"]), "big"),
            int.from_bytes(_decode(key["n"]), "big"),
        ).public_key()
        public.verify(_decode(signature), (encoded_header + "." + encoded_claims).encode(),
                      padding.PKCS1v15(), hashes.SHA256())
        current = time.time() if now is None else now
        if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
            raise ValueError("issuer")
        if claims.get("aud") != client_id or claims.get("azp", client_id) != client_id:
            raise ValueError("audience")
        if (type(claims.get("exp")) not in (int, float)
                or not math.isfinite(claims["exp"]) or claims["exp"] <= current):
            raise ValueError("expiry")
        if (type(claims.get("iat")) not in (int, float)
                or not math.isfinite(claims["iat"]) or claims["iat"] > current + 60):
            raise ValueError("issued at")
        if (not isinstance(claims.get("nonce"), str)
                or not hmac.compare_digest(claims["nonce"], nonce)):
            raise ValueError("nonce")
        expected_hash = base64.urlsafe_b64encode(
            hashlib.sha256(access_token.encode("ascii")).digest()[:16]).decode().rstrip("=")
        if not isinstance(claims.get("at_hash"), str) or not hmac.compare_digest(
                claims["at_hash"], expected_hash):
            raise ValueError("access token binding")
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub or len(sub) > 255 or not sub.isascii():
            raise ValueError("subject")
        if claims.get("email_verified") is not True or not isinstance(claims.get("email"), str):
            raise ValueError("verified email")
        if claims["email"].casefold() != email.casefold():
            raise ValueError("Gmail owner")
        return sub
    except (
        ValueError, TypeError, KeyError, AttributeError, OverflowError, InvalidSignature
    ) as exc:
        raise ConnectorError(
            "invalid_identity", "Google 로그인 정보를 확인하지 못했습니다.", 401
        ) from exc
