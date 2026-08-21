"""Apifox Token 探活。设置页 `auth_cli` 走这里；MCP 会话握手在 client.py。"""
from __future__ import annotations

import json
from typing import Any

import httpx

API_BASE = "https://api.apifox.com"
MCP_PATH = "/mcp"
REST_API_VERSION = "2024-03-28"
MCP_API_VERSION = "2025-09-01"
MAX_REDIRECTS = 5


class ApifoxAuthError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def rest_headers(token: str, project_id: str = "") -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Apifox-Api-Version": REST_API_VERSION,
    }
    if project_id:
        headers["X-Project-Id"] = str(project_id)
    return headers


def detail_headers(token: str, project_id: str) -> dict[str, str]:
    """详情 REST 必带 `X-Project-Id`，缺了平台返回 422。"""
    project = str(project_id or "").strip()
    if not project:
        raise ApifoxAuthError("missing", "Apifox 读详情需要项目 ID")
    return rest_headers(token, project)


def mcp_headers(token: str, session_id: str = "") -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Apifox-Api-Version": MCP_API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return headers


def mcp_initialize_body() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "specweaver", "version": "0.1.5"},
        },
    }


def is_same_origin(url: httpx.URL, base: httpx.URL) -> bool:
    return (
        url.scheme == base.scheme
        and url.host == base.host
        and url.port == base.port
    )


def next_same_origin_redirect(
    response: httpx.Response,
    base: httpx.URL,
) -> httpx.URL | None:
    """下一跳只允许同源。跨源直接报错，避免 Bearer 跟到第三方。"""
    if not response.is_redirect:
        return None
    location = response.headers.get("location")
    if not location:
        return None
    target = response.url.join(location)
    if not is_same_origin(target, base):
        raise ApifoxAuthError(
            "network",
            f"Apifox 重定向目标 {target.host or location} 与 api.apifox.com 不同源",
        )
    return target


def redirect_request_kwargs(
    response: httpx.Response,
    target: httpx.URL,
) -> dict[str, Any]:
    """307/308 保方法+body，其余改 GET。探活和 MCP 共用，避免两边跟跳规则分叉。"""
    original = response.request
    method = original.method if response.status_code in {307, 308} else "GET"
    headers = dict(original.headers)
    headers.pop("content-length", None)
    kwargs: dict[str, Any] = {
        "method": method,
        "url": str(target),
        "headers": headers,
    }
    if method != "GET":
        kwargs["content"] = original.content
    return kwargs


def follow_same_origin(
    client: httpx.Client,
    response: httpx.Response,
    base_url: str = API_BASE,
) -> httpx.Response:
    base = httpx.URL(base_url)
    for _ in range(MAX_REDIRECTS):
        target = next_same_origin_redirect(response, base)
        if target is None:
            return response
        response = client.request(**redirect_request_kwargs(response, target))
    raise ApifoxAuthError("network", "Apifox 重定向次数过多")


def parse_mcp_http_body(response: httpx.Response) -> Any:
    content_type = (response.headers.get("content-type") or "").lower()
    text = response.text
    if "text/event-stream" in content_type or text.lstrip().startswith(
        ("event:", "data:")
    ):
        payload = None
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            payload = json.loads(data)
        if payload is None:
            raise RuntimeError("Apifox MCP 未返回 JSON")
        return payload
    if not text.strip():
        raise RuntimeError("Apifox MCP 返回空响应")
    return response.json()


def session_id_from(response: httpx.Response) -> str:
    return (
        response.headers.get("mcp-session-id")
        or response.headers.get("Mcp-Session-Id")
        or ""
    ).strip()


def require_initialize_ok(response: httpx.Response) -> str:
    """握手必须是可解析的 initialize 结果，并带上会话头。

    只看 HTTP `< 400` 会把 `201 text/html` 空包当成 Token 有效。
    """
    if response.status_code in {401, 403}:
        raise ApifoxAuthError("credentials", "Apifox Token 无效或权限不足")
    if response.status_code >= 400:
        response.raise_for_status()
    try:
        payload = parse_mcp_http_body(response)
    except (json.JSONDecodeError, ValueError, RuntimeError) as error:
        raise ApifoxAuthError(
            "compatibility",
            f"Apifox 响应无法解析: {error}",
        ) from error
    if not isinstance(payload, dict):
        raise ApifoxAuthError("compatibility", "Apifox 握手返回无法识别")
    if payload.get("error"):
        error = payload["error"]
        message = (
            str(error.get("message") or error)
            if isinstance(error, dict)
            else str(error)
        )
        raise ApifoxAuthError("compatibility", f"Apifox 握手失败: {message}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ApifoxAuthError("compatibility", "Apifox 握手未返回 initialize 结果")
    session_id = session_id_from(response)
    if not session_id:
        raise ApifoxAuthError("compatibility", "Apifox 握手未返回会话")
    return session_id


def verify_token(
    token: str,
    *,
    timeout: float = 20,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """同步探活，给设置页 `auth_cli` 用。不回 Token。"""
    value = token.strip()
    if not value:
        raise ApifoxAuthError("missing", "请填写 Apifox Token")
    try:
        with httpx.Client(
            base_url=API_BASE,
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        ) as client:
            response = follow_same_origin(
                client,
                client.post(
                    MCP_PATH,
                    json=mcp_initialize_body(),
                    headers=mcp_headers(value),
                ),
            )
        require_initialize_ok(response)
    except ApifoxAuthError:
        raise
    except httpx.HTTPError as error:
        raise ApifoxAuthError(
            "network",
            f"Apifox 连接失败: {error.__class__.__name__}",
        ) from error
    except (json.JSONDecodeError, ValueError) as error:
        raise ApifoxAuthError(
            "compatibility",
            f"Apifox 响应无法解析: {error}",
        ) from error
