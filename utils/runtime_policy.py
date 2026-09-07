"""
Central runtime policy helpers for local-only operation.

This module provides:
- consistent environment-flag parsing
- process-level network egress guard (deny-by-default in local-only mode)
"""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import Any, Optional


class OutboundNetworkBlockedError(RuntimeError):
    """Raised when local-only runtime blocks outbound network traffic."""


def parse_bool_env(var_name: str, default: str = "0") -> bool:
    value = os.getenv(var_name, default)
    return value.strip().lower() not in {"0", "false", "no", "off"}


def is_local_only_mode() -> bool:
    return parse_bool_env("APP_LOCAL_ONLY", "0")


def _is_loopback_host(host: str) -> bool:
    if not host:
        return False
    normalized = host.strip().lower().strip("[]")
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return True

    try:
        ip = ipaddress.ip_address(normalized)
        return ip.is_loopback
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            resolved = info[4][0]
            ip = ipaddress.ip_address(resolved)
            if not ip.is_loopback:
                return False
        return bool(infos)
    except Exception:
        return False


def _extract_host_from_url(url: str) -> Optional[str]:
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        return parsed.hostname
    except Exception:
        return None


def apply_network_guards(local_only: Optional[bool] = None) -> None:
    """Apply deny-by-default egress guards for local-only runtime.

    When enabled, outbound network calls are blocked unless destination is loopback.
    The patching is idempotent for a process.
    """

    enabled = is_local_only_mode() if local_only is None else local_only
    if not enabled:
        return

    if os.getenv("APP_NETWORK_GUARD_ACTIVE", "0") == "1":
        return

    def _deny(host: Optional[str], proto: str) -> None:
        if host and _is_loopback_host(host):
            return
        raise OutboundNetworkBlockedError(
            f"APP_LOCAL_ONLY active: outbound {proto} network request blocked"
        )

    # requests
    try:
        import requests

        _orig_requests = requests.sessions.Session.request

        def _guarded_requests(self: Any, method: str, url: str, *args: Any, **kwargs: Any):
            _deny(_extract_host_from_url(url), "HTTP")
            return _orig_requests(self, method, url, *args, **kwargs)

        requests.sessions.Session.request = _guarded_requests  # type: ignore[assignment]
    except Exception:
        pass

    # httpx
    try:
        import httpx

        _orig_client_req = httpx.Client.request

        def _guarded_httpx_client(self: Any, method: str, url: Any, *args: Any, **kwargs: Any):
            _deny(_extract_host_from_url(str(url)), "HTTP")
            return _orig_client_req(self, method, url, *args, **kwargs)

        httpx.Client.request = _guarded_httpx_client  # type: ignore[assignment]

        _orig_async_req = httpx.AsyncClient.request

        async def _guarded_httpx_async(self: Any, method: str, url: Any, *args: Any, **kwargs: Any):
            _deny(_extract_host_from_url(str(url)), "HTTP")
            return await _orig_async_req(self, method, url, *args, **kwargs)

        httpx.AsyncClient.request = _guarded_httpx_async  # type: ignore[assignment]
    except Exception:
        pass

    # aiohttp
    try:
        import aiohttp

        _orig_aiohttp_request = aiohttp.ClientSession._request

        async def _guarded_aiohttp(self: Any, method: str, url: Any, *args: Any, **kwargs: Any):
            _deny(_extract_host_from_url(str(url)), "HTTP")
            return await _orig_aiohttp_request(self, method, url, *args, **kwargs)

        aiohttp.ClientSession._request = _guarded_aiohttp  # type: ignore[assignment]
    except Exception:
        pass

    # urllib
    try:
        import urllib.request

        _orig_urlopen = urllib.request.urlopen

        def _guarded_urlopen(url: Any, *args: Any, **kwargs: Any):
            target = url.full_url if hasattr(url, "full_url") else str(url)
            _deny(_extract_host_from_url(target), "HTTP")
            return _orig_urlopen(url, *args, **kwargs)

        urllib.request.urlopen = _guarded_urlopen  # type: ignore[assignment]
    except Exception:
        pass

    # socket (lowest level guard)
    try:
        _orig_create_conn = socket.create_connection

        def _guarded_create_connection(address: Any, *args: Any, **kwargs: Any):
            host = address[0] if isinstance(address, tuple) and address else None
            _deny(host, "TCP")
            return _orig_create_conn(address, *args, **kwargs)

        socket.create_connection = _guarded_create_connection  # type: ignore[assignment]

        _orig_socket_connect = socket.socket.connect

        def _guarded_socket_connect(self: Any, address: Any):
            host = address[0] if isinstance(address, tuple) and address else None
            _deny(host, "TCP")
            return _orig_socket_connect(self, address)

        socket.socket.connect = _guarded_socket_connect  # type: ignore[assignment]
    except Exception:
        pass

    os.environ["APP_NETWORK_GUARD_ACTIVE"] = "1"
