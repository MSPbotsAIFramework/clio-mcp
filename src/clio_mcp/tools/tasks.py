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
"""Tasks assigned to firm users or contacts, optionally tied to a matter."""

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

TASK_FIELDS = "id,name,status,priority,due_at,completed_at,assignee{id,name,type},matter{id,display_number},updated_at"
TASK_DETAIL_FIELDS = TASK_FIELDS + ",description,permission,statute_of_limitations,time_estimated,task_type{id,name},created_at"
TaskStatus = Literal["pending", "in_progress", "in_review", "complete", "draft"]
TaskPriority = Literal["High", "Normal", "Low"]
AssigneeType = Literal["User", "Contact"]


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def clio_tasks_list(
        query: Annotated[str | None, Field(description="Wildcard search on name and description.")] = None,
        matter_id: OptionalId = None,
        assignee_id: OptionalId = None,
        status: Annotated[TaskStatus | None, Field(description="Only this status.")] = None,
        priority: Annotated[Literal["high", "normal", "low"] | None, Field(description="Only this priority.")] = None,
        complete: Annotated[bool | None, Field(description="Only completed (true) or open (false) tasks.")] = None,
        due_at_from: IsoDate = None,
        due_at_to: IsoDate = None,
        created_since: Since = None,
        updated_since: Since = None,
        fields: Fields = None,
        limit: Limit = None,
        page_token: PageToken = None,
        order: Order = None,
        assignee_type: Annotated[
            Literal["user", "contact"] | None, Field(description="Kind of assignee_id (default user).")
        ] = None,
    ) -> str:
        """List tasks with status, priority, due date, assignee and matter."""

        async def action(client: ClioClient) -> dict:
            params = {
                "query": query,
                "matter_id": matter_id,
                "assignee_id": assignee_id,
                # Clio needs the assignee type alongside an assignee id.
                "assignee_type": (assignee_type or "user") if assignee_id is not None else None,
                "status": status,
                "priority": priority,
                "complete": complete,
                "due_at_from": due_at_from,
                "due_at_to": due_at_to,
                "created_since": created_since,
                "updated_since": updated_since,
            }
            return await list_records(client, "tasks", params, TASK_FIELDS, fields, limit, page_token, order)

        return await run_tool("clio_tasks_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def clio_tasks_get(id: RecordId, fields: Fields = None) -> str:
        """Get one task by id, including its description."""

        async def action(client: ClioClient) -> Any:
            return await get_record(client, f"tasks/{id}", TASK_DETAIL_FIELDS, fields)

        return await run_tool("clio_tasks_get", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def clio_tasks_create(
        name: Annotated[str, Field(description="Task name.")],
        description: Annotated[str, Field(description="Task description.")],
        assignee_id: Annotated[int, Field(description="Clio user id (or contact id with assignee_type Contact).", ge=1)],
        assignee_type: Annotated[AssigneeType | None, Field(description="Default User.")] = None,
        matter_id: OptionalId = None,
        due_at: IsoDate = None,
        priority: Annotated[TaskPriority | None, Field(description="Default Normal.")] = None,
        status: Annotated[TaskStatus | None, Field(description="Default pending.")] = None,
    ) -> str:
        """Create a task and assign it. Only when the user explicitly asks."""

        async def action(client: ClioClient) -> Any:
            data = compact(
                {
                    "name": required_text(name, "name"),
                    "description": required_text(description, "description"),
                    "assignee": {"id": assignee_id, "type": assignee_type or "User"},
                    "matter": ref(matter_id),
                    "due_at": due_at,
                    "priority": priority,
                    "status": status,
                }
            )
            return unwrap(await client.post("tasks", data, {"fields": TASK_FIELDS}))

        return await run_tool("clio_tasks_create", client_factory, action)

    @mcp.tool(annotations=WRITES_IDEMPOTENT)
    async def clio_tasks_update(
        id: RecordId,
        name: Annotated[str | None, Field(description="New name.")] = None,
        description: Annotated[str | None, Field(description="New description.")] = None,
        status: Annotated[TaskStatus | None, Field(description="New status (complete closes the task).")] = None,
        priority: Annotated[TaskPriority | None, Field(description="New priority.")] = None,
        due_at: IsoDate = None,
        assignee_id: Annotated[int | None, Field(description="Reassign to this user (or contact) id.", ge=1)] = None,
        assignee_type: Annotated[AssigneeType | None, Field(description="Kind of assignee_id (default User).")] = None,
    ) -> str:
        """Change a task; only the fields given change. Only when the user explicitly asks."""

        async def action(client: ClioClient) -> Any:
            if assignee_type and assignee_id is None:
                raise ValueError("assignee_type needs assignee_id.")
            data = compact(
                {
                    "name": name,
                    "description": description,
                    "status": status,
                    "priority": priority,
                    "due_at": due_at,
                    "assignee": {"id": assignee_id, "type": assignee_type or "User"} if assignee_id is not None else None,
                }
            )
            if not data:
                raise ValueError("Nothing to update: pass at least one field to change.")
            return unwrap(await client.patch(f"tasks/{id}", data, {"fields": TASK_FIELDS}))

        return await run_tool("clio_tasks_update", client_factory, action)
