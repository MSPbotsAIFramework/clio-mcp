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
"""Contacts: people and companies (clients, opposing counsel, witnesses, ...)."""

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
    Limit,
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

CONTACT_FIELDS = (
    "id,name,type,first_name,last_name,title,primary_email_address,primary_phone_number,"
    "is_client,company{id,name},updated_at"
)
CONTACT_DETAIL_FIELDS = (
    CONTACT_FIELDS + ",prefix,middle_name,date_of_birth,secondary_email_address,secondary_phone_number,"
    "email_addresses{id,name,address,default_email},phone_numbers{id,name,number,default_number},"
    "addresses{id,name,street,city,province,postal_code,country,primary},created_at"
)
ContactType = Literal["Person", "Company"]
_CHANNELS_FIELDS = "email_addresses{id,name,address,default_email},phone_numbers{id,name,number,default_number}"


def _email(address: str, existing_id: int | None = None) -> dict:
    return compact({"id": existing_id, "name": "Work", "address": address, "default_email": True})


def _phone(number: str, existing_id: int | None = None) -> dict:
    return compact({"id": existing_id, "name": "Work", "number": number, "default_number": True})


def _default_id(entries: Any, flag: str) -> int | None:
    """Id of the default (else the only) entry, so an update replaces it instead of adding one."""
    rows = [e for e in entries or [] if isinstance(e, dict) and e.get("id") is not None]
    preferred = [e for e in rows if e.get(flag)] or (rows if len(rows) == 1 else [])
    return preferred[0]["id"] if preferred else None


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def clio_contacts_list(
        query: Annotated[str | None, Field(description="Wildcard search on name, title, email, address and phone.")] = None,
        type: Annotated[ContactType | None, Field(description="Only people or only companies.")] = None,
        client_only: Annotated[bool | None, Field(description="Only contacts that are clients.")] = None,
        initial: Annotated[str | None, Field(description="Last name / company name starts with this letter.", max_length=1)] = None,
        created_since: Since = None,
        updated_since: Since = None,
        fields: Fields = None,
        limit: Limit = None,
        page_token: PageToken = None,
        order: Order = None,
    ) -> str:
        """List contacts (people and companies) with type, primary email/phone and client flag."""

        async def action(client: ClioClient) -> dict:
            params = {
                "query": query,
                "type": type,
                "client_only": client_only,
                "initial": initial.upper() if initial else None,
                "created_since": created_since,
                "updated_since": updated_since,
            }
            return await list_records(client, "contacts", params, CONTACT_FIELDS, fields, limit, page_token, order)

        return await run_tool("clio_contacts_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def clio_contacts_get(id: RecordId, fields: Fields = None) -> str:
        """Get one contact by id, including all email addresses, phone numbers and addresses."""

        async def action(client: ClioClient) -> Any:
            return await get_record(client, f"contacts/{id}", CONTACT_DETAIL_FIELDS, fields)

        return await run_tool("clio_contacts_get", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def clio_contacts_create(
        name: Annotated[str, Field(description="Display name; for a company, the company name.")],
        type: Annotated[ContactType, Field(description="Person or Company.")],
        first_name: Annotated[str | None, Field(description="Person only.")] = None,
        last_name: Annotated[str | None, Field(description="Person only.")] = None,
        company_id: Annotated[int | None, Field(description="Person only: employer company contact id.", ge=1)] = None,
        email: Annotated[str | None, Field(description="Primary (work) email address.")] = None,
        phone: Annotated[str | None, Field(description="Primary (work) phone number.")] = None,
    ) -> str:
        """Create a person or company contact. Only when the user explicitly asks."""

        async def action(client: ClioClient) -> Any:
            if type == "Company" and (first_name or last_name or company_id):
                raise ValueError("first_name, last_name and company_id apply to a Person, not a Company.")
            data = compact(
                {
                    "name": required_text(name, "name"),
                    "type": type,
                    "first_name": first_name,
                    "last_name": last_name,
                    "company": ref(company_id),
                    "email_addresses": [_email(email.strip())] if email and email.strip() else None,
                    "phone_numbers": [_phone(phone.strip())] if phone and phone.strip() else None,
                }
            )
            return unwrap(await client.post("contacts", data, {"fields": CONTACT_FIELDS}))

        return await run_tool("clio_contacts_create", client_factory, action)

    @mcp.tool(annotations=WRITES_IDEMPOTENT)
    async def clio_contacts_update(
        id: RecordId,
        name: Annotated[str | None, Field(description="New display name.")] = None,
        first_name: Annotated[str | None, Field(description="New first name.")] = None,
        last_name: Annotated[str | None, Field(description="New last name.")] = None,
        email: Annotated[str | None, Field(description="Replaces the primary email address.")] = None,
        phone: Annotated[str | None, Field(description="Replaces the primary phone number.")] = None,
    ) -> str:
        """Change a contact; only the fields given change. Only when the user explicitly asks."""

        async def action(client: ClioClient) -> Any:
            data = compact({"name": name, "first_name": first_name, "last_name": last_name})
            new_email = email.strip() if email and email.strip() else None
            new_phone = phone.strip() if phone and phone.strip() else None
            if new_email or new_phone:
                # Entries sent without an id are added as new ones, so update the
                # current primary entry in place instead.
                current = await get_record(client, f"contacts/{id}", _CHANNELS_FIELDS, None) or {}
                if new_email:
                    data["email_addresses"] = [_email(new_email, _default_id(current.get("email_addresses"), "default_email"))]
                if new_phone:
                    data["phone_numbers"] = [_phone(new_phone, _default_id(current.get("phone_numbers"), "default_number"))]
            if not data:
                raise ValueError("Nothing to update: pass at least one field to change.")
            return unwrap(await client.patch(f"contacts/{id}", data, {"fields": CONTACT_FIELDS}))

        return await run_tool("clio_contacts_update", client_factory, action)
