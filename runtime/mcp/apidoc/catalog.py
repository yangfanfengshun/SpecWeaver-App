"""把 apidoc 名单折成给人看的 markdown。"""
from __future__ import annotations

from typing import Any

from apidoc.schema import EMPTY_FOLDER_MESSAGE


def render_catalog(data: dict[str, Any], source_url: str = "") -> str:
    location = data.get("location") if isinstance(data.get("location"), dict) else {}
    items = data.get("items") if isinstance(data.get("items"), list) else []
    lines = ["# API 目录", ""]
    url = source_url or str(data.get("source_url") or "")
    if url:
        lines.append(f"- 来源：{url}")
    project_id = location.get("project_id")
    if project_id:
        lines.append(f"- 项目：{project_id}")
    folder_id = location.get("folder_id")
    if folder_id:
        folder_name = str(location.get("folder_name") or "").strip()
        suffix = f" {folder_name}" if folder_name else ""
        lines.append(f"- 目录：{folder_id}{suffix}")
    lines.append("")
    if not items:
        lines.append(EMPTY_FOLDER_MESSAGE)
        lines.append("")
        return "\n".join(lines)
    lines.extend(
        [
            "| ID | 方法 | 路径 | 名称 |",
            "| --- | --- | --- | --- |",
        ]
    )
    for item in items:
        if not isinstance(item, dict):
            continue
        lines.append(
            "| {api_id} | {method} | {path} | {name} |".format(
                api_id=item.get("api_id") or "",
                method=item.get("method") or "",
                path=item.get("path") or "",
                name=item.get("name") or "",
            )
        )
    lines.append("")
    return "\n".join(lines)
