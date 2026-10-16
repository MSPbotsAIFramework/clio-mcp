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
"""FastMCP factory and the per-request credential middleware."""

import contextvars
import re
import uuid
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .api_client import ClioClient, ClioCredentials
from .config import DEFAULT_REGION, REGION_HOSTS, Settings

HEADER_ACCESS_TOKEN = "X-Clio-Access-Token"
HEADER_REFRESH_TOKEN = "X-Clio-Refresh-Token"
HEADER_CLIENT_ID = "X-Clio-Client-Id"
HEADER_CLIENT_SECRET = "X-Clio-Client-Secret"
HEADER_REGION = "X-Clio-Region"

_PLACEHOLDER = re.compile(r"^\$\{.*\}$")
_BEARER = re.compile(r"^bearer(\s+|$)", re.IGNORECASE)

# Per-request isolation: the middleware sets these for the duration of one
# HTTP request and resets them in `finally`. Nothing credential-related is
# ever stored at module level.
_credentials_var: contextvars.ContextVar[ClioCredentials | None] = contextvars.ContextVar(
    "clio_credentials", default=None
)
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("clio_request_id", default="-")

INSTRUCTIONS = (
    "Clio Manage is practice-management software for law firms. This server "
    "reads and, on request, writes one Clio account: matters (cases/files), "
    "contacts (people and companies; clients are contacts), activities (time "
    "and expense entries), tasks, communications (logged emails and calls), "
    "document metadata, calendar entries and bills.\n"
    "Usage:\n"
    "- Records are referenced by numeric Clio ids. Find them with the *_list "
    "tools (query does a wildcard search), then use *_get for one record.\n"
    "- List results return {items, count, total, has_more, next_page_token}; "
    "pass next_page_token as page_token for the next page.\n"
    "- Every list/get tool returns a useful default field set. Pass fields to "
    "choose fields yourself, with nested syntax like client{id,name}.\n"
    "- Typical flow: clio_contacts_list(query=name) -> clio_matters_list"
    "(client_id) -> clio_activities_list / clio_tasks_list / clio_bills_list"
    "(matter_id).\n"
    "- clio_status checks the connection and shows the signed-in user.\n"
    "Tools that create or update matters, contacts, activities or tasks change "
    "the firm's live records (activities can be billed to clients): call them "
    "only when the user explicitly asks, with the exact values given. Document "
    "contents, deletes and billing changes are not available."
)


def get_client_from_context() -> ClioClient | None:
    """The client for the current request, or None outside a request."""
    credentials = _credentials_var.get()
    if credentials is None:
        return None
    return ClioClient(credentials)


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or _PLACEHOLDER.match(text):
        return None
    return text


class GatewayCredentialsMiddleware:
    """Reads the Clio headers on /mcp requests into the request context.

    Needs X-Clio-Access-Token, or X-Clio-Refresh-Token + X-Clio-Client-Id +
    X-Clio-Client-Secret to obtain one; otherwise 401.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)

        def header(name: str) -> str | None:
            return _clean(request.headers.get(name))

        access_token = header(HEADER_ACCESS_TOKEN)
        if access_token:
            access_token = _clean(_BEARER.sub("", access_token))
        refresh_token, client_id, client_secret = header(HEADER_REFRESH_TOKEN), header(HEADER_CLIENT_ID), header(HEADER_CLIENT_SECRET)
        region = (header(HEADER_REGION) or DEFAULT_REGION).lower()

        if region not in REGION_HOSTS:
            await JSONResponse(
                {"error": "Invalid region", "message": f"{HEADER_REGION} must be one of: {', '.join(REGION_HOSTS)}"},
                status_code=400,
            )(scope, receive, send)
            return
        if not (access_token or (refresh_token and client_id and client_secret)):
            await JSONResponse(
                {
                    "error": "Missing credentials",
                    "message": f"Send {HEADER_ACCESS_TOKEN}, or {HEADER_REFRESH_TOKEN} + {HEADER_CLIENT_ID} + "
                    f"{HEADER_CLIENT_SECRET} so the server can obtain one",
                    "required_one_of": [[HEADER_ACCESS_TOKEN], [HEADER_REFRESH_TOKEN, HEADER_CLIENT_ID, HEADER_CLIENT_SECRET]],
                    "optional_headers": [HEADER_REGION],
                },
                status_code=401,
            )(scope, receive, send)
            return

        credentials = ClioCredentials(
            access_token=access_token,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            region=region,
        )
        request_id = (request.headers.get("x-request-id") or "").strip()[:64] or uuid.uuid4().hex[:12]
        creds_token = _credentials_var.set(credentials)
        rid_token = request_id_var.set(request_id)
        try:
            await self.app(scope, receive, send)
        finally:
            _credentials_var.reset(creds_token)
            request_id_var.reset(rid_token)


def create_mcp_server(settings: Settings | None = None) -> FastMCP:
    """Build the FastMCP server and register every tool."""
    mcp = FastMCP(
        name="clio-mcp",
        instructions=INSTRUCTIONS,
        # The service sits behind the gateway, so Host is never localhost;
        # DNS-rebinding protection would reject those requests with 421.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        stateless_http=True,
    )

    client_factory: Callable[[], ClioClient | None] = get_client_from_context

    from .tools import activities, bills, calendar_entries, communications, contacts, documents, matters, navigation, tasks

    for module in (navigation, matters, contacts, activities, communications, tasks, documents, calendar_entries, bills):
        module.register(mcp, client_factory)

    return mcp
