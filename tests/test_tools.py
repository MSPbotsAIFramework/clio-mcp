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
"""tools/list snapshot and error-envelope mapping. No network access."""

import asyncio
import json

import pytest

from clio_mcp.api_client import ClioError
from clio_mcp.server import INSTRUCTIONS, create_mcp_server

# Tool names are a public contract: existing agents and tenant connections
# depend on them. Changing this snapshot is a breaking change.
EXPECTED_TOOLS: dict[str, set[str]] = {
    "clio_navigate": {"domain"},
    "clio_back": set(),
    "clio_status": set(),
    "clio_matters_list": set(),
    "clio_matters_get": {"id"},
    "clio_matters_create": {"description"},
    "clio_matters_update": {"id"},
    "clio_contacts_list": set(),
    "clio_contacts_get": {"id"},
    "clio_contacts_create": {"name", "type"},
    "clio_contacts_update": {"id"},
    "clio_activities_list": set(),
    "clio_activities_get": {"id"},
    "clio_activities_create": {"date", "type"},
    "clio_communications_list": set(),
    "clio_communications_get": {"id"},
    "clio_tasks_list": set(),
    "clio_tasks_get": {"id"},
    "clio_tasks_create": {"name", "description", "assignee_id"},
    "clio_tasks_update": {"id"},
    "clio_documents_list": set(),
    "clio_documents_get": {"id"},
    "clio_calendar_entries_list": set(),
    "clio_calendar_entries_get": {"id"},
    "clio_bills_list": set(),
    "clio_bills_get": {"id"},
}

WRITE_TOOLS = {
    "clio_matters_create",
    "clio_matters_update",
    "clio_contacts_create",
    "clio_contacts_update",
    "clio_activities_create",
    "clio_tasks_create",
    "clio_tasks_update",
}


@pytest.fixture(scope="module")
def tools():
    return {t.name: t for t in asyncio.run(create_mcp_server().list_tools())}


def test_all_tools_listed_without_navigation(tools):
    # Stateless: every tool is in tools/list from the first request.
    assert set(tools) == set(EXPECTED_TOOLS)


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_required_params(tools, name):
    assert set(tools[name].inputSchema.get("required", [])) == EXPECTED_TOOLS[name]


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_description_budget(tools, name):
    description = (tools[name].description or "").strip()
    assert description, f"{name} has no description"
    assert len(description) <= 500
    assert len(description.splitlines()[0]) <= 100


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_annotations_match_side_effects(tools, name):
    annotations = tools[name].annotations
    assert annotations is not None
    assert annotations.readOnlyHint is (name not in WRITE_TOOLS)
    assert not annotations.destructiveHint


@pytest.mark.parametrize("name", sorted(WRITE_TOOLS))
def test_write_tools_say_when_to_use_them(tools, name):
    assert "explicitly asks" in (tools[name].description or "")


def test_calendar_from_parameter_keeps_its_wire_name(tools):
    properties = tools["clio_calendar_entries_list"].inputSchema["properties"]
    assert "from" in properties
    assert "from_" not in properties


def test_instructions_budget():
    assert len(INSTRUCTIONS) <= 1500


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "unauthorized", False),
        (403, "unauthorized", False),
        (404, "not_found", False),
        (400, "invalid_argument", False),
        (422, "invalid_argument", False),
        (429, "rate_limited", True),
        (500, "upstream_error", True),
        (0, "upstream_error", True),
    ],
)
def test_error_envelope(status, code, retryable):
    envelope = json.loads(ClioError(status, "boom").to_envelope())
    assert envelope == {"error": {"code": code, "message": "boom", "retryable": retryable}}


def test_tool_without_credentials_returns_not_configured():
    content, _ = asyncio.run(create_mcp_server().call_tool("clio_status", {}))
    assert json.loads(content[0].text)["error"]["code"] == "not_configured"


def test_navigation_tools_need_no_credentials():
    content, _ = asyncio.run(create_mcp_server().call_tool("clio_navigate", {"domain": "bills"}))
    assert json.loads(content[0].text)["tools"] == ["clio_bills_list", "clio_bills_get"]
