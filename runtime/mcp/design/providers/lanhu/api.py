from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from common import http_error_result
from design.providers.lanhu.session import lanhu_settings


LANHU_BASE = "https://lanhuapp.com"
SUCCESS_CODE = "00000"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://lanhuapp.com/web/",
    "Accept": "application/json, text/plain, */*",
    "request-from": "web",
}


def parse_lanhu_project_url(url: str) -> dict[str, str | None]:
    parsed = urlparse(url)
    fragment_path, separator, _ = parsed.fragment.partition("?")
    if not separator or "/item/project/stage" not in fragment_path:
        raise ValueError("仅支持蓝湖 stage 标准项目链接")
    params = parse_lanhu_design_url(url)
    return {
        "project_id": params["project_id"],
        "team_id": params["team_id"],
    }


def parse_lanhu_design_url(url: str) -> dict[str, str | None]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "lanhuapp.com":
        raise ValueError("请提供蓝湖 HTTPS 标准项目链接")
    fragment_path, separator, fragment_query = parsed.fragment.partition("?")
    supported_paths = ("/item/project/stage", "/item/project/detailDetach")
    if not separator or not any(path in fragment_path for path in supported_paths):
        raise ValueError("仅支持蓝湖 stage 或 detailDetach 标准设计链接")
    query = parse_qs(fragment_query)
    project_id = (query.get("pid") or query.get("project_id") or [""])[0].strip()
    team_id = (query.get("tid") or query.get("team_id") or [""])[0].strip() or None
    image_id = (query.get("image_id") or [""])[0].strip() or None
    if not project_id:
        raise ValueError("蓝湖链接缺少 pid（project_id）")
    return {
        "project_id": project_id,
        "team_id": team_id,
        "image_id": image_id,
    }


def is_lanhu_image_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (
        host == "lanhuapp.com" or host.endswith(".lanhuapp.com")
    )


def normalize_design_sectors(
    sectors: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    sector_by_id = {
        str(sector["id"]): sector
        for sector in sectors or []
        if sector.get("id")
    }
    path_cache: dict[str, str] = {}

    def build_path(sector_id: str, trail: frozenset[str] = frozenset()) -> str:
        if sector_id in path_cache:
            return path_cache[sector_id]
        sector = sector_by_id.get(sector_id, {})
        name = str(sector.get("name") or sector_id)
        parent_id = str(sector.get("parent_id") or "")
        if sector_id in trail:
            return name
        if parent_id in sector_by_id:
            parent_path = build_path(parent_id, trail | {sector_id})
            path = f"{parent_path}/{name}" if parent_path else name
        else:
            path = name
        path_cache[sector_id] = path
        return path

    normalized: list[dict[str, Any]] = []
    image_sector_map: dict[str, list[dict[str, Any]]] = {}
    for sector_id, sector in sector_by_id.items():
        item = {
            "id": sector_id,
            "parent_id": sector.get("parent_id") or None,
            "name": sector.get("name"),
            "path": build_path(sector_id),
            "order": sector.get("order", 0),
            "image_count": len(sector.get("images") or []),
        }
        normalized.append(item)
        for image_id in sector.get("images") or []:
            if image_id:
                image_sector_map.setdefault(str(image_id), []).append(dict(item))
    return normalized, image_sector_map


def normalize_design_response(
    image_payload: dict[str, Any],
    sector_payload: dict[str, Any] | None = None,
    sector_warning: str | None = None,
) -> dict[str, Any]:
    if str(image_payload.get("code")) != SUCCESS_CODE:
        return {
            "status": "error",
            "message": image_payload.get("msg") or "蓝湖接口返回未知错误",
        }

    sectors: list[dict[str, Any]] = []
    image_sector_map: dict[str, list[dict[str, Any]]] = {}
    if sector_payload and str(sector_payload.get("code")) == SUCCESS_CODE:
        sectors, image_sector_map = normalize_design_sectors(
            (sector_payload.get("data") or {}).get("sectors") or []
        )
    elif sector_payload:
        sector_warning = str(sector_payload.get("msg") or "分组接口返回未知错误")

    project_data = image_payload.get("data") or {}
    designs = []
    for index, image in enumerate(project_data.get("images") or [], 1):
        design_sectors = image_sector_map.get(str(image.get("id")), [])
        designs.append({
            "index": index,
            "id": image.get("id"),
            "name": image.get("name"),
            "width": image.get("width"),
            "height": image.get("height"),
            "url": image.get("url"),
            "has_comment": image.get("has_comment", False),
            "update_time": image.get("update_time"),
            "sectors": [item["name"] for item in design_sectors if item.get("name")],
        })

    result: dict[str, Any] = {
        "status": "success",
        "platform": "lanhu",
        "project_name": project_data.get("name"),
        "total_sectors": len(sectors),
        "ungrouped_design_count": sum(1 for design in designs if not design["sectors"]),
        "sectors": sectors,
        "total_designs": len(designs),
        "designs": designs,
    }
    if sector_warning:
        result["sector_warning"] = sector_warning
    return result


def auth_result_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    if str(payload.get("code")) == SUCCESS_CODE:
        return {"status": "success", "platform": "lanhu", "message": "蓝湖认证有效"}
    message = str(payload.get("msg") or "蓝湖接口返回未知错误")
    lowered = message.lower()
    if any(word in lowered for word in ("登录", "login", "cookie", "token", "认证")):
        status = "auth_expired"
    elif any(word in lowered for word in ("权限", "permission", "forbidden", "无权")):
        status = "forbidden"
    else:
        status = "api_error"
    return {"status": status, "platform": "lanhu", "message": message}


def create_client(cookie: str, *, image_accept: bool = False) -> httpx.AsyncClient:
    headers = dict(HEADERS)
    headers["Cookie"] = cookie
    if image_accept:
        headers["Accept"] = "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
    return httpx.AsyncClient(timeout=60, headers=headers, follow_redirects=False)


async def get_lanhu_image(client: httpx.AsyncClient, url: str) -> httpx.Response:
    current = url
    for _ in range(6):
        if not is_lanhu_image_url(current):
            raise ValueError("图片请求或重定向目标不是蓝湖 HTTPS 域名")
        response = await client.get(current, follow_redirects=False)
        if not response.is_redirect:
            response.raise_for_status()
            return response
        location = response.headers.get("location")
        if not location:
            raise ValueError("蓝湖图片重定向缺少 Location")
        current = urljoin(str(response.url), location)
    raise ValueError("蓝湖图片重定向次数过多")


async def fetch_design_payloads(
    client: httpx.AsyncClient,
    project_id: str,
    team_id: str | None,
) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
    image_params = {
        "project_id": project_id,
        "dds_status": "1",
        "position": "1",
        "show_cb_src": "1",
        "comment": "1",
    }
    if team_id:
        image_params["team_id"] = team_id

    sector_payload = None
    sector_warning = None
    try:
        sector_response = await client.get(
            f"{LANHU_BASE}/api/project/project_sectors",
            params={"project_id": project_id},
        )
        sector_response.raise_for_status()
        sector_payload = sector_response.json()
    except Exception as error:
        sector_warning = f"设计分组读取失败: {error}"

    image_response = await client.get(
        f"{LANHU_BASE}/api/project/images",
        params=image_params,
    )
    image_response.raise_for_status()
    return image_response.json(), sector_payload, sector_warning


def select_design_id(
    params: dict[str, str | None],
    image_id: str,
) -> str:
    requested = image_id.strip()
    linked = str(params.get("image_id") or "")
    if requested and linked and requested != linked:
        raise ValueError("image_id 与蓝湖链接中的 image_id 不一致")
    selected = requested or linked
    if not selected:
        raise ValueError("缺少 image_id；请先确认设计范围")
    return selected


async def fetch_design_structure(
    client: httpx.AsyncClient,
    params: dict[str, str | None],
    image_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    query: dict[str, Any] = {
        "image_id": image_id,
        "project_id": params["project_id"],
        "dds_status": 1,
        "all_versions": 0,
    }
    if params.get("team_id"):
        query["team_id"] = params["team_id"]
    response = await client.get(f"{LANHU_BASE}/api/project/image", params=query)
    response.raise_for_status()
    payload = response.json()
    auth = auth_result_from_payload(payload)
    if auth["status"] != "success":
        raise RuntimeError(f"{auth['status']}:{auth['message']}")
    detail = payload.get("result") or payload.get("data")
    if not isinstance(detail, dict):
        raise ValueError("蓝湖设计详情缺少 result/data")
    versions = detail.get("versions")
    version = versions[0] if isinstance(versions, list) and versions else {}
    json_url = version.get("json_url") if isinstance(version, dict) else None
    if not isinstance(json_url, str) or not json_url:
        raise ValueError("蓝湖设计详情缺少 versions[0].json_url")
    if not is_lanhu_image_url(json_url):
        raise ValueError("设计结构地址不是受信任的蓝湖 HTTPS 域名")
    sketch_response = await get_lanhu_image(client, json_url)
    sketch = sketch_response.json()
    if not isinstance(sketch, dict):
        raise ValueError("蓝湖 Sketch 数据不是 JSON 对象")
    return detail, sketch


def structure_error(error: Exception) -> dict[str, Any]:
    mapped = http_error_result(
        error,
        platform="lanhu",
        status_by_code={401: "auth_expired", 403: "forbidden", 418: "auth_expired"},
        default_status="api_error",
    )
    if mapped:
        return mapped
    if isinstance(error, json.JSONDecodeError):
        status = "api_error"
        message = f"蓝湖结构响应无法解析: {error}"
    elif isinstance(error, RuntimeError) and ":" in str(error):
        status, message = str(error).split(":", 1)
    else:
        status = "api_error"
        message = str(error)
    return {
        "status": status,
        "platform": "lanhu",
        "message": message,
    }


def disabled_or_config_error() -> dict[str, str] | None:
    cookie, account, password = lanhu_settings()
    if not cookie and not (account and password):
        return {
            "status": "missing_config",
            "platform": "lanhu",
            "message": "未设置蓝湖手机号/邮箱和密码，也未设置 LANHU_COOKIE",
        }
    return None
