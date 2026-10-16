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
"""Bills (invoices), read-only."""

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..api_client import ClioClient
from ._common import (
    READ_ONLY,
    ClientFactory,
    Fields,
    IsoDate,
    Limit,
    OptionalId,
    Order,
    PageToken,
    RecordId,
    get_record,
    list_records,
    run_tool,
)

BILL_FIELDS = (
    "id,number,state,kind,issued_at,due_at,total,paid,due,balance,client{id,name},"
    "matters{id,display_number},updated_at"
)
BILL_DETAIL_FIELDS = (
    BILL_FIELDS + ",subject,memo,purchase_order,start_at,end_at,sub_total,total_tax,paid_at,last_sent_at,created_at"
)
BillState = Literal["draft", "awaiting_approval", "awaiting_payment", "paid", "void", "deleted"]


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def clio_bills_list(
        client_id: OptionalId = None,
        matter_id: OptionalId = None,
        state: Annotated[BillState | None, Field(description="Only bills in this state.")] = None,
        type: Annotated[Literal["revenue", "trust"] | None, Field(description="Revenue (fees) or trust bills.")] = None,
        overdue_only: Annotated[bool | None, Field(description="Only overdue bills.")] = None,
        due_after: IsoDate = None,
        due_before: IsoDate = None,
        issued_after: IsoDate = None,
        issued_before: IsoDate = None,
        fields: Fields = None,
        limit: Limit = None,
        page_token: PageToken = None,
        order: Order = None,
    ) -> str:
        """List bills (invoices) with number, state, dates, total, paid, balance due, client and matters."""

        async def action(client: ClioClient) -> dict:
            params = {
                "client_id": client_id,
                "matter_id": matter_id,
                "state": state,
                "type": type,
                "overdue_only": overdue_only,
                "due_after": due_after,
                "due_before": due_before,
                "issued_after": issued_after,
                "issued_before": issued_before,
            }
            return await list_records(client, "bills", params, BILL_FIELDS, fields, limit, page_token, order)

        return await run_tool("clio_bills_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def clio_bills_get(id: RecordId, fields: Fields = None) -> str:
        """Get one bill by id, including subject, memo, billing period and tax totals."""

        async def action(client: ClioClient) -> Any:
            return await get_record(client, f"bills/{id}", BILL_DETAIL_FIELDS, fields)

        return await run_tool("clio_bills_get", client_factory, action)
