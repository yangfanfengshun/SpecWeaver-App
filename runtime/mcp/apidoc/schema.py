"""apidoc 中立结构的构造函数。

list / detail 两套 `data` 形状是 Eolink、Apifox 的共同出口；平台原文怎么折
成这两套，留给各 mapper。这里只负责字段名、空值省略和 id 字符串化，避免
两边各写一份「有就写、没有就别出现」。
"""
from __future__ import annotations

from typing import Any


EMPTY_FOLDER_MESSAGE = "该目录没有直接子接口"

_LOCATION_KEYS = ("project_id", "folder_id", "folder_name")


def stringify_id(value: Any) -> str:
    if value is None or value == "":
        return ""
    return str(value)


def normalize_method(value: Any) -> str:
    if value is None or value == "":
        return ""
    return str(value).strip().upper()


def location_of(
    project_id: Any = None,
    folder_id: Any = None,
    folder_name: Any = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, str]:
    raw = {
        "project_id": project_id,
        "folder_id": folder_id,
        "folder_name": folder_name,
        **(extra or {}),
    }
    location: dict[str, str] = {}
    for key in _LOCATION_KEYS:
        text = stringify_id(raw.get(key))
        if text:
            location[key] = text
    return location


def merge_location(
    extracted: dict[str, Any],
    override: dict[str, Any] | None = None,
) -> dict[str, str]:
    merged = dict(extracted)
    for key, value in (override or {}).items():
        if value not in (None, ""):
            merged[key] = value
    return location_of(
        project_id=merged.get("project_id"),
        folder_id=merged.get("folder_id"),
        folder_name=merged.get("folder_name"),
    )


def list_item(
    *,
    api_id: Any,
    name: Any = "",
    method: Any = "",
    path: Any = "",
) -> dict[str, str]:
    return {
        "api_id": stringify_id(api_id),
        "name": "" if name is None else str(name),
        "method": normalize_method(method),
        "path": "" if path is None else str(path),
    }


def list_data(
    *,
    source_url: str = "",
    location: dict[str, Any] | None = None,
    items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "kind": "list",
        "source_url": source_url or "",
        "location": dict(location or {}),
        "items": list(items or []),
    }


def parameter(
    *,
    name: str,
    location: str,
    type: str,
    required: bool = False,
    description: Any = "",
    example: Any = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": name,
        "in": location,
        "type": type,
        "required": bool(required),
    }
    for key, value in (
        ("description", description),
        ("example", example),
    ):
        if value not in (None, ""):
            item[key] = value
    return item


def detail_data(
    *,
    api_id: Any,
    name: Any = "",
    method: Any = "",
    path: Any = "",
    location: dict[str, Any] | None = None,
    request: dict[str, Any] | None = None,
    responses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "kind": "detail",
        "api_id": stringify_id(api_id),
        "name": "" if name is None else str(name),
        "method": normalize_method(method),
        "path": "" if path is None else str(path),
        "location": dict(location or {}),
        "request": request
        or {"content_type": "", "parameters": []},
        "responses": list(responses or []),
    }


def default_message(data: dict[str, Any]) -> str:
    if data.get("kind") == "list":
        count = len(data.get("items") or [])
        if count == 0:
            return EMPTY_FOLDER_MESSAGE
        return f"找到 {count} 个接口"
    return "ok"


def envelope(
    status: str,
    platform: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "platform": platform,
        "message": message,
    }
    if data is not None:
        result["data"] = data
    return result


def success(platform: str, data: dict[str, Any], message: str = "") -> dict[str, Any]:
    return envelope(
        "success",
        platform,
        message or default_message(data),
        data,
    )
