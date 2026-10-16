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
"""Calendar entries, optionally tied to a matter (read-only)."""

import inspect
from typing import Annotated, Any

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
    get_record,
    list_records,
    run_tool,
)

CALENDAR_FIELDS = (
    "id,summary,location,start_at,end_at,all_day,matter{id,display_number},calendar_owner{id,name},updated_at"
)
CALENDAR_DETAIL_FIELDS = CALENDAR_FIELDS + ",description,recurrence_rule,court_rule,start_at_time_zone,created_at"

# "from" is a Python keyword, so this tool is declared with an explicit
# signature whose parameter carries the alias "from".
_LIST_PARAMS: list[tuple[str, str, Any]] = [
    ("query", "query", Annotated[str | None, Field(description="Wildcard search on the entry.")]),
    ("matter_id", "matter_id", OptionalId),
    ("calendar_id", "calendar_id", OptionalId),
    ("from_", "from", Annotated[str | None, Field(alias="from", description="ISO 8601 timestamp: entries ending on or after it.")]),
    ("to", "to", Annotated[str | None, Field(description="ISO 8601 timestamp: entries starting on or before it.")]),
    ("is_all_day", "is_all_day", Annotated[bool | None, Field(description="Only all-day (true) or timed (false) entries.")]),
    ("fields", "fields", Fields),
    ("limit", "limit", Limit),
    ("page_token", "page_token", PageToken),
    ("order", "order", Order),
]
_PAGING = {"fields", "limit", "page_token", "order"}


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    async def clio_calendar_entries_list(**kwargs: Any) -> str:
        """List calendar entries with title, time, location, matter and calendar owner."""

        def arg(python: str, api: str) -> Any:
            return kwargs.get(api, kwargs.get(python))

        async def action(client: ClioClient) -> dict:
            params = {api: arg(python, api) for python, api, _ in _LIST_PARAMS if api not in _PAGING}
            return await list_records(
                client,
                "calendar_entries",
                params,
                CALENDAR_FIELDS,
                kwargs.get("fields"),
                kwargs.get("limit"),
                kwargs.get("page_token"),
                kwargs.get("order"),
            )

        return await run_tool("clio_calendar_entries_list", client_factory, action)

    clio_calendar_entries_list.__signature__ = inspect.Signature(
        [
            inspect.Parameter(python, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=annotation)
            for python, _api, annotation in _LIST_PARAMS
        ],
        return_annotation=str,
    )
    mcp.tool(annotations=READ_ONLY)(clio_calendar_entries_list)

    @mcp.tool(annotations=READ_ONLY)
    async def clio_calendar_entries_get(id: RecordId, fields: Fields = None) -> str:
        """Get one calendar entry by id, including its description and recurrence."""

        async def action(client: ClioClient) -> Any:
            return await get_record(client, f"calendar_entries/{id}", CALENDAR_DETAIL_FIELDS, fields)

        return await run_tool("clio_calendar_entries_get", client_factory, action)
