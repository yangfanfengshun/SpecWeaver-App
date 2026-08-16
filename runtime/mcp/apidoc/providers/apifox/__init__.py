"""apidoc 能力的 apifox provider。

provider 契约（`capability.py` 扫目录时校验）：`PLATFORM` + `claims_url`。
其余与 eolink 暴露同一组名字：`check_auth` / `read` / `parse_url` /
`auth_error` / `current_settings`。
"""
from __future__ import annotations

from urllib.parse import urlparse

PLATFORM = "apifox"


def claims_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return "apifox.com" in host or host.endswith("apifox.cn")


from apidoc.providers.apifox.client import (  # noqa: E402
    auth_error,
    check_auth,
    current_settings,
    parse_url,
    read,
)
