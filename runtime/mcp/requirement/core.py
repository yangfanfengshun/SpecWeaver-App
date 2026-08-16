# 依赖统一声明在 runtime/pyproject.toml，版本由 uv.lock 钉死；
# 由 scripts/run-mcp.sh 通过 `uv run --project` 提供，不要在这里再写一份。
"""requirement 能力的编排层：任务缓存 → 来源候选 → 完整收集 → 收集清单。

不自带 FastMCP 实例——工具由 `requirement/server.py` 定义并调进来。
对 provider 一律经 `capability.py` 路由（任务读取走本能力的 provider，
设计稿/API 文档走对应能力的 provider），不点名任何平台实现。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from types import ModuleType
from typing import Any

from capability import (
    discover_providers,
    discover_source_adapters,
    resolve_provider,
)
from common import (
    DOWNLOAD_CONCURRENCY,
    UnsafePathError,
    atomic_write_bytes,
    atomic_write_text,
    ensure_no_symlink_components,
    prepare_output_dir,
    unsafe_symlink_components,
)
from design.schema import attach_assets
from requirement.paths import (
    canonical_json,
    clean_legacy_project_artifacts,
    design_cache_file,
    design_error,
    error_status,
    normalize_scope as normalize_scope_with_adapters,
    parse_size_bytes,
    relative_to_output,
    remove_empty_directories,
    requirement_manifest_file,
    safe_directory_name,
    safe_extension,
    safe_file_component,
    specweaver_home,
    unsafe_managed_paths,
    verify_artifacts,
)


RESTRICTED_ATTACHMENT_KINDS = {"archive", "video"}
LARGE_ATTACHMENT_BYTES = 20 * 1024 * 1024


def provider_of(capability: str, url: str = "") -> ModuleType | None:
    """按链接取某个能力当前认领它的 provider；没人认领且无法兜底时为 None。"""
    return_value = resolve_provider(discover_providers(capability), url)
    return return_value[1] if return_value else None


def no_provider_result(capability: str, url: str = "") -> dict[str, str]:
    return {
        "status": "no_provider",
        "message": (
            f"{capability} 能力当前没有 provider 认领该链接：{url}"
            if url
            else f"{capability} 能力当前没有可用的 provider"
        ),
    }


def compact_design_candidates(result: dict[str, Any], source_url: str) -> dict:
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


def compact_api_candidates(payload: dict[str, Any], source_url: str) -> dict:
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


async def discover_candidates(data: dict[str, Any]) -> dict[str, Any]:
    """把缓存里按能力分好类的外部链接逐条问对应 provider 拿候选。

    链接在 provider 消失后仍留在旧缓存里的情况（拿掉 figma 又用旧任务缓存）
    落到 `no_provider`，不炸也不静默丢——这是隔离契约的一部分。
    """
    sources = data.get("external_sources") or {}
    return {
        name: await adapter.discover_candidates(sources.get(name) or [])
        for name, adapter in discover_source_adapters().items()
    }


def suggested_scope_from_candidates(
    candidates: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    scope: dict[str, Any] = {
        "tower_attachments": True,
        "allow_restricted_attachments": False,
        "replace_existing": False,
        "skipped_sources": [],
    }
    unambiguous = True
    for name, adapter in discover_source_adapters().items():
        source_scope, source_ready = adapter.suggest_scope(
            candidates.get(name) or []
        )
        scope[name] = source_scope
        unambiguous = unambiguous and source_ready
    return scope, unambiguous


def normalize_scope(confirmed_scope: dict[str, Any]) -> dict[str, Any]:
    """按动态发现的来源能力归一化 scope，保留原有对外入口。"""
    return normalize_scope_with_adapters(
        confirmed_scope,
        discover_source_adapters(),
    )


async def download_task_attachments(
    provider: ModuleType,
    data: dict[str, Any],
    output_dir: Path,
    *,
    allow_restricted: bool,
    skipped_urls: dict[str, str] | None = None,
) -> dict[str, Any]:
    target_dir = output_dir / "requirement-attachments"
    target_dir.mkdir(parents=True, exist_ok=True)
    unique = data.get("attachments") or []
    downloaded: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    hashes: dict[str, dict[str, Any]] = {}
    saved_names: set[str] = set()
    skipped_urls = skipped_urls or {}

    for source_index, attachment in enumerate(unique, 1):
        source_url = str(attachment["source_url"])
        if source_url in skipped_urls:
            skipped.append({
                "source_index": source_index,
                "source_url": source_url,
                "name": attachment.get("name") or "未提供",
                "kind": attachment.get("kind"),
                "status": "skipped",
                "message": skipped_urls[source_url],
            })
            continue
        known_size = parse_size_bytes(str(attachment.get("size") or ""))
        if (
            (
                attachment.get("kind") in RESTRICTED_ATTACHMENT_KINDS
                or (
                    known_size is not None
                    and known_size > LARGE_ATTACHMENT_BYTES
                )
            )
            and not allow_restricted
        ):
            skipped.append({
                "source_index": source_index,
                "source_url": source_url,
                "name": attachment.get("name") or "未提供",
                "kind": attachment.get("kind"),
                "status": "confirmation_required",
                "message": "视频、压缩包或已知超大附件需要用户确认后下载",
            })
            continue
        try:
            response = await provider.request(source_url)
            content_type = (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            content_hash = hashlib.sha256(response.content).hexdigest()
            if content_hash in hashes:
                original = hashes[content_hash]
                downloaded.append({
                    "source_index": source_index,
                    "source_url": source_url,
                    "name": attachment.get("name") or "未提供",
                    "status": "duplicate",
                    "path": original["path"],
                    "sha256": content_hash,
                })
                continue
            extension = safe_extension(source_url, content_type)
            file_name = f"requirement-attachment-{len(hashes) + 1:03d}{extension}"
            file_path = target_dir / file_name
            atomic_write_bytes(file_path, response.content)
            saved_names.add(file_name)
            item = {
                "source_index": source_index,
                "source_url": source_url,
                "name": attachment.get("name") or "未提供",
                "status": "success",
                "path": relative_to_output(file_path, output_dir),
                "content_type": content_type or "application/octet-stream",
                "bytes": len(response.content),
                "sha256": content_hash,
            }
            hashes[content_hash] = item
            downloaded.append(item)
        except Exception as error:
            status, message = error_status(error)
            failures.append({
                "source_index": source_index,
                "source_url": source_url,
                "name": attachment.get("name") or "未提供",
                "status": status,
                "message": message,
            })

    owned_pattern = re.compile(
        r"requirement-attachment-\d{3}\.[a-z0-9]{1,10}",
        re.I,
    )
    for old_file in target_dir.iterdir():
        if (
            old_file.is_file()
            and owned_pattern.fullmatch(old_file.name)
            and old_file.name not in saved_names
        ):
            old_file.unlink()

    by_url = {
        item["source_url"]: item
        for item in [*downloaded, *failures, *skipped]
    }
    occurrences = []
    for occurrence in data.get("attachment_occurrences") or []:
        source = by_url.get(occurrence["source_url"], {})
        occurrences.append({
            "occurrence_index": occurrence["occurrence_index"],
            "scope": occurrence["scope"],
            "scope_index": occurrence["scope_index"],
            "position": occurrence["position"],
            "source_url": occurrence["source_url"],
            "name": occurrence.get("name") or "未提供",
            "status": source.get("status", "not_downloaded"),
            "path": source.get("path"),
            "message": source.get("message"),
        })
    manifest = {
        "source_count": len(unique),
        "saved_count": len(hashes),
        "downloaded": downloaded,
        "skipped": skipped,
        "failures": failures,
        "occurrences": occurrences,
    }
    status = "success"
    if failures or any(
        item.get("status") != "skipped"
        for item in skipped
    ):
        status = "partial"
    return {
        "status": status,
        "saved_count": len(hashes),
        "failures": failures,
        "skipped": skipped,
        "occurrences": occurrences,
        "downloaded": downloaded,
        "source_count": len(unique),
        "artifacts": [
            item["path"]
            for item in downloaded
            if item["status"] == "success"
        ],
    }


def _design_screen_folder(name: str, selected_id: str, used: set[str]) -> str:
    base = safe_file_component(name, "未命名设计")
    folder = base
    if folder in used:
        folder = f"{base}--{safe_file_component(selected_id, 'unknown')}"
    used.add(folder)
    return folder


async def collect_design(
    scope: list[dict],
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


_APIDOC_LIST_NAMES = {"api-list.md", "catalog.md"}
API_SELECTION_REQUIRED = "api_selection_required"
_OK_SOURCE_STATUSES = frozenset({"success", "not_applicable", "skipped"})
_OK_APIDOC_ITEM_STATUSES = frozenset({"success", API_SELECTION_REQUIRED})
_API_LIST_PENDING_MESSAGE = "已写入 api/api-list.md，请确认要收集详情的接口"
_PARTIAL_NEXT_ACTION = (
    "让用户选择重试失败来源、明确跳过，或明确接受现有缺失后分析；"
    "重试同一目录时保持 confirmed_scope 并设置 replace_existing=true"
)
_API_SELECTION_NEXT_ACTION = (
    "已写入 api/api-list.md。暂停并让用户从名单指定要收集详情的 api_ids；"
    "确认后再次调用写入形式的 requirement_collect，在对应 apidoc 项写入 "
    "api_ids 并设置 replace_existing=true。"
    "用户明确不需要 API 详情时，从 apidoc 采用数组移除、追加 skipped_sources "
    "后转入 spec-requirement-analysis，分析阶段跳过 API 部份。"
)
_SUCCESS_NEXT_ACTION = (
    "除非用户此前明确要求只收集资料，否则直接使用 "
    "spec-requirement-analysis 生成 requirement.md，不再二次询问"
)


def collect_outcome_status(statuses: set[str], *, missing: bool) -> str:
    """来源状态聚合成 requirement_collect 的顶层 status。

    真失败（认证、网络、缺文件）优先变成 `partial`。只有名单待选、没有失败时
    才返回 `api_selection_required`，避免 Agent 把「请选接口」当成收集失败。
    """
    if missing:
        return "partial"
    problems = set(statuses) - _OK_SOURCE_STATUSES
    if not problems:
        return "success"
    if problems == {API_SELECTION_REQUIRED}:
        return API_SELECTION_REQUIRED
    return "partial"


def collect_next_action(status: str) -> str:
    if status == "partial":
        return _PARTIAL_NEXT_ACTION
    if status == API_SELECTION_REQUIRED:
        return _API_SELECTION_NEXT_ACTION
    return _SUCCESS_NEXT_ACTION


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


async def collect_apidoc(
    scope: list[dict],
    output_dir: Path,
) -> dict[str, Any]:
    target_dir = output_dir / "api"
    results: list[dict[str, Any]] = []
    saved_names: set[str] = set()
    from apidoc.catalog import render_catalog
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


def enabled_sources() -> set[str]:
    """完整收集要采哪几家。

    由 App 写进宿主配置里本条 MCP 的 `env`。进程自己判断不了：命令行里只有
    自己的 `server.py` 路径，既不知道被哪个宿主拉起，也读不到别的 MCP 开没开。

    变量缺失时按全部启用——宁可多采，也不要让一份旧配置在升级后突然少采东西。
    """
    raw = os.environ.get("SPECWEAVER_ENABLED_SOURCES", "").strip()
    if not raw:
        # 按当前实际存在的来源能力全开，而不是手抄一份能力清单
        return set(discover_source_adapters())
    return {name.strip() for name in raw.split(",") if name.strip()}


def disabled_source_result(label: str) -> dict[str, Any]:
    """这一家没被启用，整段不采。

    状态用 `skipped`：它和 `not_applicable` 一样被排除在「问题状态」之外，
    因此不会把整体拖成 `partial`，也不会打断流程去问用户。

    但 `reason` 必须标出来——「你没开这个平台」和「你主动跳过了某一项」
    在报告里不能长成一个样。

    字段取两家返回结构的并集，省得调用处再按平台分支。
    """
    return {
        "status": "skipped",
        "reason": "not_enabled",
        "message": f"{label}未启用，已跳过",
        "source_count": 0,
        "design_count": 0,
        "preview_count": 0,
        "api_count": 0,
        "items": [],
        "preview_failures": [],
    }


async def get_manifest(
    url: str,
    output_dir: str,
) -> dict[str, Any]:
    """定位已收集需求在用户缓存中的清单，不返回清单正文。"""
    provider = provider_of("requirement", url)
    if provider is None:
        return no_provider_result("requirement", url)
    url_error = provider.validate_todo_url(url)
    if url_error:
        return {"status": "invalid_input", "message": url_error}
    try:
        target_dir = Path(output_dir).expanduser()
        if not target_dir.is_absolute():
            raise ValueError("output_dir 必须是绝对路径")
        manifest_file = requirement_manifest_file(
            provider.cache_key({}, url),
            target_dir,
        )
        ensure_no_symlink_components(manifest_file)
    except UnsafePathError as error:
        return {
            "status": "invalid_output",
            "message": str(error),
        }
    except ValueError as error:
        return {"status": "invalid_input", "message": str(error)}
    if not manifest_file.is_file():
        return {
            "status": "cache_missing",
            "manifest_file": str(manifest_file),
            "message": "收集清单缓存不存在；请先完成需求资料收集",
        }
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return {
            "status": "cache_invalid",
            "manifest_file": str(manifest_file),
            "message": str(error),
        }
    if (
        (manifest.get("requirement") or {}).get("source_url") != url
        or Path(str(manifest.get("output_dir") or "")).resolve()
        != target_dir.resolve()
    ):
        return {
            "status": "cache_invalid",
            "manifest_file": str(manifest_file),
            "message": "收集清单与请求的任务链接或项目目录不一致",
        }
    return {
        "status": manifest.get("status", "success"),
        "manifest_file": str(manifest_file.resolve()),
        "output_dir": manifest.get("output_dir") or str(target_dir.resolve()),
        "schema_version": manifest.get("schema_version"),
    }


async def collect(
    url: str,
    output_dir: str = "",
    confirmed_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """确定性编排任务、设计稿与 API 文档来源；未确认范围时只返回候选。"""
    provider = provider_of("requirement", url)
    if provider is None:
        return no_provider_result("requirement", url)
    url_error = provider.validate_todo_url(url)
    if url_error:
        return {"status": "invalid_input", "message": url_error}
    try:
        data, cache_file = provider.read_cached_data(url)
    except FileNotFoundError as error:
        return {
            "status": "cache_missing",
            "platform": provider.PLATFORM,
            "message": str(error),
        }
    except Exception as error:
        return {
            "status": "cache_invalid",
            "platform": provider.PLATFORM,
            "message": str(error),
        }

    if confirmed_scope is None:
        candidates = await discover_candidates(data)
        suggested_scope, scope_ready = suggested_scope_from_candidates(candidates)
        return {
            **provider.read_summary(
                data,
                cache_file,
                data["image_cache"],
            ),
            "status": (
                "scope_ready" if scope_ready
                else "scope_confirmation_required"
            ),
            "candidates": candidates,
            "suggested_scope": suggested_scope,
            "suggested_directory_name": safe_directory_name(
                data.get("title") or "",
                provider.cache_key(data, url),
            ),
            "message": (
                "来源候选唯一，可使用 suggested_scope"
                if scope_ready
                else "请确认任务附件、设计稿和 API 范围后再次调用"
            ),
        }
    if not output_dir:
        return {
            "status": "invalid_input",
            "message": "确认完整收集后必须提供绝对 output_dir",
        }
    requested_output = Path(output_dir).expanduser()
    unsafe_output = unsafe_symlink_components(requested_output)
    if unsafe_output:
        return {
            "status": "invalid_output",
            "output_dir": str(requested_output),
            "unsafe_paths": [str(path) for path in unsafe_output],
            "message": "output_dir 路径中不允许符号链接",
        }
    source_adapters = discover_source_adapters()
    try:
        normalized_scope = normalize_scope_with_adapters(
            confirmed_scope,
            source_adapters,
        )
        target_dir = prepare_output_dir(output_dir)
    except UnsafePathError as error:
        return {
            "status": "invalid_output",
            "output_dir": str(requested_output),
            "message": str(error),
        }
    except ValueError as error:
        return {"status": "invalid_input", "message": str(error)}

    cache_paths = [
        requirement_manifest_file(
            provider.cache_key(data, url),
            target_dir,
        ),
    ]
    for name, adapter in source_adapters.items():
        cache_paths.extend(adapter.cache_paths(normalized_scope[name]))
    unsafe_cache_paths = sorted({
        str(component)
        for path in cache_paths
        for component in unsafe_symlink_components(path)
    })
    if unsafe_cache_paths:
        return {
            "status": "invalid_output",
            "output_dir": str(target_dir),
            "unsafe_paths": unsafe_cache_paths,
            "message": "SpecWeaver 用户缓存路径中不允许符号链接",
        }

    managed_paths = [
        target_dir / "requirement-raw.md",
        target_dir / "requirement-attachments",
        # 下面两个文件名与 tower-* 目录/raw 是旧版产物，列进来是让
        # replace_existing 的确认与清理逻辑也覆盖它们
        target_dir / "tower-raw.md",
        target_dir / "tower-attachments.json",
        target_dir / "collection-manifest.json",
        target_dir / "tower-attachments",
    ]
    for adapter in source_adapters.values():
        managed_paths.extend(adapter.managed_paths(target_dir))
    existing_managed = [
        str(path)
        for path in managed_paths
        if path.exists()
    ]
    unsafe_paths = unsafe_managed_paths(managed_paths)
    if unsafe_paths:
        return {
            "status": "invalid_output",
            "output_dir": str(target_dir),
            "unsafe_paths": unsafe_paths,
            "message": "脚本管理路径中不允许符号链接",
        }
    if existing_managed and not normalized_scope["replace_existing"]:
        return {
            "status": "existing_output_confirmation_required",
            "output_dir": str(target_dir),
            "existing_managed_paths": existing_managed,
            "message": (
                "目标目录已有脚本管理的来源文件；请确认更新后将 "
                "replace_existing 设为 true"
            ),
        }
    if normalized_scope["replace_existing"]:
        clean_legacy_project_artifacts(target_dir)
        for adapter in source_adapters.values():
            adapter.clean(target_dir)

    project_raw = atomic_write_text(
        target_dir / "requirement-raw.md",
        cache_file.read_text(encoding="utf-8"),
    )
    if normalized_scope["tower_attachments"]:
        skipped_attachments = {
            item["url"]: item["reason"]
            for item in normalized_scope["skipped_sources"]
            if item["source"] in {"requirement", "tower"}
        }
        attachments = await download_task_attachments(
            provider,
            data,
            target_dir,
            allow_restricted=bool(
                normalized_scope["allow_restricted_attachments"]
            ),
            skipped_urls=skipped_attachments,
        )
    else:
        owned_attachment = re.compile(
            r"(?:requirement|tower)-attachment-\d{3}\.[a-z0-9]{1,10}",
            re.I,
        )
        for old_dir_name in ("requirement-attachments", "tower-attachments"):
            old_attachment_dir = target_dir / old_dir_name
            if not old_attachment_dir.is_dir():
                continue
            for old_file in old_attachment_dir.iterdir():
                if (
                    old_file.is_file()
                    and owned_attachment.fullmatch(old_file.name)
                ):
                    old_file.unlink()
        attachments = {
            "status": "skipped",
            "saved_count": 0,
            "failures": [],
            "skipped": [],
            "occurrences": [],
            "downloaded": [],
            "source_count": len(data.get("attachments") or []),
            "artifacts": [],
        }
    sources = enabled_sources()
    source_results = {}
    for name, adapter in source_adapters.items():
        source_results[name] = (
            await adapter.collect(normalized_scope[name], target_dir)
            if name in sources
            else disabled_source_result(adapter.LABEL)
        )
    manifest = {
        "schema_version": 3,
        "output_dir": str(target_dir),
        "requirement": {
            "status": "success",
            "platform": provider.PLATFORM,
            "todo_id": data.get("todo_id") or "未提供",
            "task_title": data.get("title") or "未提供",
            "task_type": data.get("task_type", "requirement"),
            "source_url": url,
            "raw_file": relative_to_output(project_raw, target_dir),
            "attachments": attachments,
        },
        **source_results,
        "confirmed_scope": normalized_scope,
    }
    missing = verify_artifacts(target_dir, manifest, source_adapters)
    statuses = {
        attachments["status"],
        *(result["status"] for result in source_results.values()),
    }
    status = collect_outcome_status(statuses, missing=bool(missing))
    manifest["verification"] = {
        "status": "success" if not missing else "failed",
        "missing": missing,
    }
    manifest["status"] = status
    manifest_file = atomic_write_text(
        requirement_manifest_file(
            provider.cache_key(data, url),
            target_dir,
        ),
        canonical_json(manifest),
    )
    unresolved = [
        item["message"]
        for item in attachments.get("skipped", [])
        if item.get("status") != "skipped"
    ]
    unresolved.extend(
        item.get("message") or (
            f"任务附件失败：{item.get('source_url', '未提供')}"
        )
        for item in attachments.get("failures", [])
    )
    for name, adapter in source_adapters.items():
        unresolved.extend(adapter.unresolved(source_results[name]))
    unresolved.extend(f"缺少文件：{item}" for item in missing)
    counts = {}
    for name, adapter in source_adapters.items():
        counts.update(adapter.count(source_results[name]))
    return {
        "status": status,
        "output_dir": str(target_dir),
        "manifest_file": str(manifest_file),
        "requirement_raw_file": str(project_raw),
        "attachment_count": attachments.get("saved_count", 0),
        **counts,
        "unresolved": unresolved,
        "next_action": collect_next_action(status),
    }
