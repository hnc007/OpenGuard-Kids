"""Rotate two server certificates issued by one ephemeral CA on a single loopback origin."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from ipaddress import IPv4Address
import json
from pathlib import Path
import secrets
import ssl
from tempfile import TemporaryDirectory
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
import httpx
import pytest
from sqlalchemy.orm import Session

from agent.client import Transport
from scripts.benchmark_recovery import LocalServer
from server.cli import create_user
from server.db import make_engine
from server.models import Child
from server.security import Vault


@dataclass
class Leaf:
    cert: x509.Certificate
    key: ec.EllipticCurvePrivateKey

    @property
    def pin(self):
        return self.cert.fingerprint(hashes.SHA256()).hex()


def issue_certificates(folder):
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Ephemeral rotation test CA")])
    now = datetime.now(timezone.utc)
    base = (x509.CertificateBuilder().issuer_name(ca_name)
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1)))
    ca_usage = x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                             data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                             encipher_only=False, decipher_only=False)
    ca = (base.subject_name(ca_name).public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(ca_usage, critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    ca_file = folder / "ca.pem"
    ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))

    def issue_leaf():
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        leaf = (base.subject_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(IPv4Address("127.0.0.1"))]), critical=False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                .sign(ca_key, hashes.SHA256()))
        return Leaf(leaf, key)

    return str(ca_file), issue_leaf(), issue_leaf()


@contextmanager
def serving(server, leaf):
    (server.folder / "server.pem").write_bytes(leaf.cert.public_bytes(serialization.Encoding.PEM))
    (server.folder / "server-key.pem").write_bytes(leaf.key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    server.start()
    try:
        yield
    finally:
        server.stop()


def enroll(server, transport, ca_file):
    password = secrets.token_urlsafe(24)
    engine = make_engine(server.database)
    try:
        with Session(engine) as db, db.begin():
            parent = create_user(db, "rotation.parent", password)
            child = Child(parent_id=parent.id, name_cipher=Vault(server.key).encrypt("Hồ sơ thử"))
            db.add(child)
            db.flush()
            child_id = child.id
    finally:
        engine.dispose()
    with httpx.Client(base_url=transport.origin, verify=ssl.create_default_context(cafile=ca_file),
                      trust_env=False) as browser:
        csrf = {"X-CSRF-Token": browser.get("/api/auth/csrf").raise_for_status().json()["csrf_token"]}
        result = browser.post("/api/auth/login", headers=csrf, json={"username": "rotation.parent", "password": password})
        csrf["X-CSRF-Token"] = result.raise_for_status().json()["csrf_token"]
        consent = {"consent": True, "child_informed": True, "child_agrees": True}
        pair = browser.post(f"/api/children/{child_id}/pairing", headers=csrf, json=consent).raise_for_status().json()
    return transport.request("POST", "/api/enroll", {
        "code": pair["code"], "label": "Thiết bị thử", "installation_id": str(uuid4())})


def read_policy(transport, identity):
    path = f"/api/devices/{identity['device_id']}/policy"
    result = transport.request("GET", path, token=identity["access_token"])
    assert result["policy"]["device_id"] == identity["device_id"]
    assert result["policy"]["version"] == 1
    assert result["signature"]


def read_command_channel(transport, identity):
    path = f"/api/devices/{identity['device_id']}/commands/ws?policy_version=0"
    with transport.socket(path, identity["access_token"]) as channel:
        assert json.loads(channel.recv(timeout=5)) == {"commands": [], "policy_version": 1}


@pytest.mark.parametrize("probe", [read_policy, read_command_channel], ids=["https", "wss"])
def test_rotate_leaf_with_overlapping_pins(probe):
    with TemporaryDirectory(prefix="ogk-rotate-") as temp:
        folder = Path(temp)
        ca, old_leaf, new_leaf = issue_certificates(folder)
        assert old_leaf.key.public_key().public_numbers() != new_leaf.key.public_key().public_numbers()
        assert old_leaf.pin != new_leaf.pin
        server = LocalServer(folder)
        with serving(server, old_leaf):
            origin = f"https://127.0.0.1:{server.port}"
            old_only = Transport(origin, old_leaf.pin, ca)
            both = Transport(origin, f"{old_leaf.pin},{new_leaf.pin}", ca)
            new_only = Transport(origin, new_leaf.pin, ca)
            identity = enroll(server, old_only, ca)
            probe(old_only, identity)
            probe(both, identity)
            with pytest.raises(ssl.SSLError, match="mã ghim"):
                probe(new_only, identity)

        with serving(server, new_leaf):
            probe(both, identity)
            probe(new_only, identity)
            with pytest.raises(ssl.SSLError, match="mã ghim"):
                probe(old_only, identity)
            with pytest.raises(ssl.SSLCertVerificationError):
                probe(Transport(origin, new_leaf.pin, ca_file=None), identity)

        # After the old pin is retired, even a CA-valid rollback must be rejected.
        with serving(server, old_leaf):
            with pytest.raises(ssl.SSLError, match="mã ghim"):
                probe(new_only, identity)
        with serving(server, new_leaf):
            probe(new_only, identity)
