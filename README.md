# Clio MCP Server

An [MCP](https://modelcontextprotocol.io) server for the [Clio Manage](https://www.clio.com)
API (v4), the practice-management platform used by law firms. It lets an AI agent read a
firm's matters, contacts, time and expense entries, tasks, logged communications, document
metadata, calendar entries and bills, and, on explicit request, create or update matters,
contacts, tasks and time / expense entries.

- Transport: MCP Streamable HTTP at `POST /mcp`, health probe at `GET /health`
- Stateless and multi-tenant: credentials arrive on every request as HTTP headers and are
  never stored; every tool is listed from the first `tools/list`
- Python 3.12, [uv](https://docs.astral.sh/uv/), FastMCP, httpx
- License: Apache-2.0, Copyright 2026 MSPbots.ai

## Authentication headers

Send **either** `X-Clio-Access-Token` **or** `X-Clio-Refresh-Token` + `X-Clio-Client-Id` +
`X-Clio-Client-Secret`. Sending all four is best: the access token is used directly and is
refreshed automatically when Clio rejects it.

| Header | Required | Type | Default | Allowed values | Description | Example |
|---|---|---|---|---|---|---|
| `X-Clio-Access-Token` | One of | string | none | - | Clio OAuth access token (a leading `Bearer ` is accepted and ignored) | `your-access-token` |
| `X-Clio-Refresh-Token` | One of | string | none | - | Clio OAuth refresh token, used to obtain a new access token | `your-refresh-token` |
| `X-Clio-Client-Id` | With refresh token | string | none | - | Client id (app key) of the Clio developer application | `your-client-id` |
| `X-Clio-Client-Secret` | With refresh token | string | none | - | Client secret (app secret) of the Clio developer application | `your-client-secret` |
| `X-Clio-Region` | No | string | `us` | `us`, `ca`, `eu`, `au` | Region that hosts the Clio account | `us` |

Where to get them:

- In the Clio **Developer Portal** of the account's region (`developers.clio.com`,
  `ca.developers.clio.com`, `eu.developers.clio.com` or `au.developers.clio.com`) create an
  application and note its **App Key** (client id) and **App Secret** (client secret). Give it
  read access to Matters, Contacts, Activities, Tasks, Communications, Documents, Calendars and
  Bills, and write access only to the areas the agent should change.
- Authorise the application for the firm's Clio account with the standard OAuth
  authorisation-code flow (`/oauth/authorize` then `/oauth/token` on the region's host). The
  token response contains the access token (valid for 30 days) and the refresh token (does
  not expire). Tokens from one region do not work in another.
- The region is the one in the account's Clio URL: `app.clio.com` = `us`, `ca.app.clio.com` =
  `ca`, `eu.app.clio.com` = `eu`, `au.app.clio.com` = `au`.

How credentials are sent to Clio: every call is `Authorization: Bearer <access token>` to
`https://{region host}/api/v4/...`. When Clio answers 401 (or no access token was sent) and a
refresh token with client id / secret is present, the server calls
`POST https://{region host}/oauth/token` with `grant_type=refresh_token` once and retries. The
refreshed token lives only for that request. Values that look like unresolved templates
(`${...}`) are treated as missing.

A request to `/mcp` without usable credentials gets HTTP 401 listing the accepted header sets;
an unknown region gets HTTP 400.

## Tools

All tools return a JSON string. Records are referenced by numeric Clio ids. List tools return
`{items, count, total, has_more, next_page_token}` and take the paging parameters `fields`,
`limit` (1-200, default 50), `page_token` and `order`, plus `created_since` / `updated_since`
where Clio supports them. Without `fields`, every list and get tool returns a useful default
field set (Clio itself returns only `id` and `etag`); pass `fields` with Clio's nested syntax,
for example `id,display_number,client{id,name}`, to choose your own.

| Tool | Purpose | Parameters (required in **bold**) |
|---|---|---|
| `clio_navigate` | Describe the tools of one Clio area (changes nothing) | **domain** (`matters`, `contacts`, `activities`, `communications`, `tasks`, `documents`, `calendar-entries`, `bills`) |
| `clio_back` | List every Clio area and its tools (changes nothing) | - |
| `clio_status` | Check the Clio connection: signed-in user, region and whether the token can be refreshed | - |
| `clio_matters_list` | List matters (cases/files) with number, description, status, client and responsible attorney | query, status, client_id, practice_area_id, responsible_attorney_id, billable, + paging |
| `clio_matters_get` | Get one matter by id, including attorneys, stage, billing method and dates | **id**, fields |
| `clio_matters_create` | Create a matter for a client (**write**) | **description**, client_id, client_name, status (`open`, `pending`, `closed`), practice_area_id, responsible_attorney_id, originating_attorney_id, billable, open_date, client_reference |
| `clio_matters_update` | Change fields of a matter; only the fields given change (**write**) | **id**, description, status (`open`, `pending`, `closed`), client_id, practice_area_id, responsible_attorney_id, originating_attorney_id, billable, close_date |
| `clio_contacts_list` | List contacts (people and companies) with type, primary email/phone and client flag | query, type (`Person`, `Company`), client_only, initial, + paging |
| `clio_contacts_get` | Get one contact by id, including all email addresses, phone numbers and addresses | **id**, fields |
| `clio_contacts_create` | Create a person or company contact (**write**) | **name**, **type** (`Person`, `Company`), first_name, last_name, company_id, email, phone |
| `clio_contacts_update` | Change a contact; only the fields given change (**write**) | **id**, name, first_name, last_name, email, phone |
| `clio_activities_list` | List time and expense entries with date, hours, rate, total, note and matter | query, matter_id, user_id, task_id, type (`TimeEntry`, `ExpenseEntry`, `HardCostEntry`, `SoftCostEntry`), status (`billed`, `draft`, `unbilled`, `non_billable`, `billable`, `written_off`), start_date, end_date, + paging |
| `clio_activities_get` | Get one time or expense entry by id | **id**, fields |
| `clio_activities_create` | Log a time or expense entry; billable entries can end up on a client bill (**write**) | **date**, **type** (`TimeEntry`, `ExpenseEntry`, `HardCostEntry`, `SoftCostEntry`), matter_id, note, price, quantity, non_billable |
| `clio_communications_list` | List logged emails and phone calls with subject, date, matter and participants (no body) | query, matter_id, contact_id, user_id, type (`EmailCommunication`, `PhoneCommunication`), date, + paging |
| `clio_communications_get` | Get one logged email or phone call by id, including its body | **id**, fields |
| `clio_tasks_list` | List tasks with status, priority, due date, assignee and matter | query, matter_id, assignee_id, assignee_type (`user`, `contact`), status (`pending`, `in_progress`, `in_review`, `complete`, `draft`), priority (`high`, `normal`, `low`), complete, due_at_from, due_at_to, + paging |
| `clio_tasks_get` | Get one task by id, including its description | **id**, fields |
| `clio_tasks_create` | Create a task and assign it (**write**) | **name**, **description**, **assignee_id**, assignee_type (`User`, `Contact`), matter_id, due_at, priority (`High`, `Normal`, `Low`), status (`pending`, `in_progress`, `in_review`, `complete`, `draft`) |
| `clio_tasks_update` | Change a task; only the fields given change (**write**) | **id**, name, description, status (`pending`, `in_progress`, `in_review`, `complete`, `draft`), priority (`High`, `Normal`, `Low`), due_at, assignee_id, assignee_type (`User`, `Contact`) |
| `clio_documents_list` | List document metadata: name, file name, size, type, folder, matter and contact | query, matter_id, contact_id, document_category_id, parent_id, scope (`children`, `descendants`), include_deleted, + paging |
| `clio_documents_get` | Get one document's metadata by id | **id**, fields |
| `clio_calendar_entries_list` | List calendar entries with title, time, location, matter and calendar owner | query, matter_id, calendar_id, from, to, is_all_day, + paging |
| `clio_calendar_entries_get` | Get one calendar entry by id, including its description and recurrence | **id**, fields |
| `clio_bills_list` | List bills (invoices) with number, state, dates, total, paid, balance due, client and matters | client_id, matter_id, state (`draft`, `awaiting_approval`, `awaiting_payment`, `paid`, `void`, `deleted`), type (`revenue`, `trust`), overdue_only, due_after, due_before, issued_after, issued_before, + paging |
| `clio_bills_get` | Get one bill by id, including subject, memo, billing period and tax totals | **id**, fields |

Errors come back as a JSON envelope instead of an exception:

```json
{"error": {"code": "unauthorized", "message": "Clio returned HTTP 401 for matters: ...", "retryable": false}}
```

`code` is one of `not_configured`, `unauthorized`, `not_found`, `invalid_argument`,
`rate_limited`, `upstream_error`; Clio validation errors name the offending fields. Clio
allows about 50 requests per minute per access token. Reads are retried with backoff on 429,
5xx and network errors (honouring `Retry-After`); writes are retried only on 429, so a write
never runs twice.

## Run locally

```bash
uv sync
uv run python -m clio_mcp          # listens on 0.0.0.0:8080
```

or with Docker:

```bash
docker build --platform linux/amd64 -t clio-mcp:dev .
docker run --rm -p 8080:8080 clio-mcp:dev
```

Configuration (environment variables, all optional): `MCP_HTTP_HOST` (default `0.0.0.0`),
`MCP_HTTP_PORT` (default `8080`), `LOG_LEVEL` (default `INFO`). Credentials are never read
from the environment.

## Testing example

```bash
# list tools
curl -s http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Clio-Access-Token: your-access-token" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# open matters of one client
curl -s http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Clio-Access-Token: your-access-token" \
  -H "X-Clio-Region: us" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"clio_matters_list","arguments":{"client_id":123,"status":"open","limit":10}}}'
```

Unit tests (no network access needed):

```bash
uv run pytest
```

## Delivery notes

- **All tools always listed.** Older versions only listed navigation tools until one was
  called. Behind a stateless gateway that hid every other tool, so this version lists all 26
  tools up front. `clio_navigate` and `clio_back` remain for compatibility and only describe
  the tools.
- **No interactive prompts.** Older versions asked the user for missing values mid-call,
  which a stateless gateway cannot relay. The values Clio needs are now required parameters
  (`clio_matters_create.description`, `clio_contacts_create.name` / `type`,
  `clio_activities_create.date` / `type`, `clio_tasks_create.name` / `description` /
  `assignee_id`). A `client_name` that matches several contacts returns the candidates so the
  agent can ask the user and pass `client_id`.
- **Default fields.** List and get tools return a useful default field set instead of Clio's
  bare `id` / `etag`, and lists return a `next_page_token` for the next page.
- **Contact updates.** A new `email` / `phone` replaces the contact's primary entry in place
  instead of adding another entry on every update.
- **Write tools.** The 7 tools marked **write** change the firm's live records; their
  descriptions tell the agent to use them only on explicit request. Time entries take
  `quantity` in seconds. There are no delete, billing or document-content tools.

## License

Apache License 2.0 - see [LICENSE](LICENSE) and [NOTICE](NOTICE).
