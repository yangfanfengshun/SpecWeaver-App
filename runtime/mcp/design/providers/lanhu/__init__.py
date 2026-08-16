"""design 能力的 lanhu provider。

provider 契约（`capability.py` 扫目录时校验）：`PLATFORM` + `claims_url`。
MCP 工具面是 check_auth / read；candidates、detail、下载由 requirement
编排层直接调内部实现。加平台就是加目录，公共层只卡出口 schema。
"""
from __future__ import annotations

from urllib.parse import urlparse

PLATFORM = "lanhu"


def claims_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "lanhuapp.com" or host.endswith(".lanhuapp.com")


from design.providers.lanhu.impl import (  # noqa: E402
    check_auth,
    download_previews,
    download_slices,
    get_candidates,
    get_detail,
    read,
)
