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
"""Communications: emails and phone calls logged against matters (read-only)."""

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..api_client import ClioClient
from ._common import (
    READ_ONLY,
    ClientFactory,
    Fields,
    Limit,
    OptionalId,
    Order,
    PageToken,
    RecordId,
    Since,
    get_record,
    list_records,
    run_tool,
)

COMMUNICATION_FIELDS = (
    "id,type,subject,date,received_at,matter{id,display_number},user{id,name},"
    "senders{id,name,type},receivers{id,name,type}"
)
COMMUNICATION_DETAIL_FIELDS = COMMUNICATION_FIELDS + ",body,time_entries_count,created_at,updated_at"


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def clio_communications_list(
        query: Annotated[str | None, Field(description="Wildcard search on subject and body.")] = None,
        matter_id: OptionalId = None,
        contact_id: OptionalId = None,
        user_id: OptionalId = None,
        type: Annotated[
            Literal["EmailCommunication", "PhoneCommunication"] | None, Field(description="Only emails or only calls.")
        ] = None,
        date: Annotated[str | None, Field(description="ISO 8601 date: communications on exactly this day.")] = None,
        created_since: Since = None,
        updated_since: Since = None,
        fields: Fields = None,
        limit: Limit = None,
        page_token: PageToken = None,
        order: Order = None,
    ) -> str:
        """List logged emails and phone calls with subject, date, matter and participants (no body)."""

        async def action(client: ClioClient) -> dict:
            params = {
                "query": query,
                "matter_id": matter_id,
                "contact_id": contact_id,
                "user_id": user_id,
                "type": type,
                "date": date,
                "created_since": created_since,
                "updated_since": updated_since,
            }
            return await list_records(
                client, "communications", params, COMMUNICATION_FIELDS, fields, limit, page_token, order
            )

        return await run_tool("clio_communications_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def clio_communications_get(id: RecordId, fields: Fields = None) -> str:
        """Get one logged email or phone call by id, including its body."""

        async def action(client: ClioClient) -> Any:
            return await get_record(client, f"communications/{id}", COMMUNICATION_DETAIL_FIELDS, fields)

        return await run_tool("clio_communications_get", client_factory, action)
