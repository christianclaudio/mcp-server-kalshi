"""Tests for RSA-PSS and Ed25519 signing and Kalshi auth header construction."""

import base64

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from mcp_server_kalshi.kalshi_client.base import (
    KalshiAuth,
    load_private_key_from_file,
    sign_pss_text,
    sign_text,
)

_PSS = padding.PSS(
    mgf=padding.MGF1(hashes.SHA256()),
    salt_length=padding.PSS.DIGEST_LENGTH,
)


def _make_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _verify_rsa(key: rsa.RSAPrivateKey, signature: str, message: str) -> None:
    key.public_key().verify(
        base64.b64decode(signature),
        message.encode("utf-8"),
        _PSS,
        hashes.SHA256(),
    )


def test_sign_pss_text_verifies_with_public_key():
    key = _make_key()
    msg = "1700000000000GET/trade-api/v2/portfolio/balance"
    sig = sign_pss_text(key, msg)
    # A valid signature verifies against the public key (raises if not).
    _verify_rsa(key, sig, msg)


def test_auth_flow_signs_path_without_query():
    key = _make_key()
    auth = KalshiAuth(key, "my-key-id")
    request = httpx.Request(
        "GET",
        "https://demo-api.kalshi.co/trade-api/v2/portfolio/orders?limit=5&status=resting",
    )

    flow = auth.auth_flow(request)
    signed_request = next(flow)

    assert signed_request.headers["KALSHI-ACCESS-KEY"] == "my-key-id"
    ts = signed_request.headers["KALSHI-ACCESS-TIMESTAMP"]
    sig = signed_request.headers["KALSHI-ACCESS-SIGNATURE"]

    # The signed message must exclude the query string.
    signed_path = "/trade-api/v2/portfolio/orders"
    _verify_rsa(key, sig, ts + "GET" + signed_path)


def test_load_private_key_roundtrip(tmp_path):
    key = _make_key()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_file = tmp_path / "rsa.key"
    key_file.write_bytes(pem)

    loaded = load_private_key_from_file(str(key_file))
    assert isinstance(loaded, rsa.RSAPrivateKey)  # not a string repr (the old bug)


def test_load_pkcs8_rsa_private_key_signs_as_rsa(tmp_path):
    # PKCS#8 RSA uses BEGIN PRIVATE KEY, the same banner as Ed25519.
    key = _make_key()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    assert pem.startswith(b"-----BEGIN PRIVATE KEY-----")
    assert b"BEGIN RSA PRIVATE KEY" not in pem
    key_file = tmp_path / "rsa-pkcs8.pem"
    key_file.write_bytes(pem)

    loaded = load_private_key_from_file(str(key_file))
    assert isinstance(loaded, rsa.RSAPrivateKey)
    msg = "1700000000000POST/trade-api/v2/portfolio/orders"
    _verify_rsa(loaded, sign_pss_text(loaded, msg), msg)


def test_sign_text_ed25519_verifies_with_public_key():
    key = ed25519.Ed25519PrivateKey.generate()
    msg = "1700000000000GET/trade-api/v2/portfolio/balance"
    sig = sign_text(key, msg)
    assert len(base64.b64decode(sig)) == 64
    key.public_key().verify(base64.b64decode(sig), msg.encode("utf-8"))


def test_auth_flow_ed25519_signs_path_without_query(tmp_path):
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    assert pem.startswith(b"-----BEGIN PRIVATE KEY-----")
    key_file = tmp_path / "ed25519.pem"
    key_file.write_bytes(pem)
    loaded = load_private_key_from_file(str(key_file))
    assert isinstance(loaded, ed25519.Ed25519PrivateKey)

    auth = KalshiAuth(loaded, "ed-key-id")
    request = httpx.Request(
        "GET",
        "https://demo-api.kalshi.co/trade-api/v2/portfolio/orders?limit=5&status=resting",
    )
    signed_request = next(auth.auth_flow(request))

    assert signed_request.headers["KALSHI-ACCESS-KEY"] == "ed-key-id"
    ts = signed_request.headers["KALSHI-ACCESS-TIMESTAMP"]
    sig = signed_request.headers["KALSHI-ACCESS-SIGNATURE"]
    signed_path = "/trade-api/v2/portfolio/orders"
    loaded.public_key().verify(
        base64.b64decode(sig),
        (ts + "GET" + signed_path).encode("utf-8"),
    )
