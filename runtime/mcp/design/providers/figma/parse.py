from __future__ import annotations

from urllib.parse import parse_qs, urlparse

_CLAIM_PATHS = ("/design/", "/file/", "/proto/")


def claims_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in {"figma.com", "www.figma.com"}:
        return False
    path = parsed.path or ""
    if "/community/" in path or path.startswith("/board") or "/figjam" in path:
        return False
    return any(part in path for part in _CLAIM_PATHS)


def node_id_from_query(value: str) -> str:
    """URL 里 `58-1310` 转回 API 用的 `58:1310`。"""
    text = (value or "").strip()
    if not text:
        return ""
    return text.replace("-", ":", 1)


def parse_figma_url(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    if not claims_url(url):
        raise ValueError("请提供 Figma 设计链接（design / file / proto）")
    parts = [part for part in (parsed.path or "").split("/") if part]
    file_key = ""
    for index, part in enumerate(parts):
        if part in {"design", "file", "proto"} and index + 1 < len(parts):
            file_key = parts[index + 1]
            break
    if not file_key:
        raise ValueError("Figma 链接缺少文件 key")
    query = parse_qs(parsed.query)
    node_id = node_id_from_query((query.get("node-id") or [""])[0])
    return {"file_key": file_key, "node_id": node_id}
