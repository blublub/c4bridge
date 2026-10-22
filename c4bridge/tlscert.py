"""HTTPS für die Bridge: selbstsigniertes Zertifikat erzeugen und einen zweiten (TLS-)Port neben HTTP öffnen.

Warum: Browser geben das Mikrofon (getUserMedia) nur in einem „sicheren Kontext“ frei – https:// oder localhost.
Vom Handy/Tablet im LAN (http://192.168.x.y:8080) fehlt navigator.mediaDevices daher komplett (INSTALL.md §Mikrofone).
HTTP bleibt bestehen (c4voice und die Werkzeuge reden über http://127.0.0.1:8080), HTTPS kommt als zweiter Port
(BRIDGE_SSL_PORT, z. B. 8443) in DEMSELBEN Prozess dazu – gleiche App, gleicher Zustand.

Zertifikat: ~/.config/c4bridge/bridge.crt + bridge.key (selbstsigniert, 10 Jahre, SAN = alle lokalen IPv4-Adressen,
Rechnername, localhost). Browser warnen einmal („nicht sicher“) – für das Mikrofon reicht das Bestätigen. Ohne Warnung
geht es nur mit einer eigenen CA (mkcert) oder einem öffentlichen Zertifikat (Let's Encrypt, DNS-Challenge) – siehe
INSTALL.md. Liegt dort ein eigenes Zertifikat (BRIDGE_SSL_CERT/BRIDGE_SSL_KEY), wird es unverändert benutzt.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import socket
from pathlib import Path

from . import logbus
from .config import DEFAULT_CONFIG

CERT_DIR = DEFAULT_CONFIG.parent
CERT = CERT_DIR / "bridge.crt"
KEY = CERT_DIR / "bridge.key"


def local_ips() -> list[str]:
    """Alle IPv4-Adressen dieses Rechners (ohne 127.0.0.1) – kommen als SAN ins Zertifikat."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except socket.gaierror:
        pass
    try:  # die Adresse, über die der Rechner „nach draußen“ geht (zuverlässiger als getaddrinfo)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


def ensure(cert: Path = CERT, key: Path = KEY) -> tuple[Path, Path]:
    """Zertifikat + Schlüssel anlegen, falls sie fehlen (oder das Zertifikat die aktuelle IP nicht mehr enthält)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    ips, host = local_ips(), socket.gethostname()
    if cert.is_file() and key.is_file():
        try:
            c = x509.load_pem_x509_certificate(cert.read_bytes())
            san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            have = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
            if set(ips) <= have and c.not_valid_after_utc > dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30):
                return cert, key
            logbus.info("tls", f"Zertifikat enthält {sorted(set(ips) - have) or 'Ablauf'} nicht mehr – neu erzeugen")
        except Exception as e:  # noqa: BLE001
            logbus.warn("tls", f"vorhandenes Zertifikat nicht lesbar ({e}) – neu erzeugen")
    cert.parent.mkdir(parents=True, exist_ok=True)
    k = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"c4bridge {host}"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "c4bridge (selbstsigniert)")])
    now = dt.datetime.now(dt.timezone.utc)
    san = [x509.DNSName("localhost"), x509.DNSName(host), x509.DNSName(f"{host}.local"),
           x509.IPAddress(ipaddress.ip_address("127.0.0.1"))] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
         .not_valid_after(now + dt.timedelta(days=3650))
         .add_extension(x509.SubjectAlternativeName(san), critical=False)
         .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
         .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
         .sign(k, hashes.SHA256()))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    try:
        key.chmod(0o600)
    except OSError:
        pass
    logbus.info("tls", f"Selbstsigniertes Zertifikat erzeugt: {cert} (SAN {', '.join([host, *ips])}, 10 Jahre)")
    return cert, key


async def serve(app, host: str, port: int, cert: Path, key: Path) -> None:
    """Zweiten uvicorn-Server (TLS) für dieselbe App starten – ohne Lifespan (der läuft schon im Hauptserver)."""
    import uvicorn
    cfg = uvicorn.Config(app, host=host, port=port, ssl_certfile=str(cert), ssl_keyfile=str(key),
                         lifespan="off", access_log=False, log_level="warning")
    server = uvicorn.Server(cfg)
    server.install_signal_handlers = lambda: None       # Signale gehören dem Hauptserver
    logbus.info("tls", f"HTTPS zusätzlich auf https://{host}:{port} (Zertifikat {cert.name})")
    try:
        await server.serve()
    except asyncio.CancelledError:
        server.should_exit = True
        raise
    except Exception as e:  # noqa: BLE001
        logbus.warn("tls", f"HTTPS-Port {port} nicht gestartet: {type(e).__name__}: {e}")
