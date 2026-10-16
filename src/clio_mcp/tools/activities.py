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
"""Activities: time entries and expense entries logged against matters."""

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..api_client import ClioClient
from ._common import (
    READ_ONLY,
    WRITES,
    ClientFactory,
    Fields,
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

ACTIVITY_FIELDS = (
    "id,type,date,quantity_in_hours,quantity,price,total,note,billed,non_billable,"
    "matter{id,display_number},activity_description{id,name},updated_at"
)
ACTIVITY_DETAIL_FIELDS = (
    ACTIVITY_FIELDS + ",rounded_quantity_in_hours,flat_rate,on_bill,no_charge,reference,"
    "non_billable_total,expense_category{id,name},created_at"
)
ActivityType = Literal["TimeEntry", "ExpenseEntry", "HardCostEntry", "SoftCostEntry"]
ActivityStatus = Literal["billed", "draft", "unbilled", "non_billable", "billable", "written_off"]


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def clio_activities_list(
        query: Annotated[str | None, Field(description="Wildcard search on the note.")] = None,
        matter_id: OptionalId = None,
        user_id: OptionalId = None,
        task_id: OptionalId = None,
        type: Annotated[ActivityType | None, Field(description="Only this kind of entry.")] = None,
        status: Annotated[ActivityStatus | None, Field(description="Billing status.")] = None,
        start_date: Annotated[str | None, Field(description="ISO 8601 date: entries on or after it.")] = None,
        end_date: Annotated[str | None, Field(description="ISO 8601 date: entries on or before it.")] = None,
        created_since: Since = None,
        updated_since: Since = None,
        fields: Fields = None,
        limit: Limit = None,
        page_token: PageToken = None,
        order: Order = None,
    ) -> str:
        """List time and expense entries with date, hours, rate, total, note and matter."""

        async def action(client: ClioClient) -> dict:
            params = {
                "query": query,
                "matter_id": matter_id,
                "user_id": user_id,
                "task_id": task_id,
                "type": type,
                "status": status,
                "start_date": start_date,
                "end_date": end_date,
                "created_since": created_since,
                "updated_since": updated_since,
            }
            return await list_records(client, "activities", params, ACTIVITY_FIELDS, fields, limit, page_token, order)

        return await run_tool("clio_activities_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def clio_activities_get(id: RecordId, fields: Fields = None) -> str:
        """Get one time or expense entry by id."""

        async def action(client: ClioClient) -> Any:
            return await get_record(client, f"activities/{id}", ACTIVITY_DETAIL_FIELDS, fields)

        return await run_tool("clio_activities_get", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def clio_activities_create(
        date: Annotated[str, Field(description="ISO 8601 date of the work or expense, e.g. 2026-06-01.")],
        type: Annotated[ActivityType, Field(description="TimeEntry for time; the others for expenses.")],
        matter_id: OptionalId = None,
        note: Annotated[str | None, Field(description="Description of the work or expense.")] = None,
        price: Annotated[float | None, Field(description="TimeEntry: hourly rate. Expenses: unit amount.", ge=0)] = None,
        quantity: Annotated[
            float | None, Field(description="TimeEntry: duration in SECONDS (3600 = 1 hour). Expenses: quantity.", gt=0)
        ] = None,
        non_billable: Annotated[bool | None, Field(description="Mark the entry non-billable.")] = None,
    ) -> str:
        """Log a time or expense entry; billable entries can end up on a client bill.

        Only when the user explicitly asks.
        """

        async def action(client: ClioClient) -> Any:
            data = compact(
                {
                    "date": required_text(date, "date"),
                    "type": type,
                    "matter": ref(matter_id),
                    "note": note,
                    "price": price,
                    "quantity": quantity,
                    "non_billable": non_billable,
                }
            )
            return unwrap(await client.post("activities", data, {"fields": ACTIVITY_FIELDS}))

        return await run_tool("clio_activities_create", client_factory, action)
