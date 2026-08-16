"""Tower 任务原始缓存（`~/.specweaver/cache/tower/<key>/`）的读写。

纯文件系统操作：算缓存路径、把已经解析好的 `data` 写成
`tower-raw.md` + `tower-metadata.json`、再读回来给编排侧用。
不发请求，不依赖 `client.py` 的会话状态。缓存目录与文件名带 tower 是
有意的——这是 provider 的私有缓存，不是交给下游的项目产物。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse

from common import atomic_write_bytes, atomic_write_text
from requirement.providers.tower.parsing import format_todo


def tower_cache_metadata(data: dict) -> dict:
    return {
        # v3：external_sources 的 key 从平台名（lanhu/eolink）换成能力名
        # （design/apidoc）。只认当前版本：旧缓存若照读，消费端按新 key 取值
        # 会拿到空列表——不报错但候选全丢，必须逼一次重新读取。
        "schema_version": 3,
        "title": data.get("title") or "未提供",
        "url": data.get("url") or "",
        "todo_id": data.get("todo_id") or "未提供",
        "task_type": data.get("task_type", "requirement"),
        "comment_count": len(data.get("comments", [])),
        "comment_metadata_incomplete": any(
            not comment.get("id") or not comment.get("created_at")
            for comment in data.get("comments", [])
        ),
        "sub_todo_count": len(data.get("sub_todos", [])),
        "attachments": data.get("attachments", []),
        "attachment_occurrences": data.get("attachment_occurrences", []),
        "external_sources": data.get("external_sources", {}),
        "stream_count": data.get("stream_count", 0),
    }


def tower_cache_key(data: dict, url: str) -> str:
    path_parts = [part for part in urlparse(url).path.split("/") if part]
    try:
        todo_index = path_parts.index("todos")
        raw = path_parts[todo_index + 1]
    except (ValueError, IndexError):
        raw = str(data.get("todo_id") or "unknown")
    value = re.sub(r"[^0-9A-Za-z._-]+", "-", raw).strip("-")
    return value or "unknown"


def tower_cache_file(data: dict, url: str) -> Path:
    home = Path(
        os.getenv("SPECWEAVER_HOME", Path.home() / ".specweaver")
    ).expanduser()
    return home / "cache" / "tower" / tower_cache_key(data, url) / "tower-raw.md"


def tower_metadata_file(data: dict, url: str) -> Path:
    return tower_cache_file(data, url).with_name("tower-metadata.json")


def tower_image_cache_dir(data: dict, url: str) -> Path:
    return tower_cache_file(data, url).with_name("images")


def write_cached_image(path: Path, content: bytes) -> None:
    atomic_write_bytes(path, content)


def write_tower_raw(
    data: dict,
    url: str,
    image_cache: dict | None = None,
) -> Path:
    cache_file = atomic_write_text(tower_cache_file(data, url), format_todo(data))
    metadata = tower_cache_metadata(data)
    metadata["image_cache"] = image_cache or {
        "status": "not_cached",
        "output_dir": str(tower_image_cache_dir(data, url)),
        "source_count": sum(
            attachment.get("kind") == "image"
            for attachment in data.get("attachments", [])
        ),
        "saved_count": 0,
        "images": [],
        "occurrences": [],
        "failures": [],
    }
    atomic_write_text(
        tower_metadata_file(data, url),
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
    )
    return cache_file


def read_cached_tower_data(url: str) -> tuple[dict, Path]:
    cache_file = tower_cache_file({}, url)
    if not cache_file.is_file():
        raise FileNotFoundError(
            "Tower 原始缓存不存在；请先调用 requirement_read_todo"
        )
    if not cache_file.read_text(encoding="utf-8").strip():
        raise ValueError("tower-raw.md 是空文件")
    metadata_file = tower_metadata_file({}, url)
    if not metadata_file.is_file():
        raise ValueError("Tower 缓存缺少 tower-metadata.json")
    try:
        data = json.loads(metadata_file.read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError("tower-metadata.json 损坏") from error
    if data.get("schema_version") != 3:
        raise ValueError(
            "tower-metadata.json 的版本不受支持；"
            "请重新调用 requirement_read_todo 刷新缓存"
        )
    if data.get("url") != url:
        raise ValueError("Tower 缓存与请求的 Tower 链接不一致")
    return data, cache_file


def tower_read_summary(
    data: dict,
    cache_file: Path,
    image_cache: dict,
) -> dict:
    unresolved = []
    if data.get("comment_metadata_incomplete") or any(
        not comment.get("id") or not comment.get("created_at")
        for comment in data.get("comments", [])
    ):
        unresolved.append("部分评论缺少 Tower 可解析的稳定 ID 或时间，已标记为“未提供”")
    if image_cache["failures"]:
        unresolved.append(
            f"{len(image_cache['failures'])} 张 Tower 图片缓存失败，"
            "分析时不得忽略缺失的图片证据"
        )
    status = "success" if not image_cache["failures"] else "partial"
    return {
        "status": status,
        "platform": "tower",
        "todo_id": data.get("todo_id") or "未提供",
        "task_title": data.get("title") or "未提供",
        "task_type": data.get("task_type", "requirement"),
        "cache_file": str(cache_file),
        "metadata_file": str(cache_file.with_name("tower-metadata.json")),
        "comment_count": data.get(
            "comment_count",
            len(data.get("comments", [])),
        ),
        "attachment_count": len(data.get("attachment_occurrences", [])),
        "image_count": image_cache["source_count"],
        "cached_image_count": image_cache["saved_count"],
        "image_cache_dir": image_cache["output_dir"],
        "image_paths": [
            item["path"]
            for item in image_cache["images"]
            if not item.get("duplicate")
        ],
        "image_failures": image_cache["failures"],
        "sub_todo_count": data.get(
            "sub_todo_count",
            len(data.get("sub_todos", [])),
        ),
        "external_sources": data.get("external_sources", {}),
        "stream_count": data.get("stream_count", 0),
        "read_complete": True,
        "unresolved": unresolved,
    }
