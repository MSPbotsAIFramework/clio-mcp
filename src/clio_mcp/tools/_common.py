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
"""Shared argument types, list/get helpers and the error-handling wrapper."""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..api_client import ClioClient, ClioError, page_token_from

logger = logging.getLogger("clio_mcp.tools")

ClientFactory = Callable[[], ClioClient | None]

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)
LOCAL = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
WRITES_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)

NOT_CONFIGURED = error_envelope(
    "not_configured",
    "Clio credentials are missing for this request (X-Clio-Access-Token, or a refresh token with client id/secret).",
    False,
)

DEFAULT_LIMIT = 50

RecordId = Annotated[int, Field(description="Numeric Clio id.", ge=1)]
OptionalId = Annotated[int | None, Field(description="Numeric Clio id.", ge=1)]
Fields = Annotated[
    str | None,
    Field(description="Comma-separated fields to return, nested as client{id,name}; omit for a useful default set."),
]
Limit = Annotated[int | None, Field(description=f"Records per page, 1-200 (default {DEFAULT_LIMIT}).", ge=1, le=200)]
PageToken = Annotated[str | None, Field(description="next_page_token from the previous page.")]
Order = Annotated[str | None, Field(description='Sort, e.g. "id(asc)" or "updated_at(desc)".')]
Since = Annotated[str | None, Field(description="ISO 8601 timestamp, e.g. 2026-06-01T00:00:00Z.")]
IsoDate = Annotated[str | None, Field(description="ISO 8601 date, e.g. 2026-06-01.")]


def compact(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}


def ref(record_id: int | None) -> dict[str, int] | None:
    """Clio's nested-reference shape: {"id": n}."""
    return {"id": record_id} if record_id is not None else None


def required_text(value: str | None, name: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{name} must not be blank.")
    return text


async def list_records(
    client: ClioClient,
    path: str,
    params: dict[str, Any],
    default_fields: str,
    fields: str | None,
    limit: int | None,
    page_token: str | None,
    order: str | None,
) -> dict:
    """GET a collection and return {items, count, total, has_more, next_page_token}."""
    query = compact(params) | compact(
        {"fields": (fields or "").strip() or default_fields, "limit": limit or DEFAULT_LIMIT, "page_token": page_token, "order": order}
    )
    payload = await client.get(path, query) or {}
    items = payload.get("data") if isinstance(payload, dict) else payload
    items = items if isinstance(items, list) else []
    meta = payload.get("meta") if isinstance(payload, dict) else None
    paging = meta.get("paging") if isinstance(meta, dict) else None
    next_token = page_token_from(paging.get("next") if isinstance(paging, dict) else None)
    return compact(
        {
            "items": items,
            "count": len(items),
            "total": meta.get("records") if isinstance(meta, dict) else None,
            "has_more": next_token is not None,
            "next_page_token": next_token,
        }
    )


async def get_record(client: ClioClient, path: str, default_fields: str, fields: str | None) -> Any:
    payload = await client.get(path, {"fields": (fields or "").strip() or default_fields})
    return payload.get("data", payload) if isinstance(payload, dict) else payload


def unwrap(payload: Any) -> Any:
    return payload.get("data", payload) if isinstance(payload, dict) else payload


async def run_tool(
    name: str,
    client_factory: ClientFactory,
    action: Callable[[ClioClient], Awaitable[Any]],
) -> str:
    """Resolve the caller's client, run ``action``, and always return a string.

    Errors never escape as exceptions: they become the standard error envelope.
    """
    client = client_factory()
    if client is None:
        return NOT_CONFIGURED
    if client.header_error:
        return error_envelope("invalid_argument", client.header_error, False)
    started = time.monotonic()
    try:
        result = await action(client)
    except ClioError as e:
        logger.info("tool=%s outcome=error status=%s ms=%d", name, e.status_code, _ms(started))
        return e.to_envelope()
    except ValueError as e:
        logger.info("tool=%s outcome=invalid_argument ms=%d", name, _ms(started))
        return error_envelope("invalid_argument", str(e), False)
    except Exception as e:  # noqa: BLE001 - tools must never raise
        logger.warning("tool=%s outcome=unexpected error=%s ms=%d", name, type(e).__name__, _ms(started))
        return error_envelope("upstream_error", f"Unexpected error: {type(e).__name__}", True)
    logger.info("tool=%s outcome=ok ms=%d", name, _ms(started))
    if result is None:
        result = {"result": None, "note": "Clio returned an empty response."}
    return result if isinstance(result, str) else dump_json_capped(result)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
