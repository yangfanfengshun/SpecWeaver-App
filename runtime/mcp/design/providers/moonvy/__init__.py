"""design 能力的 moonvy provider。

provider 契约：`PLATFORM` + `claims_url`。MCP 工具面是 check_auth / read；
candidates、detail、下载是 collect 调用的内部实现，不对外暴露。
"""
from __future__ import annotations

from design.providers.moonvy.parse import claims_url

PLATFORM = "moonvy"

from design.providers.moonvy.impl import (  # noqa: E402
    check_auth,
    download_previews,
    download_slices,
    get_candidates,
    get_detail,
    read,
)
