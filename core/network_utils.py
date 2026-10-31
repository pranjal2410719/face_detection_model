"""
Network & Certificate Utilities for Real-Time Wi-Fi Face Recognition System.
Provides dynamic IP discovery, automated multi-SAN SSL certificate generation,
and URL helpers for cross-device LAN/Wi-Fi operation.
"""
import os
import socket
import subprocess
import datetime
import ipaddress
import logging
from pathlib import Path
from typing import List, Dict, Any, Tuple

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

logger = logging.getLogger("NetworkUtils")

def get_lan_ips() -> List[str]:
    """
    Detects all active IPv4 addresses across network interfaces (excluding loopback).
    Prioritizes the primary route interface (e.g. Wi-Fi or Ethernet).
    """
    detected_ips: List[str] = []

    # 1. Primary outbound interface detection
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        # Does not send actual packets; resolves route to public IP
        s.connect(("1.1.1.1", 80))
        primary = s.getsockname()[0]
        s.close()
        if primary and not primary.startswith("127."):
            detected_ips.append(primary)
    except Exception:
        pass

    # 2. Linux 'ip -4 addr' discovery for all other interfaces (Wi-Fi, Hotspot, Ethernet)
    try:
        out = subprocess.check_output(["ip", "-4", "addr", "show"], stderr=subprocess.DEVNULL).decode()
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                ip = line.split()[1].split("/")[0]
                if not ip.startswith("127.") and ip not in detected_ips:
                    detected_ips.append(ip)
    except Exception:
        pass

    # 3. Fallback hostname resolution
    if not detected_ips:
        try:
            host_ips = socket.gethostbyname_ex(socket.gethostname())[2]
            for ip in host_ips:
                if not ip.startswith("127.") and ip not in detected_ips:
                    detected_ips.append(ip)
        except Exception:
            pass

    return detected_ips if detected_ips else ["127.0.0.1"]


def get_network_info(http_port: int = 8500, https_port: int = 8443) -> Dict[str, Any]:
    """
    Returns full network connectivity info and ready-to-use URLs for LAN/Wi-Fi devices.
    """
    ips = get_lan_ips()
    primary_ip = ips[0] if ips else "127.0.0.1"

    network_list = []
    for ip in ips:
        network_list.append({
            "ip": ip,
            "is_primary": (ip == primary_ip),
            "camera_https": f"https://{ip}:{https_port}",
            "camera_http": f"http://{ip}:{http_port}",
            "dashboard_https": f"https://{ip}:{https_port}/dashboard",
            "dashboard_http": f"http://{ip}:{http_port}/dashboard",
        })

    return {
        "primary_ip": primary_ip,
        "all_ips": ips,
        "http_port": http_port,
        "https_port": https_port,
        "networks": network_list,
        "recommended_camera_url": f"https://{primary_ip}:{https_port}",
        "recommended_dashboard_url": f"http://{primary_ip}:{http_port}/dashboard"
    }


def ensure_ssl_certificates(cert_dir: Path, force_renew: bool = False) -> Tuple[Path, Path]:
    """
    Ensures SSL cert and key exist and include all active LAN/Wi-Fi IPs in SAN.
    Automatically regenerates certificate if new IPs are detected or expired.
    """
    cert_dir = Path(cert_dir)
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / "cert.pem"
    key_path = cert_dir / "key.pem"

    active_ips = get_lan_ips()
    needs_generation = force_renew or (not cert_path.exists()) or (not key_path.exists())

    if not needs_generation:
        # Check existing certificate SAN to ensure all active IPs are covered
        try:
            cert_bytes = cert_path.read_bytes()
            cert_obj = x509.load_pem_x509_certificate(cert_bytes)
            
            # Check expiration (if valid for at least 7 more days)
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            if cert_obj.not_valid_after_utc < now_utc + datetime.timedelta(days=7):
                needs_generation = True
            else:
                # Extract existing SAN IPs
                existing_san_ips = set()
                try:
                    san_ext = cert_obj.extensions.get_extension_for_oid(x509.ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
                    for item in san_ext.value:
                        if isinstance(item, x509.IPAddress):
                            existing_san_ips.add(str(item.value))
                except Exception:
                    pass

                # If any active IP is missing from certificate SAN, regenerate
                for ip in active_ips:
                    if ip not in existing_san_ips and not ip.startswith("127."):
                        logger.info(f"New LAN IP {ip} not in existing SSL certificate SAN. Updating certificate...")
                        needs_generation = True
                        break
        except Exception as e:
            logger.warning(f"Error inspecting existing certificate: {e}. Regenerating...")
            needs_generation = True

    if needs_generation:
        logger.info(f"Generating new multi-SAN SSL Certificate for active LAN/Wi-Fi IPs: {active_ips}...")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "NetCam Wi-Fi Node"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Face Recognition Network"),
        ])

        san_entries = [
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ]

        hostname = socket.gethostname()
        if hostname:
            try:
                san_entries.append(x509.DNSName(hostname))
            except Exception:
                pass

        for ip_str in active_ips:
            try:
                ip_obj = ipaddress.IPv4Address(ip_str)
                if x509.IPAddress(ip_obj) not in san_entries:
                    san_entries.append(x509.IPAddress(ip_obj))
            except Exception:
                pass

        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=730))  # 2 years
            .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )

        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))
        logger.info(f"SSL certificate generated at: {cert_path}")

    return cert_path, key_path


if __name__ == "__main__":
    ips = get_lan_ips()
    print("Detected LAN IPs:", ips)
    info = get_network_info()
    print("Network info:", info)
    p_cert, p_key = ensure_ssl_certificates(Path("certs"))
    print("Certs verified:", p_cert, p_key)
