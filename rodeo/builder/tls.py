"""TLS for the live builder: a CA of this operator and the server certificate it signs.

Files under :func:`tls_dir` (``~/.rodeo/builder/tls/``, 0700):

- ``ca.pem`` / ``ca.key``: the CA, made once and reused; valid :data:`CA_DAYS`, with
  X.509 name constraints limiting it to the names it was made for (:func:`constraints`:
  each DNS name's domain, each address on its own); the key is 0600.
- ``server.pem`` / ``server.key``: the server certificate signed by the CA, whose Subject
  Alternative Names are the names and addresses the builder is reached by; made again
  when one of them is missing or fewer than :data:`RENEW_DAYS` of validity are left.

A client that imports ``ca.pem`` once trusts every builder certificate this CA signs.
"""
from __future__ import annotations

import ipaddress
import socket
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from ..paths import invoking_home

CA_DAYS = 3650
SERVER_DAYS = 397
RENEW_DAYS = 30
IPAddr = ipaddress.IPv4Address | ipaddress.IPv6Address


def tls_dir() -> Path:
    return invoking_home() / ".rodeo" / "builder" / "tls"


def _is_ip(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def server_names(bind: str, allow_hosts: tuple[str, ...] = ()) -> tuple[list[str], list[IPAddr]]:
    """``(DNS names, addresses)`` the builder on *bind* is reached by: this host's FQDN and
    short name, the host's own non-loopback addresses, every ``--allow-host`` and *bind*
    itself when it is one address (not a wildcard)."""
    names: list[str] = []
    ips: list[IPAddr] = []

    def add(value: str) -> None:
        value = value.strip().lower().rstrip(".")
        if not value or value == "localhost":
            return
        if _is_ip(value):
            ip = ipaddress.ip_address(value)
            if ip not in ips:
                ips.append(ip)
        elif value not in names:
            names.append(value)

    add(socket.getfqdn())
    add(socket.gethostname())
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_loopback and not ip.is_link_local and ip not in ips:
                ips.append(ip)
    except (OSError, ValueError):
        pass
    for name in allow_hosts:
        add(name)
    if bind not in ("0.0.0.0", "::"):
        add(bind)
    return names, ips


def constraints(names: list[str], ips: list[IPAddr]) -> x509.NameConstraints:
    """Name constraints permitting each name's domain (the name itself when it has none)
    and each address on its own."""
    permitted: list[x509.GeneralName] = []
    seen: set[str] = set()
    for name in names:
        domain = name.split(".", 1)[1] if "." in name else name
        if domain not in seen:
            seen.add(domain)
            permitted.append(x509.DNSName(domain))
    for ip in ips:
        permitted.append(x509.IPAddress(ipaddress.ip_network(ip)))
    return x509.NameConstraints(permitted_subtrees=permitted, excluded_subtrees=None)


def uncovered(ca: x509.Certificate, names: list[str], ips: list[IPAddr]) -> list[str]:
    """The names and addresses *ca*'s constraints do not permit (none without constraints)."""
    try:
        permitted = ca.extensions.get_extension_for_class(x509.NameConstraints).value.permitted_subtrees or []
    except x509.ExtensionNotFound:
        return []
    domains = [g.value.lower() for g in permitted if isinstance(g, x509.DNSName)]
    networks = [g.value for g in permitted if isinstance(g, x509.IPAddress)]
    out = [n for n in names if not any(n == d or n.endswith("." + d) for d in domains)]
    out += [str(ip) for ip in ips if not any(ip in net for net in networks)]
    return out


def _write(path: Path, data: bytes, mode: int) -> None:
    tmp = path.with_name(path.name + ".new")
    tmp.write_bytes(data)
    tmp.chmod(mode)
    tmp.replace(path)


def _key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _load(directory: Path, stem: str) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey] | None:
    cert, key = directory / (stem + ".pem"), directory / (stem + ".key")
    if not cert.is_file() or not key.is_file():
        return None
    loaded = serialization.load_pem_private_key(key.read_bytes(), password=None)
    if not isinstance(loaded, ec.EllipticCurvePrivateKey):
        raise ValueError("{} is not an EC private key".format(key))
    return x509.load_pem_x509_certificate(cert.read_bytes()), loaded


def ensure_ca(directory: Path, names: list[str], ips: list[IPAddr]) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey, bool]:
    """The CA in *directory*, made for *names* and *ips* when there is none yet;
    ``(certificate, key, whether it was just made)``."""
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    existing = _load(directory, "ca")
    if existing is not None:
        return existing[0], existing[1], False
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "rodeo builder CA " + (names[0] if names else str(ips[0]) if ips else "local"))])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=CA_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                         crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(constraints(names, ips), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    _write(directory / "ca.key", _key_pem(key), 0o600)
    _write(directory / "ca.pem", cert.public_bytes(serialization.Encoding.PEM), 0o644)
    return cert, key, True


def _current(cert: x509.Certificate, ca: x509.Certificate, names: list[str], ips: list[IPAddr]) -> bool:
    """True when *cert* was signed by *ca*, names every name and address, and has more
    than :data:`RENEW_DAYS` left."""
    if cert.issuer != ca.subject:
        return False
    if cert.not_valid_after_utc - datetime.now(timezone.utc) < timedelta(days=RENEW_DAYS):
        return False
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False
    have_names = {n.lower() for n in san.get_values_for_type(x509.DNSName)}
    have_ips = set(san.get_values_for_type(x509.IPAddress))
    return set(names) <= have_names and set(ips) <= have_ips


def ensure_server_cert(directory: Path, ca: x509.Certificate, ca_key: ec.EllipticCurvePrivateKey,
                       names: list[str], ips: list[IPAddr]) -> tuple[Path, Path, bool]:
    """The server certificate and key in *directory* for *names* and *ips*, signed by *ca*,
    made again when the current one is not :func:`_current`; ``(cert, key, whether made)``."""
    if not names and not ips:
        raise ValueError("a server certificate needs at least one name or address")
    existing = _load(directory, "server")
    if existing is not None and _current(existing[0], ca, names, ips):
        return directory / "server.pem", directory / "server.key", False
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    san: list[x509.GeneralName] = [x509.DNSName(n) for n in names] + [x509.IPAddress(ip) for ip in ips]
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0] if names else str(ips[0]))]))
            .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=SERVER_DAYS))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=True, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    _write(directory / "server.key", _key_pem(key), 0o600)
    _write(directory / "server.pem", cert.public_bytes(serialization.Encoding.PEM), 0o644)
    return directory / "server.pem", directory / "server.key", True


def ssl_context(cert: Path, key: Path) -> ssl.SSLContext:
    """A server-side context (TLS 1.2 or newer) with *cert* and *key*."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(cert), str(key))
    return ctx


@dataclass
class ServerTLS:
    """What :func:`ensure` made or found: the server certificate and key, the CA certificate,
    the names the certificate carries, whether the CA was just made, and the names the CA's
    constraints do not cover (a browser refuses the certificate for those)."""

    cert: Path
    key: Path
    ca: Path
    names: list[str] = field(default_factory=list)
    ips: list[IPAddr] = field(default_factory=list)
    ca_created: bool = False
    uncovered: list[str] = field(default_factory=list)


def ensure(bind: str, allow_hosts: tuple[str, ...] = (), directory: Path | None = None) -> ServerTLS:
    """The CA and server certificate for a builder on *bind*, made or renewed as needed
    under *directory* (default :func:`tls_dir`)."""
    directory = directory or tls_dir()
    names, ips = server_names(bind, allow_hosts)
    if not names and not ips:
        raise ValueError("a server certificate needs at least one name or address: this host has only "
                         "loopback ones, give one with --allow-host")
    ca, ca_key, created = ensure_ca(directory, names, ips)
    cert, key, _ = ensure_server_cert(directory, ca, ca_key, names, ips)
    return ServerTLS(cert, key, directory / "ca.pem", names, ips, created, uncovered(ca, names, ips))
