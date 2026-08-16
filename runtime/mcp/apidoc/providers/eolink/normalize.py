"""把 Eolink list / getApi 原文折成 apidoc 中立结构。

只做纯函数转换：不打网络、不写缓存。mock、testHistory、空 key、session_id
在这里丢掉；`data>>result>>id` 在这里折成嵌套 JSON Schema。
"""
from __future__ import annotations

from typing import Any

from apidoc.schema import (
    detail_data,
    list_data,
    list_item,
    location_of,
    merge_location,
    parameter,
    stringify_id,
)


# Eolink 把 HTTP 方法收成数字；样本里 list 的 0 对应 POST。
_REQUEST_METHODS = {
    0: "POST",
    1: "GET",
    2: "PUT",
    3: "DELETE",
    4: "HEAD",
    5: "OPTIONS",
    6: "PATCH",
}

# paramType 同样是数字。样本里 0=字符串、12=数组、13=对象。
_PARAM_TYPES = {
    0: "string",
    1: "file",
    2: "json",
    3: "integer",
    4: "number",
    5: "number",
    6: "string",
    7: "string",
    8: "boolean",
    9: "integer",
    10: "integer",
    11: "integer",
    12: "array",
    13: "object",
}

_REQUEST_CONTENT_TYPES = {
    0: "application/x-www-form-urlencoded",
    1: "application/json",
    2: "application/xml",
    3: "text/plain",
    4: "application/octet-stream",
}

_SKIP_REQUEST_KEYS = frozenset({"session_id"})


def from_list(
    payload: Any,
    *,
    source_url: str = "",
    location: dict[str, Any] | None = None,
) -> dict[str, Any]:
    items_raw = _api_list(payload)
    first = items_raw[0] if items_raw else {}
    extracted = location_of(
        project_id=first.get("projectID"),
        folder_id=first.get("groupID"),
        folder_name=first.get("groupName"),
    )
    items = [
        list_item(
            api_id=item.get("apiID"),
            name=item.get("apiName"),
            method=_request_method(item.get("apiRequestType")),
            path=item.get("apiURI") or "",
        )
        for item in items_raw
        if item.get("apiID") not in (None, "")
    ]
    return list_data(
        source_url=source_url,
        location=merge_location(extracted, location),
        items=items,
    )


def from_detail(
    payload: Any,
    *,
    location: dict[str, Any] | None = None,
) -> dict[str, Any]:
    info = _api_info(payload)
    base = info.get("baseInfo") if isinstance(info.get("baseInfo"), dict) else {}
    extracted = location_of(
        project_id=base.get("projectID"),
        folder_id=base.get("groupID"),
    )
    request_params = [
        parameter(
            name=str(item.get("paramKey")),
            location="body",
            type=_param_type(item.get("paramType")),
            required=_is_required(item.get("paramNotNull")),
            description=item.get("paramName"),
            example=_example_value(item.get("paramValue"), _param_type(item.get("paramType"))),
        )
        for item in info.get("requestInfo") or []
        if isinstance(item, dict)
        and str(item.get("paramKey") or "").strip()
        and str(item.get("paramKey")).strip() not in _SKIP_REQUEST_KEYS
    ]
    status_code = _as_int(base.get("apiSuccessStatusCode"), 200)
    return detail_data(
        api_id=base.get("apiID"),
        name=base.get("apiName"),
        method=_request_method(base.get("apiRequestType")),
        path=base.get("apiURI") or "",
        location=merge_location(extracted, location),
        request={
            "content_type": _content_type(base.get("apiRequestParamType")),
            "parameters": request_params,
        },
        responses=[
            {
                "status": status_code if status_code is not None else 200,
                "description": "成功",
                "schema": _fold_result_info(info.get("resultInfo") or []),
            }
        ],
    )


def _api_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    raw = payload.get("apiList")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _api_info(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    info = payload.get("apiInfo")
    if isinstance(info, dict):
        return info
    if "baseInfo" in payload or "requestInfo" in payload:
        return payload
    return {}


def _as_int(value: Any, default: int | None = None) -> int | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return default


def _request_method(value: Any) -> str:
    if isinstance(value, str) and value.strip() and not value.strip().lstrip("-").isdigit():
        return value.strip().upper()
    code = _as_int(value)
    if code is None:
        return ""
    return _REQUEST_METHODS.get(code, "")


def _param_type(value: Any) -> str:
    code = _as_int(value)
    if code is None:
        return "string"
    return _PARAM_TYPES.get(code, "string")


def _content_type(value: Any) -> str:
    code = _as_int(value, 0)
    if code is None:
        return _REQUEST_CONTENT_TYPES[0]
    return _REQUEST_CONTENT_TYPES.get(code, _REQUEST_CONTENT_TYPES[0])


def _is_required(value: Any) -> bool:
    return stringify_id(value) == "1" or value is True or value == 1


def _example_value(value: Any, type_name: str) -> Any:
    if value in (None, ""):
        return None
    if type_name == "integer":
        parsed = _as_int(value)
        return parsed if parsed is not None else value
    if type_name in {"number", "boolean"}:
        return value
    return str(value)


def _fold_result_info(rows: Any) -> dict[str, Any]:
    root: dict[str, Any] = {"type": "object", "properties": {}}
    if not isinstance(rows, list):
        return root
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("paramKey") or "").strip()
        if not key:
            continue
        _assign(root, key.split(">>"), _field_schema(row))
    return root


def _field_schema(row: dict[str, Any]) -> dict[str, Any]:
    type_name = _param_type(row.get("paramType"))
    schema: dict[str, Any] = {"type": type_name}
    description = str(row.get("paramName") or "").strip()
    if description:
        schema["description"] = description
    example = _first_example(row.get("paramValueList"))
    coerced = _example_value(example, type_name)
    if coerced not in (None, ""):
        schema["example"] = coerced
    if type_name == "array":
        schema.setdefault("items", {"type": "object", "properties": {}})
    if type_name == "object":
        schema.setdefault("properties", {})
    return schema


def _first_example(values: Any) -> Any:
    if not isinstance(values, list):
        return None
    for item in values:
        if isinstance(item, dict) and item.get("value") not in (None, ""):
            return item.get("value")
        if item not in (None, "", []):
            return item
    return None


def _assign(node: dict[str, Any], parts: list[str], leaf: dict[str, Any]) -> None:
    name = parts[0]
    rest = parts[1:]
    props = node.setdefault("properties", {})
    existing = props.get(name)
    if not rest:
        props[name] = _merge_node(existing, leaf)
        return
    child = existing if isinstance(existing, dict) else None
    if child is None:
        child = {"type": "object", "properties": {}}
        props[name] = child
    if child.get("type") == "array":
        items = child.get("items")
        if not isinstance(items, dict) or items.get("type") != "object":
            items = {"type": "object", "properties": {}}
            child["items"] = items
        _assign(items, rest, leaf)
        return
    child.setdefault("type", "object")
    child.setdefault("properties", {})
    _assign(child, rest, leaf)


def _merge_node(existing: Any, incoming: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(existing, dict):
        return incoming
    merged = {**existing, **incoming}
    if incoming.get("type") == "array":
        items = incoming.get("items")
        if existing.get("type") == "object" and existing.get("properties"):
            merged["items"] = {
                "type": "object",
                "properties": existing.get("properties") or {},
            }
        elif isinstance(items, dict):
            merged["items"] = items
        merged.pop("properties", None)
    if incoming.get("type") == "object":
        merged.setdefault("properties", existing.get("properties") or {})
    return merged
