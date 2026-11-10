"""月维 design provider：read / 预览 / 切图内部实现。"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
from io import BytesIO
from typing import Any

import httpx
from PIL import Image

from common import (
    IMAGE_EXTENSIONS,
    atomic_write_bytes,
    http_error_result,
    prepare_output_dir,
)
from design.schema import count_nodes, navigation, write_design_document
from design.providers.moonvy.auth import (
    MoonvyAuthError,
    headers,
    token_from_config,
    verify_token,
)
from design.providers.moonvy.normalize import (
    artboard_from_genome,
    document_from_moonvy,
    moonvy_slices,
)
from design.providers.moonvy.parse import parse_moonvy_url

API_BASE = "https://api.moonvy.com"
INLINE_NODE_LIMIT = 120
_LIST_PAGE_CAP = 40


def _config_error() -> dict[str, str] | None:
    if token_from_config():
        return None
    return {
        "status": "missing_config",
        "platform": "moonvy",
        "message": "未设置 MOONVY_TOKEN",
    }


def _http_error(error: Exception) -> dict[str, str]:
    mapped = http_error_result(
        error,
        platform="moonvy",
        status_by_code={401: "auth_expired", 403: "forbidden", 404: "not_found"},
        default_status="api_error",
    )
    if mapped:
        if mapped["status"] == "auth_expired":
            mapped["message"] = (
                "月维 Token 无效或已过期（约 180 天），请从已登录的月维页面重新复制"
            )
        elif mapped["status"] == "forbidden":
            mapped["message"] = "当前月维 Token 没有这份设计的访问权限"
        return mapped
    if isinstance(error, MoonvyAuthError):
        if error.kind == "credentials":
            status = "auth_expired"
        elif error.kind == "forbidden":
            status = "forbidden"
        elif error.kind == "missing":
            status = "missing_config"
        else:
            status = "network_error"
        return {"status": status, "platform": "moonvy", "message": str(error)}
    return {"status": "api_error", "platform": "moonvy", "message": str(error)}


def _file_stem(name: str, node_id: str) -> str:
    safe = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", name).strip("-") or "slice"
    suffix = node_id.replace(":", "-")[:12] or "node"
    return f"{safe}--{suffix}"


def _slice_filename(stem: str, item: dict[str, Any]) -> str:
    if item.get("role") == "2x":
        return f"{stem}@2x{item['extension']}"
    return f"{stem}{item['extension']}"


def _scaled_bytes(content: bytes, source_ratio: float, target_ratio: float) -> bytes:
    if source_ratio == target_ratio:
        return content
    with Image.open(BytesIO(content)) as image:
        image.load()
        factor = target_ratio / source_ratio
        resized = image.resize(
            (
                max(1, round(image.width * factor)),
                max(1, round(image.height * factor)),
            ),
            Image.Resampling.LANCZOS,
        )
        buffer = BytesIO()
        kind = (image.format or "PNG").upper()
        if kind == "JPEG":
            if resized.mode not in {"RGB", "L"}:
                resized = resized.convert("RGB")
            resized.save(buffer, format="JPEG", quality=90)
        elif kind == "WEBP":
            resized.save(buffer, format="WEBP")
        elif kind == "GIF":
            resized.save(buffer, format="GIF")
        else:
            resized.save(buffer, format="PNG")
        return buffer.getvalue()


async def _post_json(
    client: httpx.AsyncClient,
    path: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    response = await client.post(path, json=body)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("月维响应不是 JSON 对象")
    return payload


def _candidate_row(node: dict[str, Any], index: int) -> dict[str, Any]:
    meta = node.get("meta") if isinstance(node.get("meta"), dict) else {}
    info = meta.get("designInfo") if isinstance(meta.get("designInfo"), dict) else {}
    return {
        "index": index,
        "id": node.get("id"),
        "name": node.get("name"),
        "width": info.get("w"),
        "height": info.get("h"),
        "sectors": [],
    }


def _selected_id(params: dict[str, str], image_id: str) -> str:
    requested = image_id.strip()
    linked = params["file_id"]
    if requested and linked and requested != linked:
        raise ValueError("image_id 与月维链接中的设计图不一致")
    selected = requested or linked
    if not selected:
        raise ValueError("缺少 image_id；请先确认设计范围")
    return selected


async def _fetch_node(
    client: httpx.AsyncClient,
    project_id: str,
    node_id: str,
) -> dict[str, Any]:
    return await _post_json(
        client,
        "/anynode/get",
        {"projectId": project_id, "id": node_id, "lv": "full"},
    )


async def _list_designs(
    client: httpx.AsyncClient,
    project_id: str,
    dir_id: str,
) -> list[dict[str, Any]]:
    designs: list[dict[str, Any]] = []
    page = 0
    while page < _LIST_PAGE_CAP:
        body: dict[str, Any] = {"projectId": project_id, "pageIndex": page}
        if dir_id:
            body["id"] = dir_id
        payload = await _post_json(client, "/anynode/list", body)
        batch = payload.get("list") or []
        if not isinstance(batch, list):
            break
        for item in batch:
            if isinstance(item, dict) and item.get("type") == "design":
                designs.append(item)
        if not payload.get("hasNext") or not batch:
            break
        page += 1
    return designs


def _decode_genome(content: bytes) -> dict[str, Any]:
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("月维 genome 不是 JSON 对象")
    return payload


async def _load_screen(
    client: httpx.AsyncClient,
    project_id: str,
    node_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    node = await _fetch_node(client, project_id, node_id)
    if node.get("type") != "design":
        raise ValueError("这条月维链接不是设计图")
    genome_url = str(((node.get("files") or {}).get("genome") or {}).get("url") or "")
    if not genome_url:
        raise ValueError("月维设计图缺少 genome")
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as files:
        response = await files.get(genome_url)
    response.raise_for_status()
    genome = _decode_genome(response.content)
    page = artboard_from_genome(genome)
    return node, genome, page


async def check_auth(project_url: str = "") -> dict[str, str]:
    early = _config_error()
    if early:
        return early
    token = token_from_config()
    try:
        verify_token(token)
        if not project_url:
            return {
                "status": "configured",
                "platform": "moonvy",
                "message": "已配置月维 Token；提供设计链接后可验证项目权限",
            }
        params = parse_moonvy_url(project_url)
        async with httpx.AsyncClient(
            base_url=API_BASE,
            timeout=60,
            headers=headers(token),
        ) as client:
            if params["file_id"]:
                await _fetch_node(client, params["project_id"], params["file_id"])
            else:
                await _post_json(
                    client,
                    "/anynode/list",
                    {
                        "projectId": params["project_id"],
                        "pageIndex": 0,
                        **({"id": params["dir_id"]} if params["dir_id"] else {}),
                    },
                )
        return {"status": "success", "platform": "moonvy", "message": "月维认证有效"}
    except ValueError as error:
        return {"status": "invalid_input", "platform": "moonvy", "message": str(error)}
    except Exception as error:
        return _http_error(error)


async def get_candidates(url: str) -> dict[str, Any]:
    early = _config_error()
    if early:
        return early
    try:
        params = parse_moonvy_url(url)
    except ValueError as error:
        return {"status": "invalid_input", "platform": "moonvy", "message": str(error)}
    token = token_from_config()
    try:
        async with httpx.AsyncClient(
            base_url=API_BASE,
            timeout=60,
            headers=headers(token),
        ) as client:
            if params["file_id"]:
                node = await _fetch_node(client, params["project_id"], params["file_id"])
                if node.get("type") != "design":
                    return {
                        "status": "not_found",
                        "platform": "moonvy",
                        "message": "这条月维链接不是设计图",
                    }
                designs = [_candidate_row(node, 1)]
            else:
                nodes = await _list_designs(client, params["project_id"], params["dir_id"])
                designs = [
                    _candidate_row(node, index)
                    for index, node in enumerate(nodes, 1)
                ]
        return {
            "status": "success",
            "platform": "moonvy",
            "project_name": "",
            "total_designs": len(designs),
            "designs": designs,
        }
    except Exception as error:
        return _http_error(error)


async def get_detail(
    url: str,
    image_id: str = "",
    output_file: str = "",
) -> dict[str, Any]:
    early = _config_error()
    if early:
        return early
    try:
        params = parse_moonvy_url(url)
        selected_id = _selected_id(params, image_id)
        token = token_from_config()
        async with httpx.AsyncClient(
            base_url=API_BASE,
            timeout=120,
            headers=headers(token),
            follow_redirects=True,
        ) as client:
            node, _genome, page = await _load_screen(
                client,
                params["project_id"],
                selected_id,
            )
            document = document_from_moonvy(page)
            preview = node.get("preview") if isinstance(node.get("preview"), dict) else {}
            preview_url = str(preview.get("normal") or "")
        result: dict[str, Any] = {
            "status": "success",
            "platform": "moonvy",
            "message": "已读取月维设计图",
            "source": {
                "url": url,
                "project_id": params["project_id"],
                "team_id": None,
                "design_id": selected_id,
                "name": node.get("name") or document.get("name"),
                "structure_source": "moonvy",
            },
            "canvas": document.get("canvas"),
            "preview_url": preview_url,
            "navigation": navigation(document),
        }
        if output_file:
            result["output_file"] = str(write_design_document(document, output_file))
            result["delivery"] = "file"
            return result
        if count_nodes(document) > INLINE_NODE_LIMIT:
            result.update({
                "delivery": "summary",
                "truncated": True,
                "message": "设计稿较大，已返回精简导航；传入绝对 output_file 可保存完整规范化 JSON",
            })
            return result
        result["delivery"] = "inline"
        result["truncated"] = False
        result["document"] = document
        return result
    except ValueError as error:
        return {"status": "invalid_input", "platform": "moonvy", "message": str(error)}
    except Exception as error:
        return _http_error(error)


async def read(
    url: str,
    image_id: str = "",
    output_file: str = "",
) -> dict[str, Any]:
    """项目或文件夹链接返回候选屏；带 image_id 或设计图链接返回统一 schema。"""
    try:
        params = parse_moonvy_url(url)
    except ValueError as error:
        return {"status": "invalid_input", "platform": "moonvy", "message": str(error)}
    if image_id.strip() or params["file_id"]:
        return await get_detail(url, image_id, output_file)
    return await get_candidates(url)


async def download_previews(
    images: list[dict[str, Any]],
    output_dir: str,
) -> dict[str, Any]:
    early = _config_error()
    if early:
        return early
    target_dir = prepare_output_dir(output_dir)
    downloaded: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            for index, image in enumerate(images, 1):
                url = str(image.get("url") or "")
                design_id = str(image.get("id") or "")
                if not url:
                    failures.append({
                        "source_index": index,
                        "design_id": design_id,
                        "status": "invalid_input",
                        "error": "缺少预览地址",
                    })
                    continue
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    extension = IMAGE_EXTENSIONS.get(content_type, ".png")
                    stem = str(image.get("file_stem") or "preview")
                    path = target_dir / f"{stem}{extension}"
                    atomic_write_bytes(path, response.content)
                    downloaded.append({
                        "source_index": index,
                        "design_id": design_id,
                        "design_name": str(image.get("name") or ""),
                        "file_name": path.name,
                        "path": str(path),
                        "bytes": len(response.content),
                        "sha256": hashlib.sha256(response.content).hexdigest(),
                    })
                except Exception as error:
                    failures.append({
                        "source_index": index,
                        "design_id": design_id,
                        "status": "api_error",
                        "error": str(error),
                    })
    except Exception as error:
        return _http_error(error)
    status = "success" if not failures else "partial"
    if failures and not downloaded:
        status = str(failures[0].get("status", "api_error"))
    return {
        "status": status,
        "platform": "moonvy",
        "saved_count": len(downloaded),
        "images": downloaded,
        "failures": failures,
    }


async def download_slices(
    url: str,
    image_id: str,
    output_dir: str,
    manifest_file: str = "",
) -> dict[str, Any]:
    del manifest_file
    early = _config_error()
    if early:
        return early
    try:
        params = parse_moonvy_url(url)
        selected_id = _selected_id(params, image_id)
        token = token_from_config()
        target_dir = prepare_output_dir(output_dir)
        async with httpx.AsyncClient(
            base_url=API_BASE,
            timeout=120,
            headers=headers(token),
            follow_redirects=True,
        ) as client:
            _node, genome, page = await _load_screen(client, params["project_id"], selected_id)
            images = genome.get("images") if isinstance(genome.get("images"), dict) else {}
            slices = moonvy_slices(page, images)
        saved = []
        cached: dict[str, bytes] = {}
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            for item in slices:
                url = item["url"]
                content = cached.get(url)
                if content is None:
                    response = await client.get(url)
                    response.raise_for_status()
                    content = response.content
                    cached[url] = content
                name = _file_stem(item["layer_name"], item["layer_id"])
                path = target_dir / _slice_filename(name, item)
                atomic_write_bytes(
                    path,
                    _scaled_bytes(content, float(item["source_ratio"]), float(item["target_ratio"])),
                )
                saved.append({
                    "layer_id": item["layer_id"],
                    "layer_name": item["layer_name"],
                    "local_path": str(path),
                    "source_url": url,
                })
        return {
            "status": "success",
            "platform": "moonvy",
            "saved_count": len(saved),
            "assets": saved,
        }
    except ValueError as error:
        return {"status": "invalid_input", "platform": "moonvy", "message": str(error)}
    except Exception as error:
        return _http_error(error)
