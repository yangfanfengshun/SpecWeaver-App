"""需求收集的纯辅助函数：路径/文件名安全处理、缓存路径计算、范围归一化、
错误分类。全部不发请求、不依赖任何 provider 的会话状态。
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
from types import ModuleType
from typing import Any
from urllib.parse import urlparse

import httpx


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def safe_directory_name(title: str, fallback: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", title).strip(" .-")
    if not value or value in {".", ".."}:
        value = f"requirement-{fallback}"
    return value[:100].rstrip(" .") or f"requirement-{fallback}"


def safe_file_component(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", value).strip(" .-")
    raw = (cleaned or fallback).encode("utf-8")[:100]
    while raw:
        try:
            return raw.decode("utf-8").rstrip(" .") or fallback
        except UnicodeDecodeError:
            raw = raw[:-1]
    return fallback


def specweaver_home() -> Path:
    return Path(
        os.getenv("SPECWEAVER_HOME", Path.home() / ".specweaver")
    ).expanduser().absolute()


def requirement_manifest_file(task_key: str, output_dir: Path) -> Path:
    output_hash = hashlib.sha256(
        str(output_dir.resolve()).encode("utf-8")
    ).hexdigest()[:16]
    return (
        specweaver_home()
        / "cache"
        / "requirement"
        / task_key
        / output_hash
        / "manifest.json"
    )


def design_cache_file(platform: str, image_id: str, name: str) -> Path:
    safe_platform = safe_file_component(platform, "unknown")
    safe_id = safe_file_component(image_id, "unknown")
    safe_name = safe_file_component(name, "未命名设计")
    return (
        specweaver_home()
        / "cache"
        / "design"
        / safe_platform
        / safe_id
        / f"{safe_name}--{safe_id}.json"
    )


def relative_to_output(path: str | Path, output_dir: Path) -> str:
    return str(Path(path).resolve().relative_to(output_dir.resolve()))


def safe_extension(source_url: str, content_type: str) -> str:
    guessed = mimetypes.guess_extension(content_type, strict=False) or ""
    if guessed == ".jpe":
        guessed = ".jpg"
    if guessed:
        return guessed
    suffix = Path(urlparse(source_url).path).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        return suffix
    return ".bin"


def error_status(error: Exception) -> tuple[str, str]:
    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        status = (
            "auth_expired" if code == 401
            else "forbidden" if code == 403
            else "api_error"
        )
        return status, f"HTTP {code}"
    if isinstance(error, httpx.HTTPError):
        return "network_error", str(error)
    return "download_error", str(error)


def design_error(error: Exception) -> tuple[str, str]:
    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        status = (
            "auth_expired" if code in {401, 418}
            else "forbidden" if code == 403
            else "api_error"
        )
        return status, f"设计稿来源返回 HTTP {code}"
    if isinstance(error, httpx.HTTPError):
        return "network_error", str(error)
    return "api_error", str(error)


def unsafe_managed_paths(paths: list[Path]) -> list[str]:
    unsafe = []
    for path in paths:
        if path.is_symlink():
            unsafe.append(str(path))
            continue
        if not path.is_dir():
            continue
        for child in path.rglob("*"):
            if child.is_symlink():
                unsafe.append(str(child))
    return unsafe


def remove_empty_directories(root: Path) -> None:
    if not root.is_dir():
        return
    for directory in sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass
    try:
        root.rmdir()
    except OSError:
        pass


def clean_legacy_project_artifacts(output_dir: Path) -> None:
    for file_name in (
        "collection-manifest.json",
        "tower-attachments.json",
        "tower-raw.md",
    ):
        path = output_dir / file_name
        if path.is_file() and not path.is_symlink():
            path.unlink()


def normalize_scope(
    confirmed_scope: dict[str, Any],
    source_adapters: dict[str, ModuleType],
) -> dict[str, Any]:
    if not isinstance(confirmed_scope, dict):
        raise ValueError("confirmed_scope 必须是对象")
    required = {
        "tower_attachments",
        "allow_restricted_attachments",
        "replace_existing",
        "skipped_sources",
    } | set(source_adapters)
    missing = sorted(required - confirmed_scope.keys())
    if missing:
        raise ValueError(
            f"confirmed_scope 缺少字段: {', '.join(missing)}"
        )
    for key in (
        "tower_attachments",
        "allow_restricted_attachments",
        "replace_existing",
    ):
        value = confirmed_scope[key]
        if not isinstance(value, bool):
            raise ValueError(f"confirmed_scope.{key} 必须是布尔值")
    normalized_sources = {
        name: adapter.normalize_scope(confirmed_scope.get(name))
        for name, adapter in source_adapters.items()
    }
    skipped_sources = confirmed_scope.get("skipped_sources") or []
    if not isinstance(skipped_sources, list) or not all(
        isinstance(item, dict) for item in skipped_sources
    ):
        raise ValueError("confirmed_scope.skipped_sources 必须是对象数组")
    normalized_skipped = []
    seen_skipped = set()
    for item in skipped_sources:
        normalized = {
            "source": str(item.get("source") or "").strip(),
            "url": str(item.get("url") or "").strip(),
            "reason": str(item.get("reason") or "").strip(),
        }
        key = (
            normalized["source"],
            normalized["url"],
            normalized["reason"],
        )
        if key not in seen_skipped:
            seen_skipped.add(key)
            normalized_skipped.append(normalized)
    normalized_skipped.sort(
        key=lambda item: (item["source"], item["url"], item["reason"])
    )
    allowed_source_names = {"requirement", "tower", *source_adapters}
    for adapter in source_adapters.values():
        allowed_source_names.update(
            getattr(adapter, "LEGACY_SOURCE_NAMES", set())
        )
    for item in normalized_skipped:
        if (
            item["source"] not in allowed_source_names
            or not item["url"]
            or not item["reason"]
        ):
            raise ValueError(
                "skipped_sources 每项必须包含来源、URL 和原因"
            )
    return {
        "tower_attachments": confirmed_scope["tower_attachments"],
        "allow_restricted_attachments": confirmed_scope[
            "allow_restricted_attachments"
        ],
        "replace_existing": confirmed_scope["replace_existing"],
        **normalized_sources,
        "skipped_sources": normalized_skipped,
    }


def parse_size_bytes(value: str) -> int | None:
    normalized = value.strip().replace(",", "")
    match = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|字节)?",
        normalized,
        re.I,
    )
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "B").upper()
    multiplier = {
        "B": 1,
        "字节": 1,
        "KB": 1024,
        "MB": 1024 ** 2,
        "GB": 1024 ** 3,
    }[unit]
    return int(number * multiplier)


def verify_artifacts(
    output_dir: Path,
    manifest: dict[str, Any],
    source_adapters: dict[str, ModuleType],
) -> list[str]:
    missing = []
    requirement = manifest.get("requirement") or manifest.get("tower") or {}
    paths = [
        requirement.get("raw_file") or "requirement-raw.md",
        *(requirement.get("attachments") or {}).get("artifacts", []),
    ]
    for relative in filter(None, paths):
        candidate = Path(str(relative))
        if candidate.is_absolute():
            continue
        path = (output_dir / candidate).resolve()
        try:
            path.relative_to(output_dir.resolve())
        except ValueError:
            missing.append(f"路径越界：{relative}")
            continue
        if not path.is_file():
            missing.append(str(relative))
    for name, adapter in source_adapters.items():
        missing.extend(adapter.verify(output_dir, manifest.get(name) or {}))
    return missing
