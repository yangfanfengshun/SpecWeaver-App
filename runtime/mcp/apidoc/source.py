"""apidoc 能力接入 requirement 编排层的统一适配器。

候选压缩、名单/详情落盘都在这里；requirement/core.py 只负责编排。
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from apidoc.catalog import render_catalog
from capability import no_provider_result, provider_of
from common import DOWNLOAD_CONCURRENCY, atomic_write_text
from requirement.paths import canonical_json, relative_to_output, safe_file_component


LABEL = "API 文档"
LEGACY_SOURCE_NAMES = {"eolink"}
API_SELECTION_REQUIRED = "api_selection_required"
_APIDOC_LIST_NAMES = {"api-list.md", "catalog.md"}
_OK_APIDOC_ITEM_STATUSES = frozenset({"success", API_SELECTION_REQUIRED})
_API_LIST_PENDING_MESSAGE = "已写入 api/api-list.md，请确认要收集详情的接口"


def canonical_api_id(value: Any) -> int | str:
    """范围里的接口 ID：纯数字收成 int，其余保留非空字符串。"""
    if isinstance(value, bool) or value is None:
        raise ValueError("api_id 无效")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        raise ValueError("api_id 无效")
    if text.lstrip("-").isdigit():
        return int(text)
    return text


def _api_id_sort_key(value: Any) -> tuple:
    if value is None or value == "":
        return (1, 0, "")
    if isinstance(value, bool):
        return (1, 0, "")
    if isinstance(value, int):
        return (0, value, "")
    text = str(value)
    if text.lstrip("-").isdigit():
        return (0, int(text), "")
    return (2, 0, text)


def compact_candidates(payload: dict[str, Any], source_url: str) -> dict:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if data.get("kind") == "detail":
        items = [{
            "api_id": data.get("api_id"),
            "name": data.get("name") or "",
            "method": data.get("method") or "",
            "path": data.get("path") or "",
        }]
        location = data.get("location") or {}
        resolved_url = source_url
    elif data.get("kind") == "list":
        items = list(data.get("items") or [])
        location = data.get("location") or {}
        resolved_url = data.get("source_url") or source_url
    else:
        items = []
        location = {}
        resolved_url = source_url
    return {
        "source_url": resolved_url,
        "status": "success",
        "location": location,
        "items": items,
    }


async def discover_candidates(urls: list[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source_url in urls:
        provider = provider_of("apidoc", source_url)
        if provider is None:
            result.append({
                "source_url": source_url,
                **no_provider_result("apidoc", source_url),
                "items": [],
            })
            continue
        try:
            payload = await provider.read(source_url)
            if payload.get("status") != "success":
                raise ValueError(payload.get("message") or "API 文档来源读取失败")
            result.append(compact_candidates(payload, source_url))
        except Exception as error:
            classified = provider.auth_error(error)
            result.append({
                "source_url": source_url,
                "status": classified["status"],
                "message": classified["message"],
                "items": [],
            })
    return result


def suggest_scope(
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    scope: list[dict[str, Any]] = []
    unambiguous = True
    for source in candidates:
        source_url = str(source.get("source_url") or "").strip()
        if source.get("status") != "success" or not source_url:
            unambiguous = False
            continue
        scope.append({"url": source_url})
    return scope, unambiguous


def normalize_scope(value: Any) -> list[dict[str, Any]]:
    scope = value or []
    if not isinstance(scope, list) or not all(
        isinstance(item, dict) for item in scope
    ):
        raise ValueError("confirmed_scope.apidoc 必须是对象数组")
    normalized = []
    seen = set()
    for item in scope:
        source_url = str(item.get("url") or "").strip()
        api_ids = item.get("api_ids")
        if api_ids is not None:
            if not isinstance(api_ids, list) or not api_ids:
                raise ValueError("API 文档范围的 api_ids 必须是非空数组")
            try:
                api_ids = sorted(
                    {canonical_api_id(api_id) for api_id in api_ids},
                    key=lambda value: (
                        (0, value) if isinstance(value, int) else (1, str(value))
                    ),
                )
            except ValueError as error:
                raise ValueError("API 文档范围的 api_ids 必须是非空 ID 数组") from error
        key = (source_url, tuple(api_ids or []))
        if key not in seen:
            seen.add(key)
            normalized.append({
                "url": source_url,
                **({"api_ids": api_ids} if api_ids is not None else {}),
            })
    normalized.sort(
        key=lambda item: (item["url"], tuple(item.get("api_ids") or []))
    )
    return normalized


def _apidoc_collect_status(
    results: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    selection_required: list[dict[str, Any]],
) -> str:
    if failures:
        if not any(item.get("status") == "success" for item in results):
            failure_statuses = {item["status"] for item in failures}
            if len(failure_statuses) == 1:
                return failure_statuses.pop()
        return "partial"
    if selection_required:
        return API_SELECTION_REQUIRED
    return "success"


async def collect(
    scope: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, Any]:
    target_dir = output_dir / "api"
    results: list[dict[str, Any]] = []
    saved_names: set[str] = set()
    for item in scope:
        source_url = str(item.get("url") or "").strip()
        requested_api_ids = item.get("api_ids")
        if not source_url:
            results.append({
                "status": "invalid_input",
                "message": "API 文档范围缺少 url",
            })
            continue
        provider = provider_of("apidoc", source_url)
        if provider is None:
            results.append({
                "source_url": source_url,
                "api_ids": requested_api_ids or [],
                **no_provider_result("apidoc", source_url),
            })
            continue
        try:
            api_ids = requested_api_ids
            prefetched: dict[str, dict[str, Any]] = {}
            if api_ids is None:
                listed = await provider.read(source_url)
                if listed.get("status") != "success":
                    raise ValueError(
                        listed.get("message") or "API 文档来源读取失败"
                    )
                data = listed.get("data") or {}
                if (
                    data.get("kind") == "detail"
                    and data.get("api_id") not in (None, "")
                ):
                    api_ids = [data["api_id"]]
                    prefetched[str(data["api_id"])] = listed
                else:
                    list_file = atomic_write_text(
                        target_dir / "api-list.md",
                        render_catalog(data, source_url),
                    )
                    saved_names.add(list_file.name)
                    results.append({
                        "status": API_SELECTION_REQUIRED,
                        "source_url": source_url,
                        "api_ids": [],
                        "path": relative_to_output(list_file, output_dir),
                        "message": _API_LIST_PENDING_MESSAGE,
                    })
                    continue
            if not isinstance(api_ids, list) or not api_ids:
                raise ValueError("API 文档范围的 api_ids 必须是非空数组")

            semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

            async def fetch(api_id: Any) -> tuple[Any, dict[str, Any]]:
                cached = prefetched.get(str(api_id))
                if cached is not None:
                    return api_id, cached
                try:
                    async with semaphore:
                        return api_id, await provider.read(
                            source_url,
                            api_id=api_id,
                        )
                except Exception as error:
                    return api_id, provider.auth_error(error)

            gathered = await asyncio.gather(
                *(fetch(api_id) for api_id in api_ids),
                return_exceptions=True,
            )
            records = []
            for outcome in gathered:
                if isinstance(outcome, Exception):
                    classified = provider.auth_error(outcome)
                    results.append({
                        "status": classified["status"],
                        "source_url": source_url,
                        "api_ids": [],
                        "message": classified["message"],
                    })
                    continue
                if isinstance(outcome, BaseException):
                    raise outcome
                api_id, payload = outcome
                if payload.get("status") != "success":
                    results.append({
                        "status": payload.get("status") or "api_error",
                        "source_url": source_url,
                        "api_ids": [api_id],
                        "message": payload.get("message")
                        or "API 接口详情读取失败",
                    })
                    continue
                data = payload.get("data") or {}
                if data.get("kind") != "detail":
                    results.append({
                        "status": "api_error",
                        "source_url": source_url,
                        "api_ids": [api_id],
                        "message": "API 接口详情读取失败",
                    })
                    continue
                actual_id = data.get("api_id")
                if actual_id in (None, ""):
                    raise ValueError("接口详情缺少 API ID")
                if str(actual_id) != str(api_id):
                    raise ValueError(
                        f"来源返回 API ID {actual_id}，与确认的 {api_id} 不一致"
                    )
                api_name = str(data.get("name") or f"API-{api_id}")
                records.append((api_id, api_name, data))

            for api_id, api_name, data in sorted(
                records,
                key=lambda record: _api_id_sort_key(record[0]),
            ):
                file_name = (
                    f"{api_id}-"
                    f"{safe_file_component(api_name, f'API-{api_id}')}.json"
                )
                file_payload = {
                    "schema_version": 1,
                    "specweaver_schema": f"{provider.PLATFORM}-api",
                    "platform": provider.PLATFORM,
                    "source_url": source_url,
                    "location": data.get("location") or {},
                    "api_id": api_id,
                    "api_name": api_name,
                    "api_detail": data,
                }
                output_file = atomic_write_text(
                    target_dir / file_name,
                    canonical_json(file_payload),
                )
                saved_names.add(output_file.name)
                results.append({
                    "status": "success",
                    "source_url": source_url,
                    "api_id": api_id,
                    "name": api_name,
                    "path": relative_to_output(output_file, output_dir),
                })
        except Exception as error:
            classified = provider.auth_error(error)
            results.append({
                "status": classified["status"],
                "source_url": source_url,
                "api_ids": requested_api_ids or [],
                "message": classified["message"],
            })
    failures = [
        item for item in results
        if item["status"] not in _OK_APIDOC_ITEM_STATUSES
    ]
    selection_required = [
        item for item in results
        if item["status"] == API_SELECTION_REQUIRED
    ]
    successful_ids = {
        item["api_id"]
        for item in results
        if item.get("status") == "success" and item.get("api_id") is not None
    }
    if target_dir.is_dir():
        for old_file in target_dir.iterdir():
            if not old_file.is_file() or old_file.name in saved_names:
                continue
            owned = old_file.name.lower() in _APIDOC_LIST_NAMES or bool(
                re.fullmatch(r"eolink-\d{3}\.json", old_file.name, re.I)
            )
            old_api_id = None
            if old_file.suffix.lower() == ".json" and not owned:
                try:
                    old_payload = json.loads(old_file.read_text(encoding="utf-8"))
                    owned = str(
                        old_payload.get("specweaver_schema") or ""
                    ).endswith("-api")
                    old_api_id = old_payload.get("api_id")
                except (OSError, ValueError, TypeError):
                    owned = False
            if owned and (not failures or old_api_id in successful_ids):
                old_file.unlink()
    results.sort(key=lambda item: (
        item.get("status") != "success",
        _api_id_sort_key(item.get("api_id")),
        item.get("source_url", ""),
    ))
    status = "not_applicable"
    if scope:
        status = _apidoc_collect_status(results, failures, selection_required)
    return {
        "status": status,
        "source_count": len(scope),
        "api_count": sum(item.get("status") == "success" for item in results),
        "items": results,
    }


def managed_paths(output_dir: Path) -> list[Path]:
    return [output_dir / "api"]


def cache_paths(_scope: list[dict[str, Any]]) -> list[Path]:
    return []


def clean(_output_dir: Path) -> None:
    return None


def verify(output_dir: Path, result: dict[str, Any]) -> list[str]:
    missing = []
    for item in result.get("items", []):
        relative = item.get("path")
        if not relative:
            continue
        path = (output_dir / str(relative)).resolve()
        try:
            path.relative_to(output_dir.resolve())
        except ValueError:
            missing.append(f"路径越界：{relative}")
            continue
        if not path.is_file():
            missing.append(str(relative))
    return missing


def unresolved(result: dict[str, Any]) -> list[str]:
    messages = []
    for item in result.get("items", []):
        status = item.get("status")
        if status == "success":
            continue
        if status == API_SELECTION_REQUIRED:
            messages.append(
                item.get("message")
                or _API_LIST_PENDING_MESSAGE
            )
            continue
        messages.append(
            item.get("message")
            or f"API 文档收集失败：{item.get('source_url', '未提供')}"
        )
    return messages


def count(result: dict[str, Any]) -> dict[str, int]:
    return {"apidoc_count": int(result.get("api_count", 0))}
