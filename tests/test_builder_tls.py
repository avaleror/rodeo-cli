"""The builder's TLS: its CA, the server certificate it signs, and renewals (rodeo/builder/tls.py)."""
from __future__ import annotations

import ipaddress
import ssl
import stat
from datetime import datetime, timezone

import pytest
from cryptography import x509

from rodeo.builder import tls


@pytest.fixture(autouse=True)
def this_host(monkeypatch):
    monkeypatch.setattr(tls.socket, "getfqdn", lambda *a: "builder.example.test")
    monkeypatch.setattr(tls.socket, "gethostname", lambda *a: "builder")
    monkeypatch.setattr(tls.socket, "getaddrinfo", lambda host, port: [(None, None, None, None, ("10.0.0.5", 0)),
                                                                        (None, None, None, None, ("127.0.0.1", 0))])


def _san(cert):
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return san.get_values_for_type(x509.DNSName), san.get_values_for_type(x509.IPAddress)


def test_server_names_are_the_hosts_names_and_addresses_plus_the_given_ones():
    names, ips = tls.server_names("0.0.0.0", ("Lab.Example.Test", "192.168.1.9", "localhost"))
    assert names == ["builder.example.test", "builder", "lab.example.test"]
    assert ips == [ipaddress.ip_address("10.0.0.5"), ipaddress.ip_address("192.168.1.9")]
    assert tls.server_names("10.0.0.5")[1] == [ipaddress.ip_address("10.0.0.5")]
    assert ipaddress.ip_address("127.0.0.1") in tls.server_names("127.0.0.1")[1]


def test_constraints_permit_each_domain_and_each_address():
    permitted = tls.constraints(["builder.example.test", "box", "lab.example.test"],
                                [ipaddress.ip_address("10.0.0.5")]).permitted_subtrees
    assert [g.value for g in permitted if isinstance(g, x509.DNSName)] == ["example.test", "box"]
    assert [str(g.value) for g in permitted if isinstance(g, x509.IPAddress)] == ["10.0.0.5/32"]


def test_the_ca_is_made_once_with_constraints_and_a_private_key(tmp_path):
    made = tls.ensure("0.0.0.0", directory=tmp_path / "tls")
    assert made.ca_created and made.ca == tmp_path / "tls" / "ca.pem" and made.uncovered == []
    assert stat.S_IMODE((tmp_path / "tls").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "tls" / "ca.key").stat().st_mode) == 0o600
    assert stat.S_IMODE(made.key.stat().st_mode) == 0o600
    ca = x509.load_pem_x509_certificate(made.ca.read_bytes())
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True
    assert ca.extensions.get_extension_for_class(x509.NameConstraints).critical
    assert "rodeo builder CA builder.example.test" in ca.subject.rfc4514_string()
    again = tls.ensure("0.0.0.0", directory=tmp_path / "tls")
    assert not again.ca_created and again.ca.read_bytes() == made.ca.read_bytes()
    assert again.cert.read_bytes() == made.cert.read_bytes()


def test_the_server_certificate_names_every_name_and_address_and_is_signed_by_the_ca(tmp_path):
    made = tls.ensure("0.0.0.0", ("lab.example.test",), directory=tmp_path)
    cert = x509.load_pem_x509_certificate(made.cert.read_bytes())
    ca = x509.load_pem_x509_certificate(made.ca.read_bytes())
    names, ips = _san(cert)
    assert names == ["builder.example.test", "builder", "lab.example.test"] and ips == [ipaddress.ip_address("10.0.0.5")]
    assert cert.issuer == ca.subject
    assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    assert cert.not_valid_after_utc > datetime.now(timezone.utc)
    ca.public_key().verify(cert.signature, cert.tbs_certificate_bytes, cert.signature_algorithm_parameters)


def test_the_server_certificate_is_made_again_for_a_new_name_or_near_its_end(tmp_path, monkeypatch):
    first = tls.ensure("0.0.0.0", directory=tmp_path).cert.read_bytes()
    assert tls.ensure("0.0.0.0", directory=tmp_path).cert.read_bytes() == first
    with_name = tls.ensure("0.0.0.0", ("other.example.test",), directory=tmp_path).cert.read_bytes()
    assert with_name != first and "other.example.test" in _san(x509.load_pem_x509_certificate(with_name))[0]
    assert tls.ensure("0.0.0.0", directory=tmp_path).cert.read_bytes() == with_name
    monkeypatch.setattr(tls, "RENEW_DAYS", tls.SERVER_DAYS + 1)
    assert tls.ensure("0.0.0.0", directory=tmp_path).cert.read_bytes() != with_name


def test_names_outside_the_cas_constraints_are_reported(tmp_path):
    tls.ensure("0.0.0.0", directory=tmp_path)
    made = tls.ensure("0.0.0.0", ("elsewhere.example.org", "192.168.1.9"), directory=tmp_path)
    assert made.uncovered == ["elsewhere.example.org", "192.168.1.9"] and not made.ca_created
    assert set(_san(x509.load_pem_x509_certificate(made.cert.read_bytes()))[0]) >= {"elsewhere.example.org"}


def test_ssl_context_serves_the_certificate_with_tls_1_2_or_newer(tmp_path):
    made = tls.ensure("0.0.0.0", directory=tmp_path)
    ctx = tls.ssl_context(made.cert, made.key)
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2
    assert ctx.verify_mode == ssl.CERT_NONE


def test_a_server_certificate_needs_a_name(tmp_path, monkeypatch):
    monkeypatch.setattr(tls.socket, "getfqdn", lambda *a: "localhost")
    monkeypatch.setattr(tls.socket, "gethostname", lambda *a: "localhost")
    monkeypatch.setattr(tls.socket, "getaddrinfo", lambda host, port: [])
    with pytest.raises(ValueError, match="at least one name"):
        tls.ensure("0.0.0.0", directory=tmp_path)
