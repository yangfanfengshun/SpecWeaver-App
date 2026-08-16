#!/usr/bin/env python3
# 依赖统一声明在 runtime/pyproject.toml，版本由 uv.lock 钉死；
# 由 scripts/run-mcp.sh 通过 `uv run --project` 提供，不要在这里再写一份。
"""apidoc 能力入口：FastMCP 工具面。

公共部分不点名任何 provider：靠扫 `providers/` 目录发现，靠 `claims_url`
认领链接。加 / 拿掉一个平台，这里零改动。
"""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capability import discover_providers, resolve_provider


mcp = FastMCP("SpecWeaver API Doc")


def _provider(url: str = ""):
    providers = discover_providers("apidoc")
    resolved = resolve_provider(providers, url)
    if resolved:
        return resolved[1]
    if not url:
        # 裸 ID / 探活没链接：Eolink 是主力，claims_url 认领规则不动。
        eolink = providers.get("eolink")
        if eolink is not None:
            return eolink
    return None


def _no_provider(url: str = "") -> dict[str, str]:
    return {
        "status": "no_provider",
        "message": (
            f"apidoc 能力当前没有 provider 认领该链接：{url}"
            if url
            else "apidoc 能力当前没有可用的 provider"
        ),
    }


@mcp.tool()
async def apidoc_auth(url: str = "") -> dict[str, str]:
    """检查 API 文档平台认证，不返回账号或密码。url 只用来选 provider。"""
    provider = _provider(url)
    if provider is None:
        return _no_provider(url)
    return await provider.check_auth()


@mcp.tool()
async def apidoc_read(url: str = "", api_id: str = "") -> dict[str, Any]:
    """读取 API 文档。目录/项目链接返回名单；带 api_id 或单接口链接返回详情。"""
    provider = _provider(url)
    if provider is None:
        return _no_provider(url)
    return await provider.read(url, api_id=api_id or None)


if __name__ == "__main__":
    mcp.run(transport="stdio")
