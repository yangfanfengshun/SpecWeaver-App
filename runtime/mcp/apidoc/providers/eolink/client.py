# 依赖统一声明在 runtime/pyproject.toml，版本由 uv.lock 钉死；
# 由 scripts/run-mcp.sh 通过 `uv run --project` 提供，不要在这里再写一份。
"""apidoc 能力的 eolink provider：会话/HTTP 层 + 工具级实现。

不自带 FastMCP 实例——工具由 `apidoc/server.py` 定义并经 provider 路由
调进来。本模块暴露的 check_auth / read 就是 apidoc 能力的 provider 统一接口。
"""
from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from apidoc.providers.eolink.auth import HEADERS, LOGIN_PATH, SUCCESS_CODE
from apidoc.providers.eolink.normalize import from_detail, from_list
from apidoc.schema import envelope, stringify_id, success
from common import http_error_result, read_config

SESSION_EXPIRED_CODE = "120005"
MAX_REDIRECTS = 5

_client: httpx.AsyncClient | None = None
_client_base_url = ""
_login_fingerprint = ""


def current_settings() -> tuple[str, str, str]:
    config = read_config()
    return (
        config["EOLINK_BASE_URL"].rstrip("/"),
        config["EOLINK_USER"],
        config["EOLINK_PASSWORD"],
    )


def parse_url(url: str, base_url: str | None = None) -> dict[str, Any]:
    parsed = urlparse(url)
    expected_host = urlparse(base_url or current_settings()[0]).netloc.lower()
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("请提供有效的 Eolink 链接")
    if expected_host and parsed.netloc.lower() != expected_host:
        raise ValueError(f"链接域名与 EOLINK_BASE_URL 不一致: {parsed.netloc}")

    fragment_query = parsed.fragment.partition("?")[2]
    query = parse_qs(fragment_query or parsed.query)

    def first(name: str) -> str:
        return (query.get(name) or [""])[0].strip()

    project_id = first("projectID")
    if not project_id.isdigit():
        raise ValueError("Eolink 链接中缺少有效的 projectID")

    child_group_id = first("childGroupID")
    group_id = child_group_id if child_group_id not in {"", "0", "-1"} else first("groupID")
    if group_id and group_id != "-1" and not group_id.isdigit():
        raise ValueError("Eolink 链接中的 groupID 无效")

    parsed: dict[str, Any] = {
        "projectID": int(project_id),
        "groupID": int(group_id) if group_id and group_id != "-1" else -1,
        "projectName": first("projectName"),
    }
    api_id = first("apiID")
    if api_id.isdigit():
        parsed["apiID"] = int(api_id)
    return parsed


async def get_client(base_url: str) -> httpx.AsyncClient:
    global _client, _client_base_url, _login_fingerprint
    if not base_url:
        raise RuntimeError("未设置 EOLINK_BASE_URL")
    if _client is None or _client_base_url != base_url:
        if _client is not None:
            await _client.aclose()
        # follow_redirects=False：自动跟随会把带着账号密码或会话 Cookie 的请求
        # 一路跟到重定向指定的任意地址。改为手动跟跳并校验同源，与 tower/蓝湖一致。
        _client = httpx.AsyncClient(base_url=base_url, timeout=30, follow_redirects=False)
        _client_base_url = base_url
        _login_fingerprint = ""
    return _client


def is_same_origin(url: httpx.URL, base: httpx.URL) -> bool:
    return (
        url.scheme == base.scheme
        and url.host == base.host
        and url.port == base.port
    )


async def resolve_redirects(
    client: httpx.AsyncClient,
    response: httpx.Response,
    base_url: str,
) -> httpx.Response:
    """跟随重定向，但只跟到与 EOLINK_BASE_URL 同源的地址。

    同源跳转照常跟随（部分部署会在登录前跳一次），跳出配置域名则直接报错，
    避免把凭据或会话 Cookie 送到第三方地址。
    """
    base = httpx.URL(base_url)
    for _ in range(MAX_REDIRECTS):
        if not response.is_redirect:
            return response
        location = response.headers.get("location")
        if not location:
            return response
        target = response.url.join(location)
        if not is_same_origin(target, base):
            raise RuntimeError(
                f"Eolink 重定向目标 {target.host or location} 与 EOLINK_BASE_URL 不同源"
            )
        response = await client.get(target, headers=HEADERS)
    raise RuntimeError("Eolink 重定向次数过多")


async def ensure_login(force: bool = False) -> None:
    global _login_fingerprint
    base_url, user, password = current_settings()
    if not user or not password:
        raise RuntimeError("未设置 EOLINK_USER 或 EOLINK_PASSWORD")
    fingerprint = hashlib.sha256(f"{base_url}\0{user}\0{password}".encode()).hexdigest()
    if _login_fingerprint == fingerprint and not force:
        return

    client = await get_client(base_url)
    await resolve_redirects(client, await client.get("/"), base_url)
    response = await resolve_redirects(
        client,
        await client.post(
            LOGIN_PATH,
            data={
                "loginName": user,
                "loginPassword": hashlib.md5(password.encode()).hexdigest(),
            },
            headers={**HEADERS, "Origin": base_url, "Referer": f"{base_url}/"},
        ),
        base_url,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("statusCode") != SUCCESS_CODE:
        raise RuntimeError(f"Eolink 认证信息无效（状态码 {payload.get('statusCode') or '未知'}）")
    _login_fingerprint = fingerprint


async def api_post(path: str, form: dict[str, Any], retry: bool = True) -> dict:
    global _login_fingerprint
    await ensure_login()
    base_url = current_settings()[0]
    client = await get_client(base_url)
    response = await resolve_redirects(
        client,
        await client.post(
            path,
            data=form,
            headers={**HEADERS, "Origin": base_url, "Referer": f"{base_url}/"},
        ),
        base_url,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("statusCode") == SESSION_EXPIRED_CODE and retry:
        _login_fingerprint = ""
        await ensure_login(force=True)
        return await api_post(path, form, retry=False)
    if payload.get("statusCode") == SESSION_EXPIRED_CODE:
        raise RuntimeError("Eolink 登录会话已失效，请在 SpecWeaver 设置页重新配置 Eolink 后重试")
    return payload


def auth_error(error: Exception) -> dict[str, str]:
    mapped = http_error_result(
        error,
        platform="eolink",
        status_by_code={401: "auth_expired", 403: "forbidden"},
        default_status="network_error",
    )
    if mapped:
        return mapped
    message = str(error)
    status = "auth_expired" if any(
        word in message for word in ("认证信息无效", "登录会话已失效")
    ) else "api_error"
    return {"status": status, "platform": "eolink", "message": message}


async def check_auth() -> dict[str, str]:
    """检查 Eolink 配置和认证状态，不返回账号或密码。"""
    base_url, user, password = current_settings()
    missing = [
        name
        for name, value in (
            ("EOLINK_BASE_URL", base_url),
            ("EOLINK_USER", user),
            ("EOLINK_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        return {
            "status": "missing_config",
            "platform": "eolink",
            "message": f"未设置 {', '.join(missing)}",
        }
    try:
        await ensure_login(force=True)
    except Exception as error:
        return auth_error(error)
    return {"status": "success", "platform": "eolink", "message": "Eolink 认证有效"}


async def read(url: str = "", api_id: Any = None) -> dict[str, Any]:
    """读取名单或一条详情，出口是中立信封 `{status, platform, message, data}`。

    目录 / 项目链接默认只给 list，不顺手把下面所有接口详情拉回来。
    带 `api_id`，或链接里能抠到 `apiID`，才走详情。
    """
    requested_id = stringify_id(api_id)
    location: dict[str, Any] | None = None
    if url:
        try:
            location = parse_url(url)
        except ValueError as error:
            return envelope("invalid_input", "eolink", str(error))
        if not requested_id and location.get("apiID") not in (None, ""):
            requested_id = stringify_id(location["apiID"])
    try:
        if requested_id and not url:
            detail = await _read_detail(requested_id, None)
            if detail.get("status") == "success":
                return detail
            if detail.get("status") in {
                "missing_config",
                "auth_expired",
                "forbidden",
                "network_error",
            }:
                return detail
            try:
                project_id = int(requested_id)
            except ValueError:
                return detail
            listed = await _read_project(project_id)
            if listed.get("status") == "success":
                return listed
            return detail
        if requested_id:
            return await _read_detail(requested_id, location)
        if not url:
            return envelope("invalid_input", "eolink", "read 需要 url 或 api_id")
        return await _read_list(url, location or {})
    except Exception as error:
        return auth_error(error)


async def _read_project(project_id: int) -> dict[str, Any]:
    raw = await api_post(
        "/server/index.php?g=Web&c=Api&o=getAllApiList",
        {"projectID": project_id, "groupID": "-1"},
    )
    status_code = raw.get("statusCode")
    if status_code is not None and str(status_code) != SUCCESS_CODE:
        return envelope(
            "api_error",
            "eolink",
            f"项目 {project_id} 返回失败状态 {status_code}",
        )
    data = from_list(raw, location={"project_id": project_id})
    return success("eolink", data)


async def _read_list(url: str, location: dict[str, Any]) -> dict[str, Any]:
    project_id = location["projectID"]
    group_id = location["groupID"]
    if group_id == -1:
        path = "/server/index.php?g=Web&c=Api&o=getAllApiList"
        form = {"projectID": project_id, "groupID": "-1"}
    else:
        path = "/server/index.php?g=Web&c=Api&o=getApiList"
        form = {"projectID": project_id, "groupID": group_id}
    raw = await api_post(path, form)
    override = {"project_id": project_id}
    if group_id != -1:
        override["folder_id"] = group_id
    data = from_list(raw, source_url=url, location=override)
    return success("eolink", data)


async def _read_detail(
    api_id: str,
    location: dict[str, Any] | None,
) -> dict[str, Any]:
    form: dict[str, Any] = {"apiID": api_id}
    if location and location.get("projectID") is not None:
        form["projectID"] = location["projectID"]
    raw = await api_post("/server/index.php?g=Web&c=Api&o=getApi", form)
    status_code = raw.get("statusCode")
    if status_code is not None and str(status_code) != SUCCESS_CODE:
        return envelope(
            "api_error",
            "eolink",
            f"API {api_id} 返回失败状态 {status_code}",
        )
    override = None
    if location:
        override = {"project_id": location.get("projectID")}
        group_id = location.get("groupID")
        if group_id not in (None, -1):
            override["folder_id"] = group_id
    data = from_detail(raw, location=override)
    actual_id = data.get("api_id")
    if actual_id in (None, ""):
        return envelope("api_error", "eolink", "接口详情缺少 API ID")
    if str(actual_id) != str(api_id):
        return envelope(
            "api_error",
            "eolink",
            f"来源返回 API ID {actual_id}，与确认的 {api_id} 不一致",
        )
    return success("eolink", data)
