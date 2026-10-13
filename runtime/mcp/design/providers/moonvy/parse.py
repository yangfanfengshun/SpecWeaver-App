from __future__ import annotations

from urllib.parse import urlparse

_HOSTS = {"moonvy.com", "www.moonvy.com"}


def claims_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in _HOSTS:
        return False
    parts = [part for part in (parsed.path or "").split("/") if part]
    return len(parts) >= 2 and parts[0] == "project" and all(parts[1:])


def parse_moonvy_url(url: str) -> dict[str, str]:
    if not claims_url(url):
        raise ValueError("请提供月维设计链接（moonvy.com/project/…）")
    parsed = urlparse(url)
    parts = [part for part in (parsed.path or "").split("/") if part]
    ids = parts[1:]
    if len(ids) > 3:
        raise ValueError("月维链接层级过多")
    return {
        "project_id": ids[0],
        "dir_id": ids[1] if len(ids) > 1 else "",
        "file_id": ids[2] if len(ids) > 2 else "",
    }
