"""Rate limiting + audit must key on the REAL client IP behind a proxy.

Regression for the deploy bug where the rate-limit middleware used
request.client.host directly: behind the reverse proxy the production checklist
mandates, that is the proxy's IP for every request, so all clients collapse into
one rate-limit bucket (false 429 lockouts). client_ip() must read
X-Forwarded-For when TRUST_PROXY_HEADERS is on — and must NOT trust it when off
(the header is forgeable on a direct-to-internet deployment).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from api.deps import client_ip


def _req(xff: str | None, peer: str = "10.0.0.9"):
    headers = {"x-forwarded-for": xff} if xff is not None else {}
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=peer))


def test_xff_used_when_proxy_trusted():
    with patch("api.deps.settings", SimpleNamespace(trust_proxy_headers=True)):
        assert client_ip(_req("203.0.113.7, 172.18.0.1")) == "203.0.113.7"


def test_xff_ignored_when_proxy_not_trusted():
    # Forgeable header must NOT override the socket peer when we're not behind
    # a trusted proxy — otherwise an attacker rotates it to dodge rate limits.
    with patch("api.deps.settings", SimpleNamespace(trust_proxy_headers=False)):
        assert client_ip(_req("1.2.3.4")) == "10.0.0.9"


def test_falls_back_to_peer_without_xff():
    with patch("api.deps.settings", SimpleNamespace(trust_proxy_headers=True)):
        assert client_ip(_req(None)) == "10.0.0.9"
