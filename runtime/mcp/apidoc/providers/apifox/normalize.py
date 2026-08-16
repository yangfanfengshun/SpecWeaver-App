"""把 Apifox getStructureInfo / getHttpEndpoint 原文折成 apidoc 中立结构。"""
from __future__ import annotations

import re
from typing import Any

from apidoc.schema import (
    detail_data,
    list_data,
    list_item,
    location_of,
    merge_location,
    parameter,
)


_EMPTY_FOLDER_RE = re.compile(
    r"Folder\s+(\d+)\s+has no endpoint entities in project\s+(\d+)",
    re.IGNORECASE,
)
_PARAM_INS = ("path", "query", "header")
_SKIP_X_PREFIX = "x-apifox-"


def from_structure(
    payload: Any,
    *,
    source_url: str = "",
    location: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(payload, str):
        extracted = _empty_folder_location(payload)
        return list_data(
            source_url=source_url,
            location=merge_location(extracted, location),
            items=[],
        )
    data = _structure_data(payload)
    scope = data.get("scope") if isinstance(data.get("scope"), dict) else {}
    extracted = location_of(
        project_id=scope.get("projectId"),
        folder_id=scope.get("folderId"),
    )
    items = [
        list_item(
            api_id=item.get("entityId", item.get("id")),
            name=item.get("name") or item.get("summary"),
            method=item.get("method"),
            path=item.get("path") or "",
        )
        for item in data.get("entities") or []
        if isinstance(item, dict)
        and item.get("entityId", item.get("id")) not in (None, "")
    ]
    return list_data(
        source_url=source_url,
        location=merge_location(extracted, location),
        items=items,
    )


def from_endpoint(
    payload: Any,
    *,
    location: dict[str, Any] | None = None,
) -> dict[str, Any]:
    endpoint = _endpoint_data(payload)
    extracted = location_of(
        project_id=endpoint.get("projectId"),
        folder_id=endpoint.get("folderId"),
    )
    request_body = (
        endpoint.get("requestBody")
        if isinstance(endpoint.get("requestBody"), dict)
        else {}
    )
    parameters = _body_parameters(request_body)
    raw_params = (
        endpoint.get("parameters")
        if isinstance(endpoint.get("parameters"), dict)
        else {}
    )
    for where in _PARAM_INS:
        for item in raw_params.get(where) or []:
            mapped = _http_parameter(item, where)
            if mapped is not None:
                parameters.append(mapped)
    responses = []
    for item in endpoint.get("responses") or []:
        if not isinstance(item, dict):
            continue
        status = item.get("code", item.get("status", 200))
        try:
            status_code = int(status)
        except (TypeError, ValueError):
            status_code = 200
        responses.append(
            {
                "status": status_code,
                "description": str(item.get("description") or "").strip() or "成功",
                "schema": _strip_apifox(item.get("jsonSchema") or {}),
            }
        )
    return detail_data(
        api_id=endpoint.get("id"),
        name=endpoint.get("name"),
        method=endpoint.get("method"),
        path=endpoint.get("path") or "",
        location=merge_location(extracted, location),
        request=_request_of(request_body, parameters),
        responses=responses,
    )


def folder_name_from_summary(payload: Any, folder_id: str) -> str:
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        data = payload if isinstance(payload, dict) else {}
    folders = data.get("endpointFolders") or []
    target = str(folder_id)
    for folder in folders:
        if not isinstance(folder, dict):
            continue
        if str(folder.get("id") or "") == target:
            return str(folder.get("name") or "").strip()
    return ""


def _structure_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict) and ("entities" in data or "scope" in data):
        return data
    return payload


def _endpoint_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict) and "id" in data:
        return data
    return payload


def _empty_folder_location(text: str) -> dict[str, str]:
    match = _EMPTY_FOLDER_RE.search(text)
    if not match:
        return {}
    return location_of(project_id=match.group(2), folder_id=match.group(1))


def _request_of(
    request_body: dict[str, Any],
    parameters: list[dict[str, Any]],
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "content_type": str(request_body.get("type") or ""),
        "parameters": parameters,
    }
    json_schema = request_body.get("jsonSchema")
    if isinstance(json_schema, dict) and json_schema:
        request["schema"] = _strip_apifox(json_schema)
    return request


def _body_parameters(request_body: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for item in request_body.get("parameters") or []:
        mapped = _http_parameter(item, "body")
        if mapped is not None:
            items.append(mapped)
    return items


def _http_parameter(item: Any, where: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    if item.get("enable") is False:
        return None
    name = str(item.get("name") or "").strip()
    if not name:
        return None
    schema = item.get("schema") if isinstance(item.get("schema"), dict) else {}
    type_name = item.get("type") or schema.get("type") or "string"
    if isinstance(type_name, list):
        type_name = next((part for part in type_name if part and part != "null"), "string")
    example = item.get("example")
    if example in (None, ""):
        example = schema.get("example")
    return parameter(
        name=name,
        location=where,
        type=str(type_name),
        required=bool(item.get("required")),
        description=item.get("description") or schema.get("description"),
        example=example,
    )


def _strip_apifox(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_apifox(inner)
            for key, inner in value.items()
            if not str(key).startswith(_SKIP_X_PREFIX)
        }
    if isinstance(value, list):
        return [_strip_apifox(item) for item in value]
    return value
