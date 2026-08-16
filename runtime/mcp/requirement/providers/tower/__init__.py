"""requirement 能力的 tower provider。

provider 契约（`capability.py` 扫目录时校验）：`PLATFORM` + `claims_url`。
其余是 requirement 能力约定的统一接口，`requirement/server.py` 与
`requirement/core.py` 只通过这里暴露的名字调用，不深入包内文件——
将来接禅道时，新 provider 暴露同一组名字即可。
"""
from __future__ import annotations

from urllib.parse import urlparse

PLATFORM = "tower"

_TASK_HOSTS = {"tower.im", "www.tower.im"}


def claims_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in _TASK_HOSTS


from requirement.providers.tower.cache import (  # noqa: E402
    read_cached_tower_data,
    read_cached_tower_data as read_cached_data,
    tower_cache_key as cache_key,
    tower_read_summary as read_summary,
    write_tower_raw,
)
from requirement.providers.tower.client import (  # noqa: E402
    TowerSessionError,
    add_comment,
    check_auth,
    download_images,
    load_todo_data,
    read_todo,
    tower_cookie,
)
from requirement.providers.tower.parsing import (  # noqa: E402
    format_todo,
    parse_ordered_content,
    parse_tags,
    parse_todo,
    task_type_from_tags,
    validate_tower_todo_url as validate_todo_url,
)


async def request(*args, **kwargs):
    """晚绑定到 client.request：测试 patch 实现模块时，编排层经本包调用也能命中。"""
    from requirement.providers.tower import client as _client
    return await _client.request(*args, **kwargs)
