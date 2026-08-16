"""Figma design provider：read / 预览 / 切图内部实现。"""
from __future__ import annotations

import hashlib
import re
from typing import Any

import httpx

from common import (
    IMAGE_EXTENSIONS,
    atomic_write_bytes,
    http_error_result,
    prepare_output_dir,
)
from design.schema import (
    count_nodes,
    navigation,
    write_design_document,
)
from design.providers.figma.auth import FigmaAuthError, headers, token_from_config, verify_token
from design.providers.figma.normalize import document_from_figma, figma_image_slices
from design.providers.figma.parse import parse_figma_url

API_BASE = "https://api.figma.com"
INLINE_NODE_LIMIT = 120
SCREEN_TYPES = frozenset({"FRAME", "COMPONENT"})


def _config_error() -> dict[str, str] | None:
    if token_from_config():
        return None
    return {
        "status": "missing_config",
        "platform": "figma",
        "message": "未设置 FIGMA_TOKEN",
    }


def _http_error(error: Exception) -> dict[str, str]:
    mapped = http_error_result(
        error,
        platform="figma",
        status_by_code={401: "auth_expired", 403: "forbidden"},
        default_status="api_error",
    )
    if mapped:
        if mapped["status"] == "auth_expired":
            mapped["message"] = (
                "Figma Token 无效或已过期（个人令牌最长 90 天），请重新生成后再粘贴"
            )
        elif mapped["status"] == "forbidden":
            mapped["message"] = "当前 Token 没有这份 Figma 文件的访问权限，请确认文件已分享"
        return mapped
    if isinstance(error, FigmaAuthError):
        if error.kind == "credentials":
            status = "auth_expired"
        elif error.kind == "forbidden":
            status = "forbidden"
        elif error.kind == "missing":
            status = "missing_config"
        else:
            status = "network_error"
        return {"status": status, "platform": "figma", "message": str(error)}
    return {"status": "api_error", "platform": "figma", "message": str(error)}


def _file_stem(name: str, node_id: str) -> str:
    safe = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", name).strip("-") or "node"
    return f"{safe}--{node_id.replace(':', '-')}"


async def _get_json(
    client: httpx.AsyncClient,
    path: str,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = await client.get(path, params=params)
    if response.status_code == 403:
        raise httpx.HTTPStatusError(
            "Forbidden",
            request=response.request,
            response=response,
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Figma 响应不是 JSON 对象")
    return payload


def _top_screens(canvas: dict[str, Any]) -> list[dict[str, Any]]:
    screens = []
    for child in canvas.get("children") or []:
        if not isinstance(child, dict):
            continue
        if child.get("type") in SCREEN_TYPES:
            screens.append(child)
    return screens


def _find_node(node: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    if str(node.get("id") or "") == node_id:
        return node
    for child in node.get("children") or []:
        if isinstance(child, dict):
            hit = _find_node(child, node_id)
            if hit is not None:
                return hit
    return None


def _candidate_row(node: dict[str, Any], index: int) -> dict[str, Any]:
    box = node.get("absoluteBoundingBox") if isinstance(node.get("absoluteBoundingBox"), dict) else {}
    return {
        "index": index,
        "id": node.get("id"),
        "name": node.get("name"),
        "width": box.get("width"),
        "height": box.get("height"),
        "sectors": [],
    }


async def check_auth(project_url: str = "") -> dict[str, str]:
    early = _config_error()
    if early:
        return early
    token = token_from_config()
    try:
        handle = verify_token(token)
        if not project_url:
            suffix = f"（{handle}）" if handle else ""
            return {
                "status": "configured",
                "platform": "figma",
                "message": f"已配置 Figma Token{suffix}；提供设计链接后可验证文件权限",
            }
        params = parse_figma_url(project_url)
        async with httpx.AsyncClient(base_url=API_BASE, timeout=60, headers=headers(token)) as client:
            await _get_json(client, f"/v1/files/{params['file_key']}", {"depth": "1"})
        return {"status": "success", "platform": "figma", "message": "Figma 认证有效"}
    except ValueError as error:
        return {"status": "invalid_input", "platform": "figma", "message": str(error)}
    except Exception as error:
        return _http_error(error)


async def get_candidates(url: str) -> dict[str, Any]:
    early = _config_error()
    if early:
        return early
    try:
        params = parse_figma_url(url)
    except ValueError as error:
        return {"status": "invalid_input", "platform": "figma", "message": str(error)}
    token = token_from_config()
    try:
        async with httpx.AsyncClient(base_url=API_BASE, timeout=60, headers=headers(token)) as client:
            if params["node_id"]:
                payload = await _get_json(
                    client,
                    f"/v1/files/{params['file_key']}/nodes",
                    {"ids": params["node_id"], "depth": "1"},
                )
                entry = (payload.get("nodes") or {}).get(params["node_id"]) or {}
                node = entry.get("document") if isinstance(entry, dict) else None
                if not isinstance(node, dict):
                    return {
                        "status": "not_found",
                        "platform": "figma",
                        "message": "Figma 链接中的节点不存在",
                    }
                if node.get("type") in SCREEN_TYPES:
                    designs = [_candidate_row(node, 1)]
                elif node.get("type") == "CANVAS":
                    designs = [
                        _candidate_row(child, index)
                        for index, child in enumerate(_top_screens(node), 1)
                    ]
                else:
                    designs = [_candidate_row(node, 1)]
                return {
                    "status": "success",
                    "platform": "figma",
                    "project_name": payload.get("name") or "",
                    "total_designs": len(designs),
                    "designs": designs,
                }
            payload = await _get_json(
                client,
                f"/v1/files/{params['file_key']}",
                {"depth": "2"},
            )
            document = payload.get("document") or {}
            designs = []
            index = 1
            for page in document.get("children") or []:
                if not isinstance(page, dict):
                    continue
                for child in _top_screens(page):
                    designs.append(_candidate_row(child, index))
                    index += 1
            return {
                "status": "success",
                "platform": "figma",
                "project_name": payload.get("name") or "",
                "total_designs": len(designs),
                "designs": designs,
            }
    except Exception as error:
        return _http_error(error)


async def _load_frame(
    client: httpx.AsyncClient,
    file_key: str,
    node_id: str,
) -> tuple[dict[str, Any], str]:
    if node_id:
        payload = await _get_json(
            client,
            f"/v1/files/{file_key}/nodes",
            {"ids": node_id},
        )
        entry = (payload.get("nodes") or {}).get(node_id) or {}
        node = entry.get("document") if isinstance(entry, dict) else None
        if not isinstance(node, dict):
            raise ValueError("找不到指定的 Figma 节点")
        return node, str(payload.get("name") or node.get("name") or "")
    payload = await _get_json(client, f"/v1/files/{file_key}", {"depth": "2"})
    document = payload.get("document") or {}
    screens = []
    for page in document.get("children") or []:
        if isinstance(page, dict):
            screens.extend(_top_screens(page))
    if len(screens) != 1:
        raise ValueError("缺少 image_id；请先确认设计范围")
    return screens[0], str(payload.get("name") or "")


async def get_detail(
    url: str,
    image_id: str = "",
    output_file: str = "",
) -> dict[str, Any]:
    early = _config_error()
    if early:
        return early
    try:
        params = parse_figma_url(url)
        selected_id = (image_id or params["node_id"]).strip()
        if image_id and params["node_id"] and node_id_mismatch(image_id, params["node_id"]):
            raise ValueError("image_id 与 Figma 链接中的 node-id 不一致")
        token = token_from_config()
        async with httpx.AsyncClient(base_url=API_BASE, timeout=120, headers=headers(token)) as client:
            frame, file_name = await _load_frame(client, params["file_key"], selected_id)
            if not selected_id:
                selected_id = str(frame.get("id") or "")
            document = document_from_figma(frame)
            preview_url = ""
            rendered = await _get_json(
                client,
                f"/v1/images/{params['file_key']}",
                {"ids": selected_id, "format": "png", "scale": "1"},
            )
            preview_url = str((rendered.get("images") or {}).get(selected_id) or "")
        result: dict[str, Any] = {
            "status": "success",
            "platform": "figma",
            "message": "已读取 Figma 节点结构",
            "source": {
                "url": url,
                "project_id": params["file_key"],
                "team_id": None,
                "design_id": selected_id,
                "name": document.get("name") or file_name,
                "structure_source": "figma",
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
        return {"status": "invalid_input", "platform": "figma", "message": str(error)}
    except Exception as error:
        return _http_error(error)


async def read(
    url: str,
    image_id: str = "",
    output_file: str = "",
) -> dict[str, Any]:
    """项目/文件链接返回候选屏；带 image_id 或 node-id 返回统一 schema。"""
    try:
        params = parse_figma_url(url)
    except ValueError as error:
        return {"status": "invalid_input", "platform": "figma", "message": str(error)}
    if image_id.strip() or params["node_id"]:
        return await get_detail(url, image_id, output_file)
    return await get_candidates(url)


def node_id_mismatch(left: str, right: str) -> bool:
    return left.replace("-", ":", 1) != right.replace("-", ":", 1)


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
        status = failures[0].get("status", "api_error")
    return {
        "status": status,
        "platform": "figma",
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
    early = _config_error()
    if early:
        return early
    try:
        params = parse_figma_url(url)
        selected_id = (image_id or params["node_id"]).strip()
        token = token_from_config()
        target_dir = prepare_output_dir(output_dir)
        async with httpx.AsyncClient(base_url=API_BASE, timeout=120, headers=headers(token)) as client:
            frame, _ = await _load_frame(client, params["file_key"], selected_id)
            nodes = figma_image_slices(frame)
            if not nodes:
                return {
                    "status": "success",
                    "platform": "figma",
                    "saved_count": 0,
                    "assets": [],
                }
            ids = [str(node.get("id") or "") for node in nodes]
            rendered = await _get_json(
                client,
                f"/v1/images/{params['file_key']}",
                {"ids": ",".join(ids), "format": "png", "scale": "1"},
            )
            urls = rendered.get("images") or {}
        saved = []
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            for node in nodes:
                node_id = str(node.get("id") or "")
                image_url = urls.get(node_id)
                if not image_url:
                    continue
                response = await client.get(image_url)
                response.raise_for_status()
                name = _file_stem(str(node.get("name") or "slice"), node_id)
                path = target_dir / f"{name}.png"
                atomic_write_bytes(path, response.content)
                saved.append({
                    "layer_id": node_id,
                    "layer_name": node.get("name"),
                    "local_path": str(path),
                    "source_url": image_url,
                })
        return {
            "status": "success",
            "platform": "figma",
            "saved_count": len(saved),
            "assets": saved,
        }
    except ValueError as error:
        return {"status": "invalid_input", "platform": "figma", "message": str(error)}
    except Exception as error:
        return _http_error(error)
