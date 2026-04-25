#!/usr/bin/env python3
"""Odoo MCP Server — Cloud SSE Edition.

Run locally:  python server.py
Deploy:       Set env vars and expose PORT (default 8000).
MCP config:   { "type": "sse", "url": "https://<host>/sse" }
              Add header  Authorization: Bearer <MCP_SECRET>  if MCP_SECRET is set.
"""

import json
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field, ConfigDict, field_validator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

ODOO_URL = os.environ.get("ODOO_URL", "").rstrip("/")
ODOO_DB = os.environ.get("ODOO_DB", "")
ODOO_USERNAME = os.environ.get("ODOO_USERNAME", "")
ODOO_PASSWORD = os.environ.get("ODOO_PASSWORD", "")
MCP_SECRET = os.environ.get("MCP_SECRET", "")
PORT = int(os.environ.get("PORT", "8000"))
HOST = os.environ.get("HOST", "0.0.0.0")

_RPC_ID = 0


def _next_id() -> int:
    global _RPC_ID
    _RPC_ID += 1
    return _RPC_ID


class OdooSession:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=60.0)
        self.uid: Optional[int] = None
        self.authenticated: bool = False

    async def authenticate(self) -> int:
        resp = await self.client.post(
            f"{ODOO_URL}/web/session/authenticate",
            json={"jsonrpc": "2.0", "method": "call", "id": _next_id(),
                  "params": {"db": ODOO_DB, "login": ODOO_USERNAME, "password": ODOO_PASSWORD}},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise RuntimeError(data["error"]["data"]["message"])
        uid = data["result"].get("uid")
        if not uid:
            raise RuntimeError("Authentication failed: invalid credentials or database.")
        self.uid = uid
        self.authenticated = True
        return uid

    async def call_kw(self, model: str, method: str, args: list, kwargs: dict) -> Any:
        resp = await self.client.post(
            f"{ODOO_URL}/web/dataset/call_kw",
            json={"jsonrpc": "2.0", "method": "call", "id": _next_id(),
                  "params": {"model": model, "method": method, "args": args, "kwargs": kwargs}},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            err = data["error"]
            msg = err.get("data", {}).get("message") or err.get("message", str(err))
            raise RuntimeError(f"Odoo RPC error: {msg}")
        return data["result"]

    async def close(self) -> None:
        await self.client.aclose()


_session: Optional[OdooSession] = None


@asynccontextmanager
async def app_lifespan(app):
    global _session
    _session = OdooSession()
    if ODOO_URL and ODOO_DB and ODOO_USERNAME and ODOO_PASSWORD:
        try:
            uid = await _session.authenticate()
            print(f"Odoo authenticated uid={uid}", file=sys.stderr)
        except Exception as e:
            print(f"WARNING: Odoo auth failed: {e}", file=sys.stderr)
    else:
        print("WARNING: Missing ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_PASSWORD env vars", file=sys.stderr)
    yield {}
    await _session.close()


mcp = FastMCP(
    "odoo_mcp",
    lifespan=app_lifespan,
    # Disable DNS rebinding protection — we use Bearer token auth instead
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _ensure_auth() -> str:
    if _session is None or not _session.authenticated:
        return "Error: Not authenticated. Check ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD env vars."
    return ""


def _handle_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"Error: HTTP {e.response.status_code} from Odoo."
    if isinstance(e, httpx.TimeoutException):
        return "Error: Request timed out."
    if isinstance(e, httpx.ConnectError):
        return f"Error: Cannot connect to {ODOO_URL}."
    return f"Error: {e}"


# ── Input models ─────────────────────────────────────────────────────────────

class SearchReadInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    model: str = Field(..., description="Odoo model name e.g. res.partner, sale.order, account.move")
    domain: str = Field(default="[]", description='Domain filter as JSON array e.g. [["state","=","sale"]]')
    fields: Optional[List[str]] = Field(default=None, description="Field names to return. None returns all.")
    limit: Optional[int] = Field(default=20, ge=1, le=500)
    offset: Optional[int] = Field(default=0, ge=0)
    order: Optional[str] = Field(default=None, description="Sort order e.g. 'date_order desc'")

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, v: str) -> str:
        try:
            if not isinstance(json.loads(v), list):
                raise ValueError("Domain must be a JSON array")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}")
        return v


class GetRecordInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    model: str
    record_id: int = Field(..., ge=1)
    fields: Optional[List[str]] = None


class SearchCountInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    model: str
    domain: str = Field(default="[]")

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, v: str) -> str:
        try:
            if not isinstance(json.loads(v), list):
                raise ValueError("Domain must be a JSON array")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}")
        return v


class CreateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    model: str
    values: str

    @field_validator("values")
    @classmethod
    def validate_values(cls, v: str) -> str:
        try:
            if not isinstance(json.loads(v), dict):
                raise ValueError("values must be a JSON object")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}")
        return v


class WriteInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    model: str
    ids: List[int] = Field(..., min_length=1)
    values: str

    @field_validator("values")
    @classmethod
    def validate_values(cls, v: str) -> str:
        try:
            if not isinstance(json.loads(v), dict):
                raise ValueError("values must be a JSON object")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}")
        return v


class UnlinkInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    model: str
    ids: List[int] = Field(..., min_length=1)


class FieldsGetInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    model: str
    attributes: Optional[List[str]] = None
    filter_type: Optional[str] = None


class ExecuteInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    model: str
    method: str
    ids: Optional[List[int]] = None
    args: Optional[str] = Field(default="[]")
    kwargs: Optional[str] = Field(default="{}")

    @field_validator("args")
    @classmethod
    def validate_args(cls, v: str) -> str:
        try:
            if not isinstance(json.loads(v), list):
                raise ValueError("args must be a JSON array")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}")
        return v

    @field_validator("kwargs")
    @classmethod
    def validate_kwargs(cls, v: str) -> str:
        try:
            if not isinstance(json.loads(v), dict):
                raise ValueError("kwargs must be a JSON object")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}")
        return v


class NameSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    model: str
    name: str
    domain: str = Field(default="[]")
    limit: Optional[int] = Field(default=20, ge=1, le=200)

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, v: str) -> str:
        try:
            if not isinstance(json.loads(v), list):
                raise ValueError("Domain must be a JSON array")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}")
        return v


class ListModelsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    filter_name: Optional[str] = None
    limit: Optional[int] = Field(default=50, ge=1, le=500)
    offset: Optional[int] = Field(default=0, ge=0)


class DigestGetInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    digest_id: int = Field(..., ge=1, description="ID of the digest.digest record")


class DigestSendInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    digest_id: int = Field(..., ge=1, description="ID of the digest.digest record to send immediately")


class DigestUpdateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    digest_id: int = Field(..., ge=1, description="ID of the digest.digest record to update")
    periodicity: Optional[str] = Field(
        default=None,
        description="Sending frequency: 'daily', 'weekly', or 'monthly'",
    )
    state: Optional[str] = Field(
        default=None,
        description="Digest state: 'activated' or 'deactivated'",
    )
    next_run_date: Optional[str] = Field(
        default=None,
        description="Override next scheduled run date (ISO format YYYY-MM-DD)",
    )


# ── Tools (identical to local version) ───────────────────────────────────────

@mcp.tool(name="odoo_search_read", annotations={"readOnlyHint": True})
async def odoo_search_read(params: SearchReadInput) -> str:
    """Search and read records from any Odoo model."""
    err = _ensure_auth()
    if err:
        return err
    try:
        domain = json.loads(params.domain)
        kw: Dict[str, Any] = {"limit": params.limit, "offset": params.offset}
        if params.fields:
            kw["fields"] = params.fields
        if params.order:
            kw["order"] = params.order
        records = await _session.call_kw(params.model, "search_read", [domain], kw)
        return json.dumps({"model": params.model, "count": len(records), "offset": params.offset, "records": records}, indent=2, default=str)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="odoo_get_record", annotations={"readOnlyHint": True})
async def odoo_get_record(params: GetRecordInput) -> str:
    """Get a single Odoo record by its integer ID."""
    err = _ensure_auth()
    if err:
        return err
    try:
        kw: Dict[str, Any] = {}
        if params.fields:
            kw["fields"] = params.fields
        records = await _session.call_kw(params.model, "read", [[params.record_id]], kw)
        if not records:
            return f"Error: Record id={params.record_id} not found in '{params.model}'."
        return json.dumps(records[0], indent=2, default=str)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="odoo_search_count", annotations={"readOnlyHint": True})
async def odoo_search_count(params: SearchCountInput) -> str:
    """Count records in an Odoo model matching a domain filter."""
    err = _ensure_auth()
    if err:
        return err
    try:
        domain = json.loads(params.domain)
        count = await _session.call_kw(params.model, "search_count", [domain], {})
        return json.dumps({"model": params.model, "count": count}, indent=2)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="odoo_create")
async def odoo_create(params: CreateInput) -> str:
    """Create a new record in any Odoo model."""
    err = _ensure_auth()
    if err:
        return err
    try:
        vals = json.loads(params.values)
        new_id = await _session.call_kw(params.model, "create", [vals], {})
        return json.dumps({"model": params.model, "id": new_id, "status": "created"}, indent=2)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="odoo_write")
async def odoo_write(params: WriteInput) -> str:
    """Update one or more records in an Odoo model."""
    err = _ensure_auth()
    if err:
        return err
    try:
        vals = json.loads(params.values)
        result = await _session.call_kw(params.model, "write", [params.ids, vals], {})
        return json.dumps({"model": params.model, "ids": params.ids, "success": result}, indent=2)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="odoo_unlink", annotations={"destructiveHint": True})
async def odoo_unlink(params: UnlinkInput) -> str:
    """Permanently delete records from an Odoo model. WARNING: Irreversible."""
    err = _ensure_auth()
    if err:
        return err
    try:
        result = await _session.call_kw(params.model, "unlink", [params.ids], {})
        return json.dumps({"model": params.model, "ids": params.ids, "deleted": result}, indent=2)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="odoo_fields_get", annotations={"readOnlyHint": True})
async def odoo_fields_get(params: FieldsGetInput) -> str:
    """Get field definitions for an Odoo model."""
    err = _ensure_auth()
    if err:
        return err
    try:
        kw: Dict[str, Any] = {}
        if params.attributes:
            kw["attributes"] = params.attributes
        fields = await _session.call_kw(params.model, "fields_get", [], kw)
        if params.filter_type:
            fields = {k: v for k, v in fields.items() if v.get("type") == params.filter_type}
        return json.dumps({"model": params.model, "fields": fields}, indent=2, default=str)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="odoo_name_search", annotations={"readOnlyHint": True})
async def odoo_name_search(params: NameSearchInput) -> str:
    """Search Odoo records by display name (partial match)."""
    err = _ensure_auth()
    if err:
        return err
    try:
        domain = json.loads(params.domain)
        results = await _session.call_kw(params.model, "name_search", [], {"name": params.name, "args": domain, "limit": params.limit})
        items = [{"id": r[0], "name": r[1]} for r in results]
        return json.dumps({"model": params.model, "query": params.name, "count": len(items), "results": items}, indent=2)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="odoo_execute")
async def odoo_execute(params: ExecuteInput) -> str:
    """Execute any method on an Odoo model (e.g. action_confirm, button_validate)."""
    err = _ensure_auth()
    if err:
        return err
    try:
        extra_args = json.loads(params.args or "[]")
        kw = json.loads(params.kwargs or "{}")
        call_args = [params.ids] + extra_args if params.ids is not None else extra_args
        result = await _session.call_kw(params.model, params.method, call_args, kw)
        return json.dumps({"model": params.model, "method": params.method, "result": result}, indent=2, default=str)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="odoo_list_models", annotations={"readOnlyHint": True})
async def odoo_list_models(params: ListModelsInput) -> str:
    """List all installed Odoo models."""
    err = _ensure_auth()
    if err:
        return err
    try:
        domain: list = []
        if params.filter_name:
            domain = ["|", ["model", "ilike", params.filter_name], ["name", "ilike", params.filter_name]]
        records = await _session.call_kw("ir.model", "search_read", [domain], {"fields": ["model", "name"], "limit": params.limit, "offset": params.offset, "order": "model asc"})
        total = await _session.call_kw("ir.model", "search_count", [domain], {})
        return json.dumps({"total": total, "count": len(records), "models": records, "has_more": total > params.offset + len(records)}, indent=2)
    except Exception as e:
        return _handle_error(e)


_DIGEST_FIELDS = [
    "id", "name", "periodicity", "state", "next_run_date",
    "user_ids", "company_id",
    "kpi_mail_message_total", "kpi_res_users_connected",
    "kpi_account_total_revenue", "kpi_crm_lead_created",
    "kpi_crm_opportunities_won",
]


@mcp.tool(name="odoo_digest_list", annotations={"readOnlyHint": True})
async def odoo_digest_list() -> str:
    """List all configured Odoo digests with their schedule and activation state."""
    err = _ensure_auth()
    if err:
        return err
    try:
        records = await _session.call_kw(
            "digest.digest", "search_read", [[]],
            {"fields": ["id", "name", "periodicity", "state", "next_run_date", "company_id"], "order": "id asc"},
        )
        return json.dumps({"count": len(records), "digests": records}, indent=2, default=str)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="odoo_digest_get", annotations={"readOnlyHint": True})
async def odoo_digest_get(params: DigestGetInput) -> str:
    """Get full details and KPI values for a specific Odoo digest."""
    err = _ensure_auth()
    if err:
        return err
    try:
        records = await _session.call_kw(
            "digest.digest", "read", [[params.digest_id]],
            {"fields": _DIGEST_FIELDS},
        )
        if not records:
            return f"Error: Digest id={params.digest_id} not found."
        return json.dumps(records[0], indent=2, default=str)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="odoo_digest_send")
async def odoo_digest_send(params: DigestSendInput) -> str:
    """Immediately send an Odoo digest email to its configured recipients."""
    err = _ensure_auth()
    if err:
        return err
    try:
        await _session.call_kw("digest.digest", "action_send", [[params.digest_id]], {})
        return json.dumps({"digest_id": params.digest_id, "status": "sent"}, indent=2)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="odoo_digest_update")
async def odoo_digest_update(params: DigestUpdateInput) -> str:
    """Update an Odoo digest's periodicity, activation state, or next run date."""
    err = _ensure_auth()
    if err:
        return err
    vals: Dict[str, Any] = {}
    if params.periodicity is not None:
        if params.periodicity not in ("daily", "weekly", "monthly"):
            return "Error: periodicity must be 'daily', 'weekly', or 'monthly'."
        vals["periodicity"] = params.periodicity
    if params.state is not None:
        if params.state not in ("activated", "deactivated"):
            return "Error: state must be 'activated' or 'deactivated'."
        vals["state"] = params.state
    if params.next_run_date is not None:
        vals["next_run_date"] = params.next_run_date
    if not vals:
        return "Error: Provide at least one field to update (periodicity, state, or next_run_date)."
    try:
        result = await _session.call_kw("digest.digest", "write", [[params.digest_id], vals], {})
        return json.dumps({"digest_id": params.digest_id, "updated_fields": list(vals.keys()), "success": result}, indent=2)
    except Exception as e:
        return _handle_error(e)


# ── Auth middleware ───────────────────────────────────────────────────────────

class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not MCP_SECRET:
            return await call_next(request)
        # Allow health check without auth
        if request.url.path == "/health":
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != MCP_SECRET:
            return Response("Unauthorized", status_code=401)
        return await call_next(request)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Starting Odoo MCP server on {HOST}:{PORT}", file=sys.stderr)
    if not MCP_SECRET:
        print("WARNING: MCP_SECRET not set — server is open to anyone", file=sys.stderr)

    # Streamable-HTTP transport — endpoint: POST /mcp
    # Claude Code: claude mcp add --transport http odoo-cloud https://<host>/mcp
    starlette_app = mcp.streamable_http_app()
    starlette_app.add_middleware(BearerAuthMiddleware)
    uvicorn.run(
        starlette_app,
        host=HOST,
        port=PORT,
        log_level="info",
        forwarded_allow_ips="*",
        proxy_headers=True,
    )
