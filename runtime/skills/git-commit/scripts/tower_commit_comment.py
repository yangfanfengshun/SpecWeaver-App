#!/usr/bin/env python3
"""生成同步到 Tower 的开发信息评论。stdout 只放成品，不要改写。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def find_git_root(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def git(cwd: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def origin_to_web_url(origin: str) -> str | None:
    """把 git remote 收成可点的 https 仓库地址，去掉 .git 和任何账号口令。"""
    value = origin.strip()
    if not value:
        return None

    if "://" in value:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https", "ssh", "git"}:
            return None
        host = parsed.hostname
        path = parsed.path
    elif ":" in value:
        remainder = value.split("@", 1)[-1]
        host, _, path = remainder.partition(":")
    else:
        return None

    if not host or not path:
        return None
    path = path.strip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    path = path.strip("/")
    if not path:
        return None
    return urlunparse(("https", host, f"/{path}", "", "", ""))


def is_github(web_url: str) -> bool:
    return urlparse(web_url).hostname == "github.com"


def branch_url(web_url: str, branch: str) -> str:
    encoded = quote(branch, safe="/")
    if is_github(web_url):
        return f"{web_url}/tree/{encoded}"
    return f"{web_url}/-/tree/{encoded}"


def md_link(label: str, url: str | None) -> str:
    if not url:
        return label
    escaped = label.replace("\\", "\\\\").replace("]", "\\]")
    return f"[{escaped}]({url})"


def format_commit_time(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime(TIME_FORMAT)


def render_comment(
    project: str,
    branch: str,
    head: str,
    origin: str | None,
    now: datetime | None = None,
) -> str:
    web_url = origin_to_web_url(origin) if origin else None
    branch_href = branch_url(web_url, branch) if web_url and branch != "HEAD" else None
    return "\n".join(
        [
            f"项目：{md_link(project, web_url)}",
            f"开发分支：{md_link(branch, branch_href)}",
            f"提交 HEAD：{head}",
            f"提交时间：{format_commit_time(now)}",
            f"SpecWeaver-Commit: {project}/{branch}@{head}",
        ]
    )


def build_comment(cwd: Path) -> str:
    root = find_git_root(cwd)
    if root is None:
        raise ValueError("当前目录不在 Git 仓库里")
    head = git(root, "rev-parse", "HEAD")
    if not head:
        raise ValueError("当前仓库没有提交")
    branch = git(root, "branch", "--show-current") or "HEAD"
    origin = git(root, "remote", "get-url", "origin")
    return render_comment(root.name, branch, head, origin)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 Tower 开发信息评论")
    parser.add_argument("--cwd", type=Path, default=Path("."))
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        print(build_comment(arguments.cwd))
        return 0
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
