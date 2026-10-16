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
"""Matters: the case / file every other Clio record hangs off."""

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..api_client import ClioClient
from ._common import (
    READ_ONLY,
    WRITES,
    WRITES_IDEMPOTENT,
    ClientFactory,
    Fields,
    IsoDate,
    Limit,
    OptionalId,
    Order,
    PageToken,
    RecordId,
    Since,
    compact,
    get_record,
    list_records,
    ref,
    required_text,
    run_tool,
    unwrap,
)

MATTER_FIELDS = (
    "id,display_number,description,status,client{id,name},practice_area{id,name},"
    "responsible_attorney{id,name},open_date,close_date,billable,updated_at"
)
MATTER_DETAIL_FIELDS = (
    MATTER_FIELDS + ",client_reference,location,billing_method,pending_date,originating_attorney{id,name},"
    "responsible_staff{id,name},matter_stage{id,name},last_activity_date,created_at"
)
_STATUSES = {"open", "pending", "closed"}
Status = Literal["open", "pending", "closed"]


def _status_filter(value: str | None) -> str | None:
    if not value:
        return None
    parts = [p.strip().lower() for p in value.split(",") if p.strip()]
    bad = [p for p in parts if p not in _STATUSES]
    if bad:
        raise ValueError(f"status values must be open, pending or closed (got {', '.join(bad)}).")
    return ",".join(parts) or None


async def resolve_client_id(client: ClioClient, client_id: int | None, client_name: str | None) -> int:
    if client_id is not None:
        return client_id
    name = (client_name or "").strip()
    if not name:
        raise ValueError("Provide client_id, or client_name to look the client up.")
    payload = await client.get("contacts", {"query": name, "fields": "id,name,type,is_client", "limit": 10}) or {}
    matches = [c for c in payload.get("data", []) if isinstance(c, dict)]
    exact = [c for c in matches if str(c.get("name", "")).strip().lower() == name.lower()]
    chosen = exact if len(exact) == 1 else matches
    if len(chosen) == 1:
        return int(chosen[0]["id"])
    if not chosen:
        raise ValueError(f'No contact matches "{name}". Pass client_id, or create the contact with clio_contacts_create first.')
    options = "; ".join(f"{c.get('name')} (id {c.get('id')})" for c in chosen[:10])
    raise ValueError(f'Several contacts match "{name}": {options}. Ask the user which one, then pass client_id.')


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def clio_matters_list(
        query: Annotated[str | None, Field(description="Wildcard search on description, display number and client name.")] = None,
        status: Annotated[str | None, Field(description='Comma-separated: open, pending, closed (e.g. "open,pending").')] = None,
        client_id: OptionalId = None,
        practice_area_id: OptionalId = None,
        responsible_attorney_id: OptionalId = None,
        billable: Annotated[bool | None, Field(description="Only billable (true) or non-billable (false) matters.")] = None,
        created_since: Since = None,
        updated_since: Since = None,
        fields: Fields = None,
        limit: Limit = None,
        page_token: PageToken = None,
        order: Order = None,
    ) -> str:
        """List matters (cases/files) with number, description, status, client and responsible attorney."""

        async def action(client: ClioClient) -> dict:
            params = {
                "query": query,
                "status": _status_filter(status),
                "client_id": client_id,
                "practice_area_id": practice_area_id,
                "responsible_attorney_id": responsible_attorney_id,
                "billable": billable,
                "created_since": created_since,
                "updated_since": updated_since,
            }
            return await list_records(client, "matters", params, MATTER_FIELDS, fields, limit, page_token, order)

        return await run_tool("clio_matters_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def clio_matters_get(id: RecordId, fields: Fields = None) -> str:
        """Get one matter by id, including attorneys, stage, billing method and dates."""

        async def action(client: ClioClient) -> Any:
            return await get_record(client, f"matters/{id}", MATTER_DETAIL_FIELDS, fields)

        return await run_tool("clio_matters_get", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def clio_matters_create(
        description: Annotated[str, Field(description="Matter description (required by Clio).")],
        client_id: Annotated[int | None, Field(description="Client contact id; or give client_name.", ge=1)] = None,
        client_name: Annotated[str | None, Field(description="Client name to look up when client_id is unknown; must match one contact.")] = None,
        status: Annotated[Status | None, Field(description="Default open.")] = None,
        practice_area_id: OptionalId = None,
        responsible_attorney_id: OptionalId = None,
        originating_attorney_id: OptionalId = None,
        billable: Annotated[bool | None, Field(description="Whether time on the matter is billable.")] = None,
        open_date: IsoDate = None,
        client_reference: Annotated[str | None, Field(description="The client's own reference for the matter.")] = None,
    ) -> str:
        """Create a matter for a client. Only when the user explicitly asks."""

        async def action(client: ClioClient) -> Any:
            text = required_text(description, "description")
            data = compact(
                {
                    "description": text,
                    "client": ref(await resolve_client_id(client, client_id, client_name)),
                    "status": status,
                    "practice_area": ref(practice_area_id),
                    "responsible_attorney": ref(responsible_attorney_id),
                    "originating_attorney": ref(originating_attorney_id),
                    "billable": billable,
                    "open_date": open_date,
                    "client_reference": client_reference,
                }
            )
            return unwrap(await client.post("matters", data, {"fields": MATTER_FIELDS}))

        return await run_tool("clio_matters_create", client_factory, action)

    @mcp.tool(annotations=WRITES_IDEMPOTENT)
    async def clio_matters_update(
        id: RecordId,
        description: Annotated[str | None, Field(description="New description.")] = None,
        status: Annotated[Status | None, Field(description="New status.")] = None,
        client_id: OptionalId = None,
        practice_area_id: OptionalId = None,
        responsible_attorney_id: OptionalId = None,
        originating_attorney_id: OptionalId = None,
        billable: Annotated[bool | None, Field(description="Whether time on the matter is billable.")] = None,
        close_date: IsoDate = None,
    ) -> str:
        """Change fields of a matter; only the fields given change. Only when the user explicitly asks."""

        async def action(client: ClioClient) -> Any:
            data = compact(
                {
                    "description": description,
                    "status": status,
                    "client": ref(client_id),
                    "practice_area": ref(practice_area_id),
                    "responsible_attorney": ref(responsible_attorney_id),
                    "originating_attorney": ref(originating_attorney_id),
                    "billable": billable,
                    "close_date": close_date,
                }
            )
            if not data:
                raise ValueError("Nothing to update: pass at least one field to change.")
            return unwrap(await client.patch(f"matters/{id}", data, {"fields": MATTER_FIELDS}))

        return await run_tool("clio_matters_update", client_factory, action)
