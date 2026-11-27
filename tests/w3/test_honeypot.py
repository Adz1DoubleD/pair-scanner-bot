from unittest.mock import MagicMock

import httpx
import pytest

import pair_scanner.w3.audit as audit


def client_returning(response=None, error=None):
    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None):
            if error:
                raise error
            return response

    return Client


def response(body, status=200):
    return httpx.Response(
        status, json=body, request=httpx.Request("GET", "https://api.honeypot.is")
    )


@pytest.mark.parametrize(
    "body, expected",
    [
        ({"summary": {"risk": "very_low", "riskLevel": 0}}, True),
        ({"summary": {"risk": "low", "riskLevel": 1}}, True),
        ({"summary": {"risk": "low", "riskLevel": 19}}, True),
        ({"summary": {"risk": "medium", "riskLevel": 20}}, False),
        ({"summary": {"risk": "very_high", "riskLevel": 85}}, False),
        ({"summary": {"risk": "unknown"}}, None),
        ({"summary": {"risk": "honeypot", "riskLevel": 95}}, False),
        (
            {
                "summary": {"risk": "low", "riskLevel": 1},
                "honeypotResult": {"isHoneypot": True},
            },
            False,
        ),
    ],
    ids=[
        "very-low",
        "low",
        "low-edge",
        "medium",
        "very-high",
        "unknown",
        "honeypot",
        "flagged-honeypot",
    ],
)
@pytest.mark.asyncio
async def test_check_honeypot_levels(monkeypatch, body, expected):
    monkeypatch.setattr(audit.httpx, "AsyncClient", client_returning(response(body)))

    assert await audit.check_honeypot("0xtoken") is expected


@pytest.mark.parametrize(
    "client",
    [
        client_returning(response({"error": "rate limited"}, status=429)),
        client_returning(error=httpx.ReadTimeout("slow")),
    ],
    ids=["rate-limited", "timeout"],
)
@pytest.mark.asyncio
async def test_check_honeypot_api_failure_is_unknown_not_honeypot(monkeypatch, client):
    reported = MagicMock()
    monkeypatch.setattr(audit.httpx, "AsyncClient", client)
    monkeypatch.setattr(audit.sentry_sdk, "capture_exception", reported)

    assert await audit.check_honeypot("0xtoken") is None
    reported.assert_called_once()
