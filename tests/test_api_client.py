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
"""Token refresh, retries and tool request shapes against a mocked Clio API."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from clio_mcp import api_client
from clio_mcp.api_client import ClioClient, ClioCredentials, ClioError
from clio_mcp.server import _credentials_var, create_mcp_server

TOKEN_ONLY = ClioCredentials(access_token="token-123")
REFRESHABLE = ClioCredentials(access_token="old-token", refresh_token="refresh", client_id="cid", client_secret="secret")
NEXT = "https://app.clio.com/api/v4/matters.json?limit=50&page_token=abc123"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(api_client.asyncio, "sleep", instant)


class Recorder:
    """Mock transport: replies by path (without the .json suffix)."""

    def __init__(self, routes=None, default=None):
        self.requests: list[httpx.Request] = []
        self.routes = routes or {}
        self.default = default if default is not None else {"data": []}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/api/v4/").removesuffix(".json")
        reply = self.routes.get(path, self.default)
        if callable(reply):
            return reply(request)
        return httpx.Response(200, json=reply)

    def calls(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == f"/api/v4/{path}.json"]


def body(request: httpx.Request):
    return json.loads(request.content) if request.content else None


async def _call(monkeypatch, recorder: Recorder, tool: str, args: dict, credentials: ClioCredentials = TOKEN_ONLY):
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    monkeypatch.setattr(api_client, "_get_shared_http_client", lambda: http)
    ctx = _credentials_var.set(credentials)
    try:
        content, _ = await create_mcp_server().call_tool(tool, args)
    finally:
        _credentials_var.reset(ctx)
    return json.loads(content[0].text)


def _client(recorder: Recorder, credentials: ClioCredentials) -> ClioClient:
    return ClioClient(credentials, httpx.AsyncClient(transport=httpx.MockTransport(recorder)))


def _token_route(request: httpx.Request) -> httpx.Response:
    form = parse_qs(request.content.decode())
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["refresh"]
    return httpx.Response(200, json={"access_token": "new-token", "token_type": "bearer", "expires_in": 2592000})


# ---- authentication --------------------------------------------------------


async def test_access_token_is_sent_as_bearer_and_lists_use_brackets():
    rec = Recorder()
    await _client(rec, TOKEN_ONLY).get("matters", {"ids": [1, 2], "billable": True})
    request = rec.requests[0]
    assert request.headers["Authorization"] == "Bearer token-123"
    assert request.url.path == "/api/v4/matters.json"
    assert request.url.params.get_list("ids[]") == ["1", "2"]
    assert request.url.params["billable"] == "true"


async def test_401_refreshes_once_and_retries():
    def matters(request: httpx.Request) -> httpx.Response:
        ok = request.headers["Authorization"] == "Bearer new-token"
        return httpx.Response(200 if ok else 401, json={"data": [{"id": 1}]} if ok else {"error": {"message": "expired"}})

    rec = Recorder({"oauth/token": _token_route, "matters": matters})
    rec.routes["/oauth/token"] = _token_route
    client = _client(rec, REFRESHABLE)
    assert await client.get("matters") == {"data": [{"id": 1}]}
    token_requests = [r for r in rec.requests if r.url.path == "/oauth/token"]
    assert len(token_requests) == 1
    assert str(token_requests[0].url) == "https://app.clio.com/oauth/token"


async def test_refresh_token_alone_obtains_an_access_token_first():
    creds = ClioCredentials(refresh_token="refresh", client_id="cid", client_secret="secret", region="ca")
    rec = Recorder({"/oauth/token": _token_route})
    await _client(rec, creds).get("matters")
    assert str(rec.requests[0].url) == "https://ca.app.clio.com/oauth/token"
    assert rec.requests[1].headers["Authorization"] == "Bearer new-token"


async def test_401_without_refresh_token_is_unauthorized():
    rec = Recorder({"matters": lambda r: httpx.Response(401, json={"error": {"message": "invalid token"}})})
    with pytest.raises(ClioError) as err:
        await _client(rec, TOKEN_ONLY).get("matters")
    assert err.value.status_code == 401
    assert "invalid token" in err.value.message
    assert len(rec.requests) == 1


async def test_rejected_refresh_is_unauthorized_without_leaking_secrets(monkeypatch):
    rec = Recorder(
        {
            "users/who_am_i": lambda r: httpx.Response(401, json={}),
            "/oauth/token": lambda r: httpx.Response(400, json={"error": "invalid_grant"}),
        }
    )
    creds = ClioCredentials(
        access_token="tok-value-1", refresh_token="ref-value-2", client_id="cid-value-3", client_secret="sec-value-4"
    )
    result = await _call(monkeypatch, rec, "clio_status", {}, creds)
    assert result["error"]["code"] == "unauthorized"
    text = json.dumps(result)
    assert not any(value in text for value in ("tok-value-1", "ref-value-2", "cid-value-3", "sec-value-4"))


# ---- retries ---------------------------------------------------------------


async def test_get_retries_5xx_then_succeeds():
    replies = iter([httpx.Response(503), httpx.Response(200, json={"data": []})])
    rec = Recorder({"matters": lambda r: next(replies)})
    assert await _client(rec, TOKEN_ONLY).get("matters") == {"data": []}
    assert len(rec.requests) == 2


async def test_post_is_not_retried_on_5xx():
    rec = Recorder({"matters": lambda r: httpx.Response(500, json={"error": {"message": "boom"}})})
    with pytest.raises(ClioError) as err:
        await _client(rec, TOKEN_ONLY).post("matters", {"description": "x"})
    assert err.value.status_code == 500
    assert len(rec.requests) == 1


async def test_post_is_retried_on_429():
    replies = iter([httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(201, json={"data": {"id": 9}})])
    rec = Recorder({"matters": lambda r: next(replies)})
    assert await _client(rec, TOKEN_ONLY).post("matters", {}) == {"data": {"id": 9}}
    assert len(rec.requests) == 2


async def test_validation_errors_name_the_fields(monkeypatch):
    error = {"error": {"type": "ValidationError", "message": "Validation failed", "fields": {"date": ["can't be blank"]}}}
    rec = Recorder({"activities": lambda r: httpx.Response(422, json=error)})
    result = await _call(monkeypatch, rec, "clio_activities_create", {"date": "2026-06-01", "type": "TimeEntry"})
    assert result["error"]["code"] == "invalid_argument"
    assert "date can't be blank" in result["error"]["message"]


# ---- tools -----------------------------------------------------------------


async def test_list_uses_default_fields_and_returns_next_page_token(monkeypatch):
    rec = Recorder({"matters": {"data": [{"id": 1}], "meta": {"records": 120, "paging": {"next": NEXT}}}})
    result = await _call(monkeypatch, rec, "clio_matters_list", {"status": "Open, pending"})
    params = rec.requests[0].url.params
    assert "client{id,name}" in params["fields"]
    assert params["limit"] == "50"
    assert params["status"] == "open,pending"
    assert result == {"items": [{"id": 1}], "count": 1, "total": 120, "has_more": True, "next_page_token": "abc123"}


async def test_list_passes_custom_fields_and_page_token(monkeypatch):
    rec = Recorder()
    await _call(monkeypatch, rec, "clio_contacts_list", {"fields": "id,name", "page_token": "abc", "limit": 5, "initial": "s"})
    params = rec.requests[0].url.params
    assert (params["fields"], params["page_token"], params["limit"], params["initial"]) == ("id,name", "abc", "5", "S")


async def test_bad_matter_status_is_rejected(monkeypatch):
    rec = Recorder()
    result = await _call(monkeypatch, rec, "clio_matters_list", {"status": "archived"})
    assert result["error"]["code"] == "invalid_argument"
    assert rec.requests == []


async def test_get_unwraps_data(monkeypatch):
    rec = Recorder({"matters/42": {"data": {"id": 42, "display_number": "00042"}}})
    result = await _call(monkeypatch, rec, "clio_matters_get", {"id": 42})
    assert result == {"id": 42, "display_number": "00042"}
    assert "responsible_staff{id,name}" in rec.requests[0].url.params["fields"]


async def test_matter_create_resolves_a_unique_client_name(monkeypatch):
    rec = Recorder(
        {
            "contacts": {"data": [{"id": 7, "name": "Example Holdings"}, {"id": 8, "name": "Example Holdings Ltd"}]},
            "matters": {"data": {"id": 99}},
        }
    )
    result = await _call(monkeypatch, rec, "clio_matters_create", {"description": "Lease review", "client_name": "example holdings"})
    sent = body(rec.calls("matters")[0])
    assert sent == {"data": {"description": "Lease review", "client": {"id": 7}}}
    assert result == {"id": 99}


async def test_matter_create_with_ambiguous_client_asks_for_an_id(monkeypatch):
    rec = Recorder({"contacts": {"data": [{"id": 7, "name": "Example A"}, {"id": 8, "name": "Example B"}]}})
    result = await _call(monkeypatch, rec, "clio_matters_create", {"description": "x", "client_name": "Example"})
    assert result["error"]["code"] == "invalid_argument"
    assert "id 7" in result["error"]["message"] and "id 8" in result["error"]["message"]
    assert rec.calls("matters") == []


async def test_matter_update_sends_patch_with_only_given_fields(monkeypatch):
    rec = Recorder({"matters/5": {"data": {"id": 5}}})
    await _call(monkeypatch, rec, "clio_matters_update", {"id": 5, "status": "closed", "close_date": "2026-06-30"})
    request = rec.calls("matters/5")[0]
    assert request.method == "PATCH"
    assert body(request) == {"data": {"status": "closed", "close_date": "2026-06-30"}}


async def test_update_without_changes_is_rejected(monkeypatch):
    rec = Recorder()
    result = await _call(monkeypatch, rec, "clio_tasks_update", {"id": 5})
    assert result["error"]["code"] == "invalid_argument"
    assert rec.requests == []


async def test_contact_create_body(monkeypatch):
    rec = Recorder({"contacts": {"data": {"id": 3}}})
    args = {"name": "Jane Doe", "type": "Person", "first_name": "Jane", "last_name": "Doe", "email": "jane@example.com"}
    await _call(monkeypatch, rec, "clio_contacts_create", args)
    assert body(rec.calls("contacts")[0])["data"] == {
        "name": "Jane Doe",
        "type": "Person",
        "first_name": "Jane",
        "last_name": "Doe",
        "email_addresses": [{"name": "Work", "address": "jane@example.com", "default_email": True}],
    }


async def test_contact_update_replaces_the_primary_email_in_place(monkeypatch):
    current = {"data": {"id": 3, "email_addresses": [{"id": 10, "default_email": False}, {"id": 11, "default_email": True}]}}

    def contact(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=current if request.method == "GET" else {"data": {"id": 3}})

    rec = Recorder({"contacts/3": contact})
    await _call(monkeypatch, rec, "clio_contacts_update", {"id": 3, "email": "new@example.com"})
    patch = [r for r in rec.calls("contacts/3") if r.method == "PATCH"][0]
    assert body(patch)["data"]["email_addresses"] == [
        {"id": 11, "name": "Work", "address": "new@example.com", "default_email": True}
    ]


async def test_task_create_defaults_assignee_type(monkeypatch):
    rec = Recorder({"tasks": {"data": {"id": 4}}})
    await _call(monkeypatch, rec, "clio_tasks_create", {"name": "File motion", "description": "By Friday", "assignee_id": 12})
    assert body(rec.calls("tasks")[0])["data"]["assignee"] == {"id": 12, "type": "User"}


async def test_task_list_sends_assignee_type_with_assignee(monkeypatch):
    rec = Recorder()
    await _call(monkeypatch, rec, "clio_tasks_list", {"assignee_id": 12})
    assert rec.requests[0].url.params["assignee_type"] == "user"


async def test_activity_create_body(monkeypatch):
    rec = Recorder({"activities": {"data": {"id": 8}}})
    args = {"date": "2026-06-01", "type": "TimeEntry", "matter_id": 5, "quantity": 5400, "price": 250, "note": "Call"}
    await _call(monkeypatch, rec, "clio_activities_create", args)
    assert body(rec.calls("activities")[0])["data"] == {
        "date": "2026-06-01",
        "type": "TimeEntry",
        "matter": {"id": 5},
        "note": "Call",
        "price": 250,
        "quantity": 5400,
    }


async def test_calendar_from_is_sent_under_its_api_name(monkeypatch):
    rec = Recorder()
    await _call(monkeypatch, rec, "clio_calendar_entries_list", {"from": "2026-06-01T00:00:00Z", "to": "2026-06-30T00:00:00Z"})
    params = rec.requests[0].url.params
    assert params["from"] == "2026-06-01T00:00:00Z"
    assert params["to"] == "2026-06-30T00:00:00Z"


async def test_status_reports_user_and_region(monkeypatch):
    rec = Recorder({"users/who_am_i": {"data": {"id": 1, "name": "Jane Doe"}}})
    result = await _call(monkeypatch, rec, "clio_status", {}, REFRESHABLE)
    assert result["connected"] is True
    assert result["region"] == "us"
    assert result["canAutoRefresh"] is True
    assert result["user"] == {"id": 1, "name": "Jane Doe"}
