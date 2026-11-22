"""Issue a local development certificate. Never import it into Windows trust stores."""
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def certificates(directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    server_file, key_file, ca_file = [directory / name for name in ("server.pem", "server-key.pem", "ca.pem")]
    if not any(file.exists() for file in (server_file, key_file, ca_file)):
        ca_key, leaf_key = (ec.generate_private_key(ec.SECP256R1()) for _ in range(2))
        now = datetime.now(timezone.utc)
        def builder(name, public, issuer):
            return (x509.CertificateBuilder().subject_name(name).issuer_name(issuer)
                    .public_key(public).serial_number(x509.random_serial_number())
                    .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=90)))
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OpenGuard Kids local development CA")])
        ca = builder(ca_name, ca_key.public_key(), ca_name).add_extension(
            x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(ca_key, hashes.SHA256())
        leaf = builder(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"localhost")]), leaf_key.public_key(), ca_name)
        leaf = (leaf.add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.IPAddress(ipaddress.ip_address("::1"))]), False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True).sign(ca_key, hashes.SHA256()))
        for file, material in ((key_file, leaf_key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption())), (server_file, leaf.public_bytes(serialization.Encoding.PEM)),
            (ca_file, ca.public_bytes(serialization.Encoding.PEM))):
            with file.open("xb") as handle:
                handle.write(material)
    if not all(file.is_file() for file in (server_file, key_file, ca_file)):
        raise RuntimeError("Thư mục chứng chỉ chưa đầy đủ. Chọn một thư mục mới để cấp lại.")
    cert = x509.load_pem_x509_certificate(server_file.read_bytes())
    if datetime.now(timezone.utc) >= cert.not_valid_after_utc:
        raise RuntimeError("Chứng chỉ lab đã hết hạn. Thực hiện quy trình đổi CA và pin.")
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


if __name__ == "__main__":
    print("SHA-256 TLS:", certificates(Path(".local/tls")))
