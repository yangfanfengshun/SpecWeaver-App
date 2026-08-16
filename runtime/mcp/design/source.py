"""design 能力接入 requirement 编排层的统一适配器。"""
from __future__ import annotations

from pathlib import Path
from typing import Any


LABEL = "设计稿"
LEGACY_SOURCE_NAMES = {"lanhu"}


async def discover_candidates(urls: list[str]) -> list[dict[str, Any]]:
    from requirement import core

    result: list[dict[str, Any]] = []
    for source_url in urls:
        provider = core.provider_of("design", source_url)
        if provider is None:
            result.append({
                "source_url": source_url,
                **core.no_provider_result("design", source_url),
                "items": [],
            })
            continue
        try:
            result.append(core.compact_design_candidates(
                await provider.get_candidates(source_url),
                source_url,
            ))
        except Exception as error:
            result.append({
                "source_url": source_url,
                "status": "api_error",
                "message": str(error),
                "items": [],
            })
    return result


def suggest_scope(
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    scope: list[dict[str, Any]] = []
    unambiguous = True
    for source in candidates:
        items = source.get("items") or []
        if source.get("status") != "success" or len(items) != 1:
            unambiguous = False
            continue
        item = items[0]
        if not item.get("id"):
            unambiguous = False
            continue
        scope.append({
            "url": item.get("url") or source.get("source_url"),
            "image_id": item.get("id") or "",
            "name": item.get("name") or "",
        })
    return scope, unambiguous


def normalize_scope(value: Any) -> list[dict[str, Any]]:
    scope = value or []
    if not isinstance(scope, list) or not all(
        isinstance(item, dict) for item in scope
    ):
        raise ValueError("confirmed_scope.design 必须是对象数组")
    normalized = []
    seen = set()
    for item in scope:
        row = {
            "url": str(item.get("url") or "").strip(),
            "image_id": str(
                item.get("image_id") or item.get("id") or ""
            ).strip(),
            "name": str(item.get("name") or "").strip(),
        }
        key = (row["url"], row["image_id"])
        if key not in seen:
            seen.add(key)
            normalized.append(row)
    normalized.sort(key=lambda item: (item["url"], item["image_id"]))
    return normalized


async def collect(
    scope: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, Any]:
    from requirement import core

    return await core.collect_design(scope, output_dir)


def managed_paths(output_dir: Path) -> list[Path]:
    return [output_dir / "design", output_dir / "images"]


def cache_paths(scope: list[dict[str, Any]]) -> list[Path]:
    from requirement import core
    from requirement.paths import design_cache_file

    paths = []
    for item in scope:
        provider = core.provider_of("design", str(item.get("url") or ""))
        if provider is None:
            continue
        paths.append(design_cache_file(
            provider.PLATFORM,
            str(item.get("image_id") or item.get("id") or ""),
            str(item.get("name") or "未命名设计"),
        ))
    return paths


def clean(output_dir: Path) -> None:
    from requirement.paths import remove_empty_directories

    design_dir = output_dir / "design"
    if not design_dir.is_dir() or design_dir.is_symlink():
        return
    for path in design_dir.iterdir():
        if path.is_file() and path.suffix.lower() == ".json":
            path.unlink()
    remove_empty_directories(design_dir)


def verify(output_dir: Path, result: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    relative_paths: list[str] = []
    for item in result.get("items", []):
        cache_file = item.get("design_cache_file")
        if cache_file and not Path(str(cache_file)).is_file():
            missing.append(f"设计缓存缺失：{cache_file}")
        if item.get("preview_file"):
            relative_paths.append(str(item["preview_file"]))
        relative_paths.extend(str(path) for path in item.get("slice_files") or [])
    for relative in relative_paths:
        path = (output_dir / relative).resolve()
        try:
            path.relative_to(output_dir.resolve())
        except ValueError:
            missing.append(f"路径越界：{relative}")
            continue
        if not path.is_file():
            missing.append(relative)
    return missing


def unresolved(result: dict[str, Any]) -> list[str]:
    messages = [
        item.get("slices_message")
        or item.get("message")
        or f"设计稿收集失败：{item.get('url') or item.get('source_url', '未提供')}"
        for item in result.get("items", [])
        if item.get("status") != "success"
        or item.get("slices_status") not in {None, "success"}
    ]
    messages.extend(
        item.get("message") or item.get("error") or "设计预览图收集失败"
        for item in result.get("preview_failures", [])
    )
    return messages


def count(result: dict[str, Any]) -> dict[str, int]:
    return {"design_count": int(result.get("design_count", 0))}
