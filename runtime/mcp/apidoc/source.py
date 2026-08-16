"""apidoc 能力接入 requirement 编排层的统一适配器。"""
from __future__ import annotations

from pathlib import Path
from typing import Any


LABEL = "API 文档"
LEGACY_SOURCE_NAMES = {"eolink"}


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


async def discover_candidates(urls: list[str]) -> list[dict[str, Any]]:
    from requirement import core

    result: list[dict[str, Any]] = []
    for source_url in urls:
        provider = core.provider_of("apidoc", source_url)
        if provider is None:
            result.append({
                "source_url": source_url,
                **core.no_provider_result("apidoc", source_url),
                "items": [],
            })
            continue
        try:
            payload = await provider.read(source_url)
            if payload.get("status") != "success":
                raise ValueError(payload.get("message") or "API 文档来源读取失败")
            result.append(core.compact_api_candidates(payload, source_url))
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


async def collect(
    scope: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, Any]:
    from requirement import core

    return await core.collect_apidoc(scope, output_dir)


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
        if status == "api_selection_required":
            messages.append(
                item.get("message")
                or "已写入 api/api-list.md，请确认要收集详情的接口"
            )
            continue
        messages.append(
            item.get("message")
            or f"API 文档收集失败：{item.get('source_url', '未提供')}"
        )
    return messages


def count(result: dict[str, Any]) -> dict[str, int]:
    return {"apidoc_count": int(result.get("api_count", 0))}
