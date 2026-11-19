"""LAN helpers for phone pairing (SPEC §9, §10). Stdlib only, OS-independent."""

from __future__ import annotations

import ipaddress
import socket

# Peers whose requests are trusted without a device token. Starlette's TestClient reports its
# peer as "testclient"; a real socket peer is always an IP address, so it can never match.
_LOCAL_NAMES = {"testclient"}


def lan_ip() -> str:
    """Best-guess LAN address of this machine.

    UDP "connect" only selects a route; no packet is sent. Falls back to loopback when there is
    no network (the QR then only works on this machine)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.254.254.254", 1))
        ip = str(s.getsockname()[0])
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def is_loopback(host: str | None) -> bool:
    if host is None:
        return False
    if host in _LOCAL_NAMES:
        return True
    try:
        addr = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_loopback


def host_name(host_header: str) -> str:
    """Strip the port from a Host header ("[::1]:8765" -> "[::1]", "a:1" -> "a")."""
    h = host_header.strip().lower()
    if h.startswith("["):
        return h[: h.find("]") + 1] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h
