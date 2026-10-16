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
"""Discovery helpers kept for compatibility, plus the connection check.

Every tool is listed from the first tools/list, so clio_navigate and clio_back
only describe tools; they keep no state.
"""

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .._json import compact as to_json
from ..api_client import ClioClient
from ._common import LOCAL, READ_ONLY, ClientFactory, run_tool

DOMAINS: dict[str, tuple[str, list[str]]] = {
    "matters": (
        "Matters (cases/files): list, get, create, update.",
        ["clio_matters_list", "clio_matters_get", "clio_matters_create", "clio_matters_update"],
    ),
    "contacts": (
        "Contacts (people and companies, including clients): list, get, create, update.",
        ["clio_contacts_list", "clio_contacts_get", "clio_contacts_create", "clio_contacts_update"],
    ),
    "activities": (
        "Time and expense entries: list, get, create.",
        ["clio_activities_list", "clio_activities_get", "clio_activities_create"],
    ),
    "communications": (
        "Logged emails and phone calls (read-only).",
        ["clio_communications_list", "clio_communications_get"],
    ),
    "tasks": (
        "Tasks: list, get, create, update.",
        ["clio_tasks_list", "clio_tasks_get", "clio_tasks_create", "clio_tasks_update"],
    ),
    "documents": (
        "Document metadata only (read-only; no file contents).",
        ["clio_documents_list", "clio_documents_get"],
    ),
    "calendar-entries": (
        "Calendar entries (read-only).",
        ["clio_calendar_entries_list", "clio_calendar_entries_get"],
    ),
    "bills": (
        "Bills / invoices (read-only).",
        ["clio_bills_list", "clio_bills_get"],
    ),
}

Domain = Literal["matters", "contacts", "activities", "communications", "tasks", "documents", "calendar-entries", "bills"]


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=LOCAL)
    async def clio_navigate(domain: Annotated[Domain, Field(description="Area to describe.")]) -> str:
        """Describe the tools of one Clio area. All tools are already callable; this changes nothing."""
        summary, tools = DOMAINS[domain]
        return to_json({"domain": domain, "summary": summary, "tools": tools})

    @mcp.tool(annotations=LOCAL)
    async def clio_back() -> str:
        """List every Clio area and its tools. All tools are already callable; this changes nothing."""
        return to_json({"domains": {name: {"summary": s, "tools": t} for name, (s, t) in DOMAINS.items()}})

    @mcp.tool(annotations=READ_ONLY)
    async def clio_status() -> str:
        """Check the Clio connection: signed-in user, region and whether the token can be refreshed."""

        async def action(client: ClioClient) -> Any:
            payload = await client.get("users/who_am_i", {"fields": "id,name,email,enabled,subscription_type"}) or {}
            return {
                "connected": True,
                "region": client.region,
                "canAutoRefresh": client.can_refresh,
                "user": payload.get("data", payload) if isinstance(payload, dict) else payload,
                "domains": list(DOMAINS),
            }

        return await run_tool("clio_status", client_factory, action)
