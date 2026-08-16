"""apidoc 能力的 apifox provider：名单走 MCP，详情走 REST。"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from apidoc.providers.apifox.auth import (
    API_BASE,
    MAX_REDIRECTS,
    MCP_PATH,
    ApifoxAuthError,
    detail_headers,
    mcp_headers,
    mcp_initialize_body,
    next_same_origin_redirect,
    parse_mcp_http_body,
    redirect_request_kwargs,
    require_initialize_ok,
)
from apidoc.providers.apifox.normalize import (
    folder_name_from_summary,
    from_endpoint,
    from_structure,
)
from apidoc.schema import envelope, stringify_id, success
from common import http_error_result, read_config

_PROJECT_RE = re.compile(r"/project/(\d+)")
_API_RE = re.compile(r"(?:/apis/)?api-(\d+)")
_FOLDER_RE = re.compile(r"(?:/apis/)?folder-(\d+)")
_MISSING_FOLDER_RE = re.compile(
    r"Folder\s+(\d+)\s+does not exist in project\s+(\d+)",
    re.IGNORECASE,
)

_client: httpx.AsyncClient | None = None
_token_fingerprint = ""
_mcp_session_id = ""
_rpc_id = 1


def current_settings() -> str:
    return read_config()["APIFOX_TOKEN"].strip()


def parse_url(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("请提供有效的 Apifox 链接")
    path = parsed.path or ""
    project_match = _PROJECT_RE.search(path)
    if not project_match:
        raise ValueError("Apifox 链接中缺少有效的 project id")
    location: dict[str, str] = {"project_id": project_match.group(1)}
    api_match = _API_RE.search(path)
    folder_match = _FOLDER_RE.search(path)
    if api_match:
        location["api_id"] = api_match.group(1)
    elif folder_match:
        location["folder_id"] = folder_match.group(1)
    return location


def http_api_url(project_id: str, api_id: str) -> str:
    return f"{API_BASE}/api/v1/projects/{project_id}/http-apis/{api_id}"


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        # follow_redirects=False：自动跟随会把 Bearer 送到任意 Location。
        # 同源跳转手动跟，跨源直接报错，与 Eolink / 蓝湖一致。
        _client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=30,
            follow_redirects=False,
        )
    return _client


async def follow_same_origin(client: httpx.AsyncClient, response: httpx.Response) -> httpx.Response:
    base = httpx.URL(API_BASE)
    for _ in range(MAX_REDIRECTS):
        target = next_same_origin_redirect(response, base)
        if target is None:
            return response
        response = await client.request(**redirect_request_kwargs(response, target))
    raise ApifoxAuthError("network", "Apifox 重定向次数过多")


async def send(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    method_name = method.upper()
    if method_name == "GET":
        response = await client.get(url, **kwargs)
    elif method_name == "POST":
        response = await client.post(url, **kwargs)
    else:
        response = await client.request(method_name, url, **kwargs)
    return await follow_same_origin(client, response)


def reset_session() -> None:
    global _token_fingerprint, _mcp_session_id, _rpc_id
    _token_fingerprint = ""
    _mcp_session_id = ""
    _rpc_id = 1


def auth_error(error: Exception) -> dict[str, str]:
    mapped = http_error_result(
        error,
        platform="apifox",
        status_by_code={
            401: "auth_expired",
            403: "forbidden",
            422: "invalid_input",
        },
        default_status="network_error",
        network_prefix="Apifox ",
    )
    if mapped:
        return mapped
    if isinstance(error, ApifoxAuthError):
        status = {
            "missing": "missing_config",
            "credentials": "auth_expired",
            "network": "network_error",
        }.get(error.kind, "api_error")
        return {"status": status, "platform": "apifox", "message": str(error)}
    return {"status": "api_error", "platform": "apifox", "message": str(error)}


async def check_auth() -> dict[str, str]:
    token = current_settings()
    if not token:
        return {
            "status": "missing_config",
            "platform": "apifox",
            "message": "未设置 APIFOX_TOKEN",
        }
    try:
        await ensure_mcp_session(token)
    except Exception as error:
        return auth_error(error)
    return {"status": "success", "platform": "apifox", "message": "Apifox 认证有效"}


async def read(url: str = "", api_id: Any = None) -> dict[str, Any]:
    requested_id = stringify_id(api_id)
    location: dict[str, str] | None = None
    if url:
        try:
            location = parse_url(url)
        except ValueError as error:
            return envelope("invalid_input", "apifox", str(error))
        if not requested_id:
            requested_id = location.get("api_id") or ""
    try:
        token = current_settings()
        if not token:
            return {
                "status": "missing_config",
                "platform": "apifox",
                "message": "未设置 APIFOX_TOKEN",
            }
        if requested_id:
            project_id = (location or {}).get("project_id") or ""
            if not project_id:
                return envelope(
                    "invalid_input",
                    "apifox",
                    "Apifox 读详情需要项目链接",
                )
            return await _read_detail(token, requested_id, project_id, location)
        if not url or location is None:
            return envelope("invalid_input", "apifox", "read 需要 url 或 api_id")
        return await _read_list(token, url, location)
    except Exception as error:
        return auth_error(error)


async def _read_list(
    token: str,
    url: str,
    location: dict[str, str],
) -> dict[str, Any]:
    project_id = location["project_id"]
    folder_id = location.get("folder_id") or ""
    raw = await fetch_structure(token, project_id, folder_id or None)
    if isinstance(raw, str) and _MISSING_FOLDER_RE.search(raw):
        return envelope("invalid_input", "apifox", raw.strip())
    override: dict[str, Any] = {"project_id": project_id}
    if folder_id:
        override["folder_id"] = folder_id
        name = await _folder_name(token, project_id, folder_id)
        if name:
            override["folder_name"] = name
    data = from_structure(raw, source_url=url, location=override)
    return success("apifox", data)


async def _read_detail(
    token: str,
    api_id: str,
    project_id: str,
    location: dict[str, str] | None,
) -> dict[str, Any]:
    raw = await fetch_http_api(token, project_id, api_id)
    override = {"project_id": project_id}
    if location and location.get("folder_id"):
        override["folder_id"] = location["folder_id"]
    data = from_endpoint(raw, location=override)
    actual_id = data.get("api_id")
    if actual_id in (None, ""):
        return envelope("api_error", "apifox", "接口详情缺少 API ID")
    if str(actual_id) != str(api_id):
        return envelope(
            "api_error",
            "apifox",
            f"来源返回 API ID {actual_id}，与确认的 {api_id} 不一致",
        )
    return success("apifox", data)


async def fetch_structure(
    token: str,
    project_id: str,
    folder_id: str | None = None,
) -> Any:
    arguments: dict[str, Any] = {
        "projectId": int(project_id),
        "entityType": "endpoint",
    }
    if folder_id:
        arguments["folderId"] = int(folder_id)
    return await mcp_tools_call(token, "getStructureInfo", arguments)


async def fetch_http_api(token: str, project_id: str, api_id: str) -> Any:
    client = await get_client()
    response = await send(
        client,
        "GET",
        http_api_url(project_id, api_id),
        headers=detail_headers(token, project_id),
    )
    response.raise_for_status()
    return response.json()


async def _folder_name(token: str, project_id: str, folder_id: str) -> str:
    try:
        summary = await fetch_project_summary(token, project_id)
    except Exception:
        return ""
    return folder_name_from_summary(summary, folder_id)


async def fetch_project_summary(token: str, project_id: str) -> Any:
    return await mcp_tools_call(
        token,
        "getProjectSummary",
        {"projectId": int(project_id)},
    )


def extract_tool_payload(rpc: Any) -> Any:
    if not isinstance(rpc, dict):
        return rpc
    if rpc.get("error"):
        error = rpc["error"]
        if isinstance(error, dict):
            raise RuntimeError(str(error.get("message") or error))
        raise RuntimeError(str(error))
    result = rpc.get("result", rpc)
    if not isinstance(result, dict):
        return result
    if result.get("isError"):
        raise RuntimeError(_tool_text(result) or "Apifox MCP 工具调用失败")
    if "content" in result:
        text = _tool_text(result)
        if not text:
            return result
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            return text
    return result


def _tool_text(result: dict[str, Any]) -> str:
    chunks = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            chunks.append(str(item.get("text") or ""))
    return "\n".join(part for part in chunks if part).strip()


async def ensure_mcp_session(token: str) -> str:
    global _token_fingerprint, _mcp_session_id, _rpc_id
    if _mcp_session_id and _token_fingerprint == token:
        return _mcp_session_id
    client = await get_client()
    response = await send(
        client,
        "POST",
        MCP_PATH,
        json=mcp_initialize_body(),
        headers=mcp_headers(token),
    )
    session_id = require_initialize_ok(response)
    await send(
        client,
        "POST",
        MCP_PATH,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=mcp_headers(token, session_id),
    )
    _token_fingerprint = token
    _mcp_session_id = session_id
    _rpc_id = 1
    return session_id


async def mcp_tools_call(
    token: str,
    name: str,
    arguments: dict[str, Any],
) -> Any:
    global _rpc_id
    session_id = await ensure_mcp_session(token)
    _rpc_id += 1
    client = await get_client()
    response = await send(
        client,
        "POST",
        MCP_PATH,
        json={
            "jsonrpc": "2.0",
            "id": _rpc_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=mcp_headers(token, session_id),
    )
    response.raise_for_status()
    return extract_tool_payload(parse_mcp_http_body(response))
