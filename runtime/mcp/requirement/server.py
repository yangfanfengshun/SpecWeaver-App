#!/usr/bin/env python3
# 依赖统一声明在 runtime/pyproject.toml，版本由 uv.lock 钉死；
# 由 scripts/run-mcp.sh 通过 `uv run --project` 提供，不要在这里再写一份。
"""requirement 能力入口：FastMCP 工具面。

工具经 capability 路由到当前认领链接的 provider；编排（候选 / 完整收集）
走 core.py。本文件不点名任何平台实现。
"""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capability import discover_providers, resolve_provider
from requirement import core


mcp = FastMCP("SpecWeaver Requirement")


def _provider(url: str = ""):
    resolved = resolve_provider(discover_providers("requirement"), url)
    return resolved[1] if resolved else None


def _no_provider(url: str = "") -> dict[str, str]:
    return core.no_provider_result("requirement", url)


@mcp.tool()
async def requirement_check_auth(url: str = "") -> dict[str, str]:
    """检查需求平台认证；提供任务链接时同时检查任务访问权限。"""
    provider = _provider(url)
    if provider is None:
        return _no_provider(url)
    return await provider.check_auth(url)


@mcp.tool()
async def requirement_read_todo(url: str) -> dict[str, Any]:
    """读取需求任务原文与图片到用户缓存，只返回路径、数量和来源摘要。"""
    provider = _provider(url)
    if provider is None:
        return _no_provider(url)
    return await provider.read_todo(url)


@mcp.tool()
async def requirement_list_member_todos(url: str) -> dict[str, Any]:
    """读取成员任务清单最小集，不读正文、评论或附件，也不写入缓存。"""
    provider = _provider(url)
    if provider is None:
        return _no_provider(url)
    list_fn = getattr(provider, "list_member_todos", None)
    if list_fn is None:
        return {
            "status": "not_applicable",
            "platform": getattr(provider, "PLATFORM", ""),
            "message": "当前平台不支持成员任务清单",
        }
    return await list_fn(url)


@mcp.tool()
async def requirement_download_images(url: str, output_dir: str) -> dict[str, Any]:
    """下载任务正文和全部评论中的附件图片，并返回本地文件与来源映射。"""
    provider = _provider(url)
    if provider is None:
        return _no_provider(url)
    return await provider.download_images(url, output_dir)


@mcp.tool()
async def requirement_add_comment(
    url: str,
    content: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """向需求任务发布 Markdown 评论；默认只预览，dry_run=false 才真正发布。"""
    provider = _provider(url)
    if provider is None:
        return _no_provider(url)
    return await provider.add_comment(url, content, dry_run)


@mcp.tool()
async def requirement_get_manifest(
    url: str,
    output_dir: str,
) -> dict[str, Any]:
    """定位已收集需求在用户缓存中的清单，不返回清单正文。"""
    return await core.get_manifest(url, output_dir)


@mcp.tool()
async def requirement_collect(
    url: str,
    output_dir: str = "",
    confirmed_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """确定性编排任务、设计稿与 API 文档来源；未确认范围时只返回候选。"""
    return await core.collect(url, output_dir, confirmed_scope)


if __name__ == "__main__":
    mcp.run(transport="stdio")
