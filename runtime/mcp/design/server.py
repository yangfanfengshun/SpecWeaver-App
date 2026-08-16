#!/usr/bin/env python3
# 依赖统一声明在 runtime/pyproject.toml，版本由 uv.lock 钉死；
# 由 scripts/run-mcp.sh 通过 `uv run --project` 提供，不要在这里再写一份。
"""design 能力入口：FastMCP 工具面。

公共部分不点名任何 provider：靠扫 `providers/` 目录发现，靠 `claims_url`
认领链接。加 / 拿掉一个平台，这里零改动。预览和切图落盘不是 MCP 工具，
由 requirement_collect 调 provider 内部函数完成。
"""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capability import discover_providers, resolve_provider


mcp = FastMCP("SpecWeaver Design")


def _provider(url: str = ""):
    resolved = resolve_provider(discover_providers("design"), url)
    return resolved[1] if resolved else None


def _no_provider(url: str = "") -> dict[str, str]:
    return {
        "status": "no_provider",
        "message": (
            f"design 能力当前没有 provider 认领该链接：{url}"
            if url
            else "design 能力当前没有可用的 provider"
        ),
    }


@mcp.tool()
async def design_check_auth(url: str = "") -> dict[str, str]:
    """检查设计稿平台认证；提供项目链接时同时检查项目权限。"""
    provider = _provider(url)
    if provider is None:
        return _no_provider(url)
    return await provider.check_auth(url)


@mcp.tool()
async def design_read(
    url: str,
    image_id: str = "",
    output_file: str = "",
) -> dict[str, Any]:
    """读取设计稿。项目/文件链接返回候选屏；带 image_id 或单屏链接返回统一 schema。"""
    provider = _provider(url)
    if provider is None:
        return _no_provider(url)
    return await provider.read(url, image_id, output_file)


if __name__ == "__main__":
    mcp.run(transport="stdio")
