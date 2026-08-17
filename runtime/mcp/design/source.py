"""design 能力接入 requirement 编排层的统一适配器。

候选压缩、预览/切图落盘都在这里；requirement/core.py 只负责编排。
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from capability import no_provider_result, provider_of
from common import atomic_write_text, ensure_no_symlink_components
from design.schema import attach_assets
from requirement.paths import (
    canonical_json,
    design_cache_file,
    design_error,
    relative_to_output,
    remove_empty_directories,
    safe_file_component,
)


LABEL = "设计稿"
LEGACY_SOURCE_NAMES = {"lanhu"}


def compact_candidates(result: dict[str, Any], source_url: str) -> dict:
    if result.get("status") != "success":
        return {
            "source_url": source_url,
            "status": result.get("status", "api_error"),
            "message": result.get("message", "设计稿候选读取失败"),
            "items": [],
        }
    items = []
    for item in result.get("designs") or []:
        items.append({
            "id": str(item.get("id") or item.get("image_id") or ""),
            "name": str(item.get("name") or ""),
            "url": source_url,
            "preview_url": str(
                item.get("preview_url")
                or item.get("url")
                or ""
            ),
            "sectors": item.get("sectors") or [],
        })
    return {
        "source_url": source_url,
        "status": "success",
        "project_name": result.get("project_name") or "",
        "items": items,
    }


async def discover_candidates(urls: list[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source_url in urls:
        provider = provider_of("design", source_url)
        if provider is None:
            result.append({
                "source_url": source_url,
                **no_provider_result("design", source_url),
                "items": [],
            })
            continue
        try:
            result.append(compact_candidates(
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


def _design_screen_folder(name: str, selected_id: str, used: set[str]) -> str:
    base = safe_file_component(name, "未命名设计")
    folder = base
    if folder in used:
        folder = f"{base}--{safe_file_component(selected_id, 'unknown')}"
    used.add(folder)
    return folder


def _cleanup_legacy_design_images(image_dir: Path) -> None:
    if not image_dir.is_dir():
        return
    owned_preview = re.compile(
        r"(?:lanhu-\d{3}|.+--[^/]+)-preview\.(gif|jpe?g|png|svg|webp)",
        re.I,
    )
    for old_file in image_dir.iterdir():
        if old_file.is_file() and owned_preview.fullmatch(old_file.name):
            old_file.unlink()
    owned_slice = re.compile(
        r"[a-z0-9_-]+-slice-\d{3}\.(gif|jpe?g|png|svg|webp)",
        re.I,
    )
    for slice_root in image_dir.glob("*-slices"):
        if not slice_root.is_dir():
            continue
        for old_file in slice_root.rglob("*"):
            if old_file.is_file() and owned_slice.fullmatch(old_file.name):
                old_file.unlink()
        remove_empty_directories(slice_root)


def _cleanup_stale_design_screens(
    output_dir: Path,
    results: list[dict[str, Any]],
) -> None:
    design_root = output_dir / "design"
    if not design_root.is_dir():
        return
    active = set()
    for item in results:
        preview = item.get("preview_file")
        if preview:
            active.add((output_dir / preview).resolve().parent)
        slices_dir = item.get("slices_dir")
        if slices_dir:
            active.add((output_dir / slices_dir).resolve().parent)
    for child in design_root.iterdir():
        if not child.is_dir() or child.resolve() in active:
            continue
        if any(child.glob("preview.*")):
            shutil.rmtree(child)


async def collect(
    scope: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, Any]:
    image_dir = output_dir / "images"
    results: list[dict[str, Any]] = []
    preview_failures: list[dict[str, Any]] = []
    preview_count = 0
    used_folders: set[str] = set()
    for item in scope:
        source_url = str(item.get("url") or "").strip()
        image_id = str(item.get("image_id") or item.get("id") or "").strip()
        if not source_url:
            results.append({
                "status": "invalid_input",
                "message": "设计稿范围缺少 url",
            })
            continue
        provider = provider_of("design", source_url)
        if provider is None:
            results.append({
                "source_url": source_url,
                "image_id": image_id,
                **no_provider_result("design", source_url),
            })
            continue
        preliminary_file = design_cache_file(
            provider.PLATFORM,
            image_id,
            str(item.get("name") or "未命名设计"),
        )
        try:
            ensure_no_symlink_components(preliminary_file)
            detail = await provider.get_detail(
                source_url,
                image_id=image_id,
                output_file=str(preliminary_file),
            )
        except Exception as error:
            status, message = design_error(error)
            results.append({
                "source_url": source_url,
                "image_id": image_id,
                "status": status,
                "message": message,
            })
            continue
        entry = {
            "source_url": source_url,
            "image_id": image_id,
            "status": detail.get("status", "api_error"),
            "message": detail.get("message", ""),
        }
        if detail.get("status") != "success":
            results.append(entry)
            continue
        source = detail.get("source") or {}
        selected_id = str(source.get("design_id") or image_id)
        design_name = str(
            source.get("name") or item.get("name") or "未命名设计"
        )
        cache_file = design_cache_file(
            provider.PLATFORM,
            selected_id,
            design_name,
        )
        if not preliminary_file.is_file():
            entry.update({
                "status": "verification_failed",
                "message": "设计 provider 声明写入成功但规范化结构文件不存在",
            })
            results.append(entry)
            continue
        try:
            document = json.loads(preliminary_file.read_text(encoding="utf-8"))
            atomic_write_text(cache_file, canonical_json(document))
            if preliminary_file.resolve() != cache_file.resolve():
                preliminary_file.unlink()
        except (OSError, ValueError, TypeError) as error:
            entry.update({
                "status": "verification_failed",
                "message": f"设计规范化结构无法验证: {error}",
            })
            results.append(entry)
            continue
        if cache_file.parent.is_dir():
            for old_file in cache_file.parent.glob("*.json"):
                if old_file.resolve() != cache_file.resolve():
                    old_file.unlink()
        canvas = document.get("canvas") if isinstance(document.get("canvas"), dict) else None
        entry.update({
            "id": selected_id,
            "url": source_url,
            "platform": provider.PLATFORM,
            "image_id": selected_id,
            "source_url": source_url,
            "project_id": str(source.get("project_id") or ""),
            "version_id": str(source.get("version_id") or ""),
            "name": design_name,
            "design_cache_file": str(cache_file.resolve()),
        })
        if isinstance(canvas, dict) and "w" in canvas and "h" in canvas:
            entry["canvas"] = {"w": canvas["w"], "h": canvas["h"]}
        screen_dir = output_dir / "design" / _design_screen_folder(
            design_name,
            selected_id,
            used_folders,
        )
        preview_url = str(detail.get("preview_url") or "")
        if preview_url:
            try:
                preview = await provider.download_previews(
                    [{
                        "id": selected_id,
                        "name": design_name,
                        "url": preview_url,
                        "file_stem": "preview",
                    }],
                    str(screen_dir.resolve()),
                )
            except Exception as error:
                status, message = design_error(error)
                preview = {
                    "status": status,
                    "saved_count": 0,
                    "images": [],
                    "failures": [{
                        "status": status,
                        "message": message,
                    }],
                }
            preview_count += preview.get("saved_count", 0)
            preview_failures.extend(preview.get("failures") or [])
            if (
                preview.get("status") not in {"success", "not_applicable"}
                and not preview.get("failures")
            ):
                preview_failures.append({
                    "status": preview.get("status", "api_error"),
                    "message": preview.get("message") or "设计预览图收集失败",
                })
            for image in preview.get("images") or []:
                path = image.get("path")
                if path:
                    entry["preview_file"] = relative_to_output(path, output_dir)
                    break
        slices_dir = screen_dir / "slices"
        asset_map: dict[str, str] = {}
        try:
            slices = await provider.download_slices(
                source_url,
                selected_id,
                str(slices_dir.resolve()),
                manifest_file="",
            )
            entry["slices_status"] = slices.get("status", "api_error")
            entry["slice_count"] = slices.get("saved_count", 0)
            slice_files = []
            for asset in slices.get("assets") or []:
                local_path = asset.get("local_path")
                if not local_path:
                    continue
                candidate = Path(str(local_path))
                if not candidate.is_absolute():
                    candidate = slices_dir / candidate
                try:
                    slice_files.append(
                        relative_to_output(candidate, output_dir)
                    )
                    layer_id = str(asset.get("layer_id") or "")
                    if layer_id:
                        asset_map[layer_id] = str(
                            candidate.resolve().relative_to(screen_dir.resolve())
                        )
                except ValueError:
                    entry["slices_status"] = "invalid_output"
                    entry["slices_message"] = "切图路径越出目标目录"
            entry["slice_files"] = sorted(set(slice_files))
            if entry["slice_count"] and not entry["slice_files"]:
                entry["slices_status"] = "verification_failed"
                entry["slices_message"] = "切图声明成功但没有返回可验证文件"
        except Exception as error:
            status, message = design_error(error)
            entry["slices_status"] = status
            entry["slices_message"] = message
            entry["slice_count"] = 0
            entry["slice_files"] = []
        if entry.get("slice_files"):
            entry["slices_dir"] = relative_to_output(slices_dir, output_dir)
            if asset_map and cache_file.is_file():
                try:
                    cached = json.loads(cache_file.read_text(encoding="utf-8"))
                    attach_assets(cached, asset_map)
                    atomic_write_text(cache_file, canonical_json(cached))
                except (OSError, ValueError, TypeError) as error:
                    entry["slices_status"] = "verification_failed"
                    entry["slices_message"] = f"切图已保存但无法写回结构文件: {error}"
        elif slices_dir.is_dir():
            remove_empty_directories(slices_dir)
        results.append(entry)

    failures = [
        item for item in results
        if item.get("status") != "success"
        or item.get("slices_status") not in {None, "success"}
    ]
    status = "success" if not failures else "partial"
    if preview_failures:
        status = "partial"
    detail_failures = [
        item for item in results
        if item.get("status") != "success"
    ]
    if scope and len(detail_failures) == len(scope):
        statuses = {item.get("status") for item in detail_failures}
        if len(statuses) == 1:
            status = statuses.pop() or "api_error"
    _cleanup_legacy_design_images(image_dir)
    _cleanup_stale_design_screens(output_dir, results)
    return {
        "status": status if scope else "not_applicable",
        "source_count": len(scope),
        "design_count": sum(
            bool(item.get("design_cache_file")) for item in results
        ),
        "preview_count": preview_count,
        "items": results,
        "preview_failures": preview_failures,
    }


def managed_paths(output_dir: Path) -> list[Path]:
    return [output_dir / "design", output_dir / "images"]


def cache_paths(scope: list[dict[str, Any]]) -> list[Path]:
    paths = []
    for item in scope:
        provider = provider_of("design", str(item.get("url") or ""))
        if provider is None:
            continue
        paths.append(design_cache_file(
            provider.PLATFORM,
            str(item.get("image_id") or item.get("id") or ""),
            str(item.get("name") or "未命名设计"),
        ))
    return paths


def clean(output_dir: Path) -> None:
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
