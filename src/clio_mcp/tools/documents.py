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
"""Document metadata (read-only; file contents are not exposed)."""

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

DOCUMENT_FIELDS = (
    "id,name,filename,size,content_type,parent{id,name},matter{id,display_number},contact{id,name},"
    "document_category{id,name},updated_at"
)
DOCUMENT_DETAIL_FIELDS = DOCUMENT_FIELDS + ",locked,received_at,creator{id,name},created_at,deleted_at"


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def clio_documents_list(
        query: Annotated[str | None, Field(description="Wildcard search on the document name.")] = None,
        matter_id: OptionalId = None,
        contact_id: OptionalId = None,
        document_category_id: OptionalId = None,
        parent_id: Annotated[int | None, Field(description="Folder id.", ge=1)] = None,
        scope: Annotated[
            Literal["children", "descendants"] | None,
            Field(description="With parent_id: direct children (default) or all descendants."),
        ] = None,
        include_deleted: Annotated[bool | None, Field(description="Include deleted documents.")] = None,
        created_since: Since = None,
        updated_since: Since = None,
        fields: Fields = None,
        limit: Limit = None,
        page_token: PageToken = None,
        order: Order = None,
    ) -> str:
        """List document metadata: name, file name, size, type, folder, matter and contact. No file contents."""

        async def action(client: ClioClient) -> dict:
            params = {
                "query": query,
                "matter_id": matter_id,
                "contact_id": contact_id,
                "document_category_id": document_category_id,
                "parent_id": parent_id,
                "scope": scope,
                "include_deleted": include_deleted,
                "created_since": created_since,
                "updated_since": updated_since,
            }
            return await list_records(client, "documents", params, DOCUMENT_FIELDS, fields, limit, page_token, order)

        return await run_tool("clio_documents_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def clio_documents_get(id: RecordId, fields: Fields = None) -> str:
        """Get one document's metadata by id. No file contents."""

        async def action(client: ClioClient) -> Any:
            return await get_record(client, f"documents/{id}", DOCUMENT_DETAIL_FIELDS, fields)

        return await run_tool("clio_documents_get", client_factory, action)
