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
"""Async client for the Clio Manage REST API (v4)."""

import asyncio
import logging
import time
from dataclasses import dataclass
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from ._json import error_envelope
from .config import API_PATH, DEFAULT_REGION, REGION_HOSTS

logger = logging.getLogger("clio_mcp.api")

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_MAX_RETRIES = 2
_MAX_BACKOFF_SECONDS = 20.0
# Whole-call budget including retries; the gateway times a tool call out at 120s.
_CALL_BUDGET_SECONDS = 100.0

# One connection pool for the process. It never holds credentials: the
# Authorization header is built per request from the caller's own token, so
# sharing the pool across tenants is safe.
_shared_http_client: httpx.AsyncClient | None = None


class _RejectAllCookies(DefaultCookiePolicy):
    """Never store or send a cookie: the HTTP client is shared by every tenant."""

    def set_ok(self, cookie, request) -> bool:  # noqa: ANN001 - stdlib signature
        return False

    def return_ok(self, cookie, request) -> bool:  # noqa: ANN001 - stdlib signature
        return False


def _no_cookies() -> CookieJar:
    # A plain CookieJar: httpx uses it as is (an httpx.Cookies would be copied into a default jar).
    return CookieJar(policy=_RejectAllCookies())


def _get_shared_http_client() -> httpx.AsyncClient:
    global _shared_http_client
    if _shared_http_client is None:
        _shared_http_client = httpx.AsyncClient(cookies=_no_cookies(), timeout=_TIMEOUT)
    return _shared_http_client


# status_code -> (error code, retryable). 0 means no HTTP response at all
# (connection error or timeout).
_STATUS_TO_CODE: dict[int, tuple[str, bool]] = {
    0: ("upstream_error", True),
    400: ("invalid_argument", False),
    401: ("unauthorized", False),
    403: ("unauthorized", False),
    404: ("not_found", False),
    422: ("invalid_argument", False),
    429: ("rate_limited", True),
}


def classify_status(status_code: int) -> tuple[str, bool]:
    if status_code in _STATUS_TO_CODE:
        return _STATUS_TO_CODE[status_code]
    if status_code >= 500:
        return "upstream_error", True
    if status_code >= 400:
        return "invalid_argument", False
    return "upstream_error", True


class ClioError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Clio API error {status_code}: {message}")

    def to_envelope(self) -> str:
        code, retryable = classify_status(self.status_code)
        return error_envelope(code, self.message, retryable)


@dataclass(frozen=True)
class ClioCredentials:
    access_token: str | None = None
    refresh_token: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    region: str = DEFAULT_REGION
    # Set when a header value is malformed: tools then return invalid_argument
    # instead of calling Clio, while tools/list still works.
    header_error: str | None = None

    @property
    def can_refresh(self) -> bool:
        return bool(self.refresh_token and self.client_id and self.client_secret)

    def __repr__(self) -> str:  # never leak secrets into logs or tracebacks
        return (
            f"ClioCredentials(region={self.region!r}, access_token={'<set>' if self.access_token else None}, "
            f"can_refresh={self.can_refresh}, secrets=<redacted>)"
        )


class ClioClient:
    """Clio API wrapper for one caller's credentials.

    A refreshed access token is kept only on this instance, which lives for a
    single MCP request; nothing is cached across requests.
    """

    def __init__(self, credentials: ClioCredentials, http_client: httpx.AsyncClient | None = None):
        self._credentials = credentials
        self._http = http_client
        self.header_error = credentials.header_error
        self.host = REGION_HOSTS.get(credentials.region, REGION_HOSTS[DEFAULT_REGION])
        self.base_url = f"{self.host}{API_PATH}"
        self._token = credentials.access_token
        self._refreshed = False

    @property
    def region(self) -> str:
        return self._credentials.region

    @property
    def can_refresh(self) -> bool:
        return self._credentials.can_refresh

    def _client(self) -> httpx.AsyncClient:
        return self._http or _get_shared_http_client()

    async def refresh(self) -> None:
        """Exchange the refresh token for a new access token (kept for this request only)."""
        creds = self._credentials
        if not creds.can_refresh:
            raise ClioError(401, "The Clio access token was rejected and no refresh token + client id/secret was provided")
        form = {
            "grant_type": "refresh_token",
            "refresh_token": creds.refresh_token,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
        }
        try:
            resp = await self._client().post(f"{self.host}/oauth/token", data=form, headers={"Accept": "application/json"})
        except httpx.RequestError as e:
            raise ClioError(0, f"Network error refreshing the Clio access token: {type(e).__name__}") from e
        logger.info("clio_api method=POST path=/oauth/token status=%s", resp.status_code)
        if resp.status_code in (400, 401, 403):
            raise ClioError(401, "Clio rejected the refresh token or the client id / secret; reconnect the Clio integration")
        if resp.status_code >= 400:
            raise ClioError(resp.status_code, f"Clio token endpoint returned HTTP {resp.status_code}")
        try:
            token = resp.json().get("access_token")
        except (ValueError, AttributeError):
            token = None
        if not token:
            raise ClioError(502, "Clio token response had no access_token")
        self._token = token
        self._refreshed = True

    # ---- requests -------------------------------------------------------

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, data: dict[str, Any], params: dict[str, Any] | None = None) -> Any:
        return await self.request("POST", path, params=params, body={"data": data})

    async def patch(self, path: str, data: dict[str, Any], params: dict[str, Any] | None = None) -> Any:
        return await self.request("PATCH", path, params=params, body={"data": data})

    async def request(self, method: str, path: str, params: dict[str, Any] | None = None, body: Any = None) -> Any:
        """Call ``{base}/{path}.json``; on 401 refreshes the access token once and retries."""
        if not self._token:
            await self.refresh()
        resp = await self._send(method, path, params, body)
        if resp.status_code == 401 and self.can_refresh and not self._refreshed:
            await self.refresh()
            resp = await self._send(method, path, params, body)
        if resp.status_code >= 400:
            raise ClioError(resp.status_code, _error_message(resp, path))
        return _parse_body(resp)

    async def _send(self, method: str, path: str, params: dict[str, Any] | None, body: Any) -> httpx.Response:
        url = f"{self.base_url}/{path.strip('/')}.json"
        query = _query(params)
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        started = time.monotonic()
        attempt = 0
        while True:
            t0 = time.monotonic()
            resp: httpx.Response | None = None
            error: ClioError | None = None
            try:
                resp = await self._client().request(method, url, params=query, json=body, headers=headers)
                status = resp.status_code
            except httpx.TimeoutException:
                status, error = 0, ClioError(0, f"Timed out calling Clio ({method} {path})")
            except httpx.RequestError as e:
                status = 0
                error = ClioError(0, f"Network error calling Clio ({method} {path}): {type(e).__name__}")

            logger.info(
                "clio_api method=%s path=%s status=%s ms=%d attempt=%d",
                method, path, status, (time.monotonic() - t0) * 1000, attempt,
            )

            # 429 is refused before processing, so any method may retry it; other
            # failures are retried only for reads, so a write never runs twice.
            retryable = status == 429 or (method == "GET" and (status == 0 or status >= 500))
            if retryable and attempt < _MAX_RETRIES:
                delay = _retry_delay(resp, attempt)
                if time.monotonic() - started + delay < _CALL_BUDGET_SECONDS:
                    attempt += 1
                    await asyncio.sleep(delay)
                    continue
            if error is not None:
                raise error
            assert resp is not None
            return resp


def _query(params: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Clio query string: booleans as true/false, lists as repeated ``key[]``."""
    pairs: list[tuple[str, str]] = []
    for key, value in (params or {}).items():
        if value is None or value == "":
            continue
        if isinstance(value, (list, tuple)):
            pairs.extend((f"{key}[]", str(v)) for v in value)
        elif isinstance(value, bool):
            pairs.append((key, "true" if value else "false"))
        else:
            pairs.append((key, str(value)))
    return pairs


def page_token_from(next_url: str | None) -> str | None:
    if not next_url:
        return None
    values = parse_qs(urlsplit(next_url).query).get("page_token")
    return values[0] if values else None


def _retry_delay(resp: httpx.Response | None, attempt: int) -> float:
    if resp is not None:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, min(float(retry_after), _MAX_BACKOFF_SECONDS))
            except ValueError:
                pass
    return min(float(2**attempt), _MAX_BACKOFF_SECONDS)


def _parse_body(resp: httpx.Response) -> Any:
    if not resp.content or not resp.content.strip():
        return None
    try:
        return resp.json()
    except ValueError:
        return {"raw_response": resp.text[:2000]}


def _error_message(resp: httpx.Response, path: str) -> str:
    fallback = f"Clio returned HTTP {resp.status_code} for {path}"
    try:
        payload = resp.json()
    except ValueError:
        text = resp.text.strip()
        return f"{fallback}: {text[:300]}" if text else fallback
    if not isinstance(payload, dict):
        return fallback
    error = payload.get("error")
    message = error.get("message") if isinstance(error, dict) else error if isinstance(error, str) else None
    message = message or payload.get("message")
    details = []
    fields = error.get("fields") if isinstance(error, dict) else None
    fields = fields or payload.get("errors")
    if isinstance(fields, dict):
        for name, problems in fields.items():
            for problem in problems if isinstance(problems, list) else [problems]:
                details.append(f"{name} {problem}")
    parts = [p for p in (message, "; ".join(details)) if p]
    return f"{fallback}: {' - '.join(parts)[:500]}" if parts else fallback
