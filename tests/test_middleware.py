# Copyright 2026 MSPbots.ai
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Credential middleware: 401/400 on bad headers, per-request isolation."""

import asyncio

import pytest
from starlette.testclient import TestClient

from clio_mcp.__main__ import build_http_app
from clio_mcp.server import (
    GatewayCredentialsMiddleware,
    _credentials_var,
    create_mcp_server,
    get_client_from_context,
)

MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
REFRESH = {"X-Clio-Refresh-Token": "refresh", "X-Clio-Client-Id": "client-id", "X-Clio-Client-Secret": "client-secret"}


def _client() -> TestClient:
    return TestClient(build_http_app(create_mcp_server()))


def test_health_is_local():
    with _client() as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Clio-Client-Id": "client-id", "X-Clio-Client-Secret": "client-secret"},
        {"X-Clio-Refresh-Token": "refresh", "X-Clio-Client-Id": "client-id"},
        {"X-Clio-Access-Token": "Bearer "},
        {"X-Clio-Access-Token": "${CLIO_ACCESS_TOKEN}"},
    ],
)
def test_missing_credentials_return_401(headers):
    with _client() as client:
        resp = client.post("/mcp", json=LIST_TOOLS, headers=MCP_HEADERS | headers)
    assert resp.status_code == 401
    assert ["X-Clio-Access-Token"] in resp.json()["required_one_of"]


def test_unknown_region_returns_400():
    with _client() as client:
        resp = client.post("/mcp", json=LIST_TOOLS, headers=MCP_HEADERS | {"X-Clio-Access-Token": "t", "X-Clio-Region": "mars"})
    assert resp.status_code == 400


@pytest.mark.parametrize("auth", [{"X-Clio-Access-Token": "token"}, REFRESH])
def test_tools_list_over_http(auth):
    with _client() as client:
        resp = client.post("/mcp", json=LIST_TOOLS, headers=MCP_HEADERS | auth)
    assert resp.status_code == 200
    for name in ("clio_navigate", "clio_matters_list", "clio_tasks_update", "clio_bills_get"):
        assert name in resp.text


def _run_middleware(headers: list[tuple[bytes, bytes]]) -> dict:
    seen: dict = {}

    async def app(scope, receive, send):
        seen["credentials"] = _credentials_var.get()
        seen["client"] = get_client_from_context()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    asyncio.run(GatewayCredentialsMiddleware(app)({"type": "http", "path": "/mcp", "headers": headers}, receive, send))
    return seen


def test_headers_reach_request_context_and_are_reset():
    seen = _run_middleware(
        [
            (b"x-clio-access-token", b"Bearer token-123"),
            (b"x-clio-refresh-token", b" refresh "),
            (b"x-clio-client-id", b"client-id"),
            (b"x-clio-client-secret", b"client-secret"),
            (b"x-clio-region", b"EU"),
        ]
    )
    creds = seen["credentials"]
    assert creds.access_token == "token-123"
    assert creds.refresh_token == "refresh"
    assert creds.can_refresh is True
    assert seen["client"].base_url == "https://eu.app.clio.com/api/v4"
    assert _credentials_var.get() is None


def test_region_defaults_to_us():
    seen = _run_middleware([(b"x-clio-access-token", b"token")])
    assert seen["client"].base_url == "https://app.clio.com/api/v4"
    assert seen["credentials"].can_refresh is False


def test_concurrent_requests_do_not_share_credentials():
    observed: list[tuple[str, str]] = []

    async def app(scope, receive, send):
        before = _credentials_var.get().access_token
        await asyncio.sleep(0.01)
        observed.append((before, _credentials_var.get().access_token))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    middleware = GatewayCredentialsMiddleware(app)

    def scope(token: bytes) -> dict:
        return {"type": "http", "path": "/mcp", "headers": [(b"x-clio-access-token", token)]}

    async def both():
        await asyncio.gather(middleware(scope(b"tenant-a"), receive, send), middleware(scope(b"tenant-b"), receive, send))

    asyncio.run(both())
    assert sorted(observed) == [("tenant-a", "tenant-a"), ("tenant-b", "tenant-b")]


def test_credentials_repr_hides_secrets():
    seen = _run_middleware(
        [
            (b"x-clio-access-token", b"very-secret-token"),
            (b"x-clio-refresh-token", b"very-secret-refresh"),
            (b"x-clio-client-id", b"c"),
            (b"x-clio-client-secret", b"very-secret-value"),
        ]
    )
    text = repr(seen["credentials"])
    assert "very-secret" not in text
