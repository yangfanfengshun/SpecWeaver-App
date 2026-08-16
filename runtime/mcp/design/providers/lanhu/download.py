from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

import httpx

from common import (
    DOWNLOAD_CONCURRENCY,
    IMAGE_EXTENSIONS,
    atomic_write_bytes,
    prepare_output_dir,
)
from design.providers.lanhu.api import (
    create_client,
    get_lanhu_image,
    is_lanhu_image_url,
)
from design.providers.lanhu.design import write_design_document


def _classify_lanhu_download_error(error: BaseException) -> tuple[str, str]:
    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        status = (
            "auth_expired" if code in {401, 418}
            else "forbidden" if code == 403
            else "api_error"
        )
        return status, f"蓝湖返回 HTTP {code}"
    if isinstance(error, httpx.HTTPError):
        return "network_error", str(error)
    return "invalid_input", str(error)


def safe_file_stem(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", value).strip(" .-")
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("file_stem 不能为空")
    raw = cleaned.encode("utf-8")[:200]
    while raw:
        try:
            return raw.decode("utf-8").rstrip(" .")
        except UnicodeDecodeError:
            raw = raw[:-1]
    raise ValueError("file_stem 不能为空")


async def download_design_images(
    cookie: str,
    images: list[dict[str, Any]],
    output_dir: str,
) -> dict[str, Any]:
    target_dir = prepare_output_dir(output_dir)
    downloaded: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    hashes: dict[str, dict[str, Any]] = {}
    saved_names: set[str] = set()

    async with create_client(cookie, image_accept=True) as client:
        semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

        async def fetch_one(image: dict[str, Any]) -> httpx.Response:
            image_url = str(image.get("url") or "")
            if not is_lanhu_image_url(image_url):
                raise ValueError("只允许下载蓝湖 HTTPS 图片链接")
            async with semaphore:
                return await get_lanhu_image(client, image_url)

        # 并发拉取（信号量限流），哈希去重/命名按原始顺序串行处理，
        # 保证文件命名与去重结果只取决于 images 顺序，与网络返回先后无关。
        responses = await asyncio.gather(
            *(fetch_one(image) for image in images),
            return_exceptions=True,
        )

        for source_index, (image, result) in enumerate(zip(images, responses), 1):
            design_name = str(image.get("name") or "")
            design_id = str(image.get("id") or "")
            image_url = str(image.get("url") or "")
            if isinstance(result, BaseException):
                status, message = _classify_lanhu_download_error(result)
                failures.append({
                    "source_index": source_index,
                    "design_id": design_id,
                    "design_name": design_name,
                    "source_url": image_url,
                    "status": status,
                    "error": message,
                })
                continue
            response = result
            try:
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                extension = IMAGE_EXTENSIONS.get(content_type)
                if not extension:
                    raise ValueError(f"不支持的图片类型: {content_type or '未知类型'}")

                content_hash = hashlib.sha256(response.content).hexdigest()
                if content_hash in hashes:
                    downloaded.append({
                        "source_index": source_index,
                        "design_id": design_id,
                        "design_name": design_name,
                        "source_url": image_url,
                        "duplicate": True,
                        "duplicate_of": hashes[content_hash]["file_name"],
                        "sha256": content_hash,
                    })
                    continue

                requested_stem = str(image.get("file_stem") or "")
                file_stem = (
                    safe_file_stem(requested_stem)
                    if requested_stem
                    else f"lanhu-{len(hashes) + 1:03d}-preview"
                )
                file_name = f"{file_stem}{extension}"
                file_path = target_dir / file_name
                atomic_write_bytes(file_path, response.content)
                item = {
                    "source_index": source_index,
                    "design_id": design_id,
                    "design_name": design_name,
                    "source_url": image_url,
                    "file_name": file_name,
                    "path": str(file_path),
                    "content_type": content_type,
                    "bytes": len(response.content),
                    "sha256": content_hash,
                    "duplicate": False,
                }
                hashes[content_hash] = item
                saved_names.add(file_name)
                downloaded.append(item)
            except Exception as error:
                status, message = _classify_lanhu_download_error(error)
                failures.append({
                    "source_index": source_index,
                    "design_id": design_id,
                    "design_name": design_name,
                    "source_url": image_url,
                    "status": status,
                    "error": message,
                })

    if not failures:
        owned_pattern = re.compile(
            r"lanhu-\d{3}(?:-preview)?\.(gif|jpe?g|png|svg|webp)$",
            re.I,
        )
        for old_file in target_dir.iterdir():
            if (
                old_file.is_file()
                and owned_pattern.fullmatch(old_file.name)
                and old_file.name not in saved_names
            ):
                old_file.unlink()

    status = "success" if not failures else "partial"
    if failures and not downloaded:
        failure_statuses = {item["status"] for item in failures}
        if len(failure_statuses) == 1:
            status = failure_statuses.pop()
    return {
        "status": status,
        "platform": "lanhu",
        "source_count": len(images),
        "saved_count": len(hashes),
        "output_dir": str(target_dir),
        "images": downloaded,
        "failures": failures,
        "stale_files_removed": not failures,
    }


def load_manifest(
    manifest_file: str,
    design_id: str,
) -> tuple[Path | None, dict[str, Any] | None]:
    if not manifest_file:
        return None, None
    path = Path(manifest_file).expanduser()
    if not path.is_absolute():
        raise ValueError("manifest_file 必须是绝对路径")
    if not path.is_file():
        raise ValueError("manifest_file 不存在")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest_design_id = str((manifest.get("source") or {}).get("design_id") or "")
    if manifest_design_id and manifest_design_id != design_id:
        raise ValueError("manifest_file 与当前 design_id 不一致")
    return path, manifest


async def download_slice_assets(
    cookie: str,
    assets: list[dict[str, Any]],
    output_dir: str,
    design_id: str,
    manifest_file: str = "",
) -> dict[str, Any]:
    target_dir = prepare_output_dir(output_dir)
    manifest_path, manifest = load_manifest(manifest_file, design_id)
    downloaded: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    hashes: dict[str, dict[str, Any]] = {}
    saved_paths: set[Path] = set()
    counters = {"icon": 0, "img": 0, "bg": 0}

    async with create_client(cookie, image_accept=True) as client:
        semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

        async def fetch_one(asset: dict[str, Any]) -> httpx.Response:
            source_url = str(asset["source_url"])
            category = str(asset["category"])
            if category not in counters:
                raise ValueError(f"未知切图分类: {category}")
            if not is_lanhu_image_url(source_url):
                raise ValueError("只允许下载蓝湖 HTTPS 切图链接")
            async with semaphore:
                return await get_lanhu_image(client, source_url)

        # 并发拉取（信号量限流），哈希去重/分类计数按原始顺序串行处理，
        # 保证 `lanhu-slice-NNN` 编号只取决于 assets 顺序，与网络返回先后无关。
        responses = await asyncio.gather(
            *(fetch_one(asset) for asset in assets),
            return_exceptions=True,
        )

        for asset, result in zip(assets, responses):
            category = str(asset["category"])
            if isinstance(result, BaseException):
                status, message = _classify_lanhu_download_error(result)
                failures.append({**asset, "status": status, "error": message})
                continue
            response = result
            try:
                content_type = (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .lower()
                )
                extension = IMAGE_EXTENSIONS.get(content_type)
                if not extension:
                    raise ValueError(f"不支持的图片类型: {content_type or '未知类型'}")
                content_hash = hashlib.sha256(response.content).hexdigest()
                if content_hash in hashes:
                    original = hashes[content_hash]
                    downloaded.append({
                        **asset,
                        "status": "duplicate",
                        "local_path": original["local_path"],
                        "duplicate_of": original["file_name"],
                        "sha256": content_hash,
                    })
                    continue

                counters[category] += 1
                category_dir = target_dir / category
                category_dir.mkdir(parents=True, exist_ok=True)
                file_name = f"lanhu-slice-{counters[category]:03d}{extension}"
                file_path = category_dir / file_name
                atomic_write_bytes(file_path, response.content)
                item = {
                    **asset,
                    "status": "success",
                    "file_name": file_name,
                    "local_path": str(file_path.resolve()),
                    "content_type": content_type,
                    "bytes": len(response.content),
                    "sha256": content_hash,
                }
                hashes[content_hash] = item
                saved_paths.add(file_path.resolve())
                downloaded.append(item)
            except Exception as error:
                status, message = _classify_lanhu_download_error(error)
                failures.append({**asset, "status": status, "error": message})

    if not failures:
        owned_pattern = re.compile(
            r"lanhu-slice-\d{3}\.(gif|jpe?g|png|svg|webp)$",
            re.I,
        )
        for category in counters:
            category_dir = target_dir / category
            if not category_dir.is_dir():
                continue
            for old_file in category_dir.iterdir():
                resolved = old_file.resolve()
                if (
                    old_file.is_file()
                    and owned_pattern.fullmatch(old_file.name)
                    and resolved not in saved_paths
                ):
                    old_file.unlink()

    mappings = downloaded + failures
    if manifest is not None and manifest_path is not None:
        for item in mappings:
            local_path = item.get("local_path")
            if local_path:
                item["local_path"] = os.path.relpath(
                    str(local_path),
                    start=manifest_path.parent,
                )
        manifest["assets"] = mappings
        write_design_document(manifest, str(manifest_path))

    status = "success" if not failures else "partial"
    if failures and not downloaded:
        failure_statuses = {item["status"] for item in failures}
        if len(failure_statuses) == 1:
            status = failure_statuses.pop()
    return {
        "status": status,
        "platform": "lanhu",
        "design_id": design_id,
        "source_count": len(assets),
        "saved_count": len(hashes),
        "output_dir": str(target_dir),
        "manifest_file": str(manifest_path.resolve()) if manifest_path else None,
        "assets": mappings,
        "failures": failures,
        "stale_files_removed": not failures,
    }
