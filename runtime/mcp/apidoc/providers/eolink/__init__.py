"""apidoc 能力的 eolink provider。

provider 契约（`capability.py` 扫目录时校验）：`PLATFORM` + `claims_url`。
其余是 apidoc 能力约定的统一接口，`apidoc/server.py` 与 requirement 的
编排层只通过这里暴露的名字调用；Apifox 暴露同一组名字。
"""
from __future__ import annotations

from urllib.parse import urlparse

PLATFORM = "eolink"


def claims_url(url: str) -> bool:
    """Eolink 多为私有化部署，域名里通常带 eolink；部署在别的域名时靠链接
    形状（fragment 里带 projectID 且指向 api 页面）兜底——与旧版链接分类
    的判断保持一致。"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    fragment = parsed.fragment.lower()
    eolink_shape = "projectid=" in fragment and "api" in fragment
    return "eolink" in host or eolink_shape


from apidoc.providers.eolink.client import (  # noqa: E402
    SUCCESS_CODE,
    auth_error,
    check_auth,
    current_settings,
    parse_url,
    read,
)
