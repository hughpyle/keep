"""Shared URL validation for provider base URLs — SSRF prevention.

Reuses the same ipaddress-based logic as documents.py _is_private_url()
but exposes it as a reusable function for provider configuration.
"""

import ipaddress
import socket
from urllib.parse import urlparse


def _validate_provider_url(url: str, **_kwargs) -> str:
    """Validate a provider base URL against SSRF risks.

    Checks:
    - Scheme must be http or https.
    - Link-local addresses (169.254.x.x) and cloud metadata endpoints
      are blocked — these are the primary SSRF targets.
    - Private/loopback IPs (localhost, 10.x, 192.168.x) are allowed
      since local LLM servers (Ollama, vLLM, LocalAI) are common.

    Returns:
        The validated URL (unchanged).

    Raises:
        ValueError: If the URL fails validation.
    """
    parsed = urlparse(url)

    # Scheme check
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"Provider URL must use http or https scheme, got: {parsed.scheme!r}"
        )

    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"Provider URL has no hostname: {url}")

    # Block cloud metadata endpoints by hostname
    _METADATA_HOSTS = {"metadata.google.internal", "169.254.169.254"}
    if hostname.lower() in _METADATA_HOSTS:
        raise ValueError(f"Provider URL targets a blocked metadata endpoint: {hostname}")

    # Block cloud metadata by IP — link-local (169.254.x.x, fe80::) covers
    # IPv4 IMDS.  AWS also exposes IMDS on fd00:ec2::254 (IPv6 unique-local),
    # so we block that specific address too.
    _METADATA_ADDRS = {
        ipaddress.ip_address("169.254.169.254"),     # AWS/GCP IPv4 IMDS
        ipaddress.ip_address("fd00:ec2::254"),        # AWS IPv6 IMDS
        ipaddress.ip_address("168.63.129.16"),        # Azure wireserver (IMDS/DNS/DHCP)
    }

    def _check_addr(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
        if addr.is_link_local:
            raise ValueError(
                f"Provider URL resolves to link-local address: {addr}"
            )
        if addr in _METADATA_ADDRS:
            raise ValueError(
                f"Provider URL resolves to cloud metadata address: {addr}"
            )

    # Check if hostname is an IP literal
    try:
        addr = ipaddress.ip_address(hostname)
        _check_addr(addr)
        return url
    except ValueError:
        pass  # Not an IP literal — resolve via DNS

    try:
        for _, _, _, _, sockaddr in socket.getaddrinfo(hostname, None):
            addr = ipaddress.ip_address(sockaddr[0])
            _check_addr(addr)
    except socket.gaierror:
        pass  # DNS failure will surface when the provider tries to connect

    return url
