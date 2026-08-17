# 依赖统一声明在 runtime/pyproject.toml，版本由 uv.lock 钉死；
# 由 scripts/run-mcp.sh 通过 `uv run --project` 提供，不要在这里再写一份。
"""requirement 能力的 tower provider：会话/HTTP 层 + 下载缓存编排 + 工具实现。

纯 HTML/文本解析在 `parsing.py`，缓存文件读写在 `cache.py`（都是无网络
依赖的纯函数）。这里保留的是需要 `request`/`get_client` 这套会话状态的
部分：会话续期、下载编排、评论发布，以及各工具的平台实现（由
`requirement/server.py` 的能力工具经 provider 路由调进来，本模块不再
自带 FastMCP 实例）。
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
import hashlib
from pathlib import Path
import re
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import uuid4

from bs4 import BeautifulSoup
import httpx

from capability import classify_links
from common import (
    DOWNLOAD_CONCURRENCY,
    IMAGE_EXTENSIONS,
    UnsafePathError,
    http_error_result,
    manual_cookie_hint,
    prepare_output_dir,
    read_config,
    update_config_atomic,
)
from session import fingerprint, with_session
from requirement.providers.tower.cache import (
    read_cached_tower_data,
    tower_cache_key,
    tower_image_cache_dir,
    tower_read_summary,
    write_cached_image,
    write_tower_raw,
)
from requirement.providers.tower.parsing import (
    TOWER_BASE,
    MEMBER_LIST_STATUS,
    collect_external_links,
    parse_comment_context,
    parse_member_todo_items,
    parse_member_todo_url,
    parse_stream_response,
    parse_todo,
    build_attachment_occurrences,
    build_image_occurrences,
    text_to_comment_html,
    unique_attachments,
    validate_tower_member_todo_url,
    validate_tower_todo_url,
)
from requirement.providers.tower.auth import (
    TowerLoginError,
    cookies_from_header,
    is_login_response,
    login_tower,
)


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

_client: httpx.AsyncClient | None = None
_client_cookie_fingerprint = ""
_runtime_cookie = ""
_login_lock: asyncio.Lock | None = None
_login_lock_loop: asyncio.AbstractEventLoop | None = None


class TowerSessionError(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def tower_cookie() -> str:
    return _runtime_cookie or read_config()["TOWER_COOKIE"]


def login_lock() -> asyncio.Lock:
    """返回绑定到当前事件循环的登录锁。

    `asyncio.Lock` 首次 `acquire` 时会绑定到当时的 running loop；模块级单例如果
    跨多个 `asyncio.run()`（例如同进程内先后起了两个新 loop）复用，第二次会抛
    `RuntimeError: ... is bound to a different event loop`。这里按「当前 loop」
    做惰性重建：同一个 loop 内始终拿到同一把锁（互斥有效），loop 变了就换一把新的。
    """
    global _login_lock, _login_lock_loop
    running_loop = asyncio.get_running_loop()
    if _login_lock is None or _login_lock_loop is not running_loop:
        _login_lock = asyncio.Lock()
        _login_lock_loop = running_loop
    return _login_lock


async def get_client() -> httpx.AsyncClient:
    global _client, _client_cookie_fingerprint
    configured_cookie = tower_cookie()
    cookie_fingerprint = fingerprint(configured_cookie)
    if _client is None or _client_cookie_fingerprint != cookie_fingerprint:
        if _client is not None:
            await _client.aclose()
        _client = httpx.AsyncClient(
            timeout=30,
            follow_redirects=False,
            cookies=cookies_from_header(configured_cookie),
        )
        _client_cookie_fingerprint = cookie_fingerprint
    return _client


async def refresh_tower_session(
    validation_url: str,
    stale_fingerprint: str,
) -> None:
    global _runtime_cookie
    async with login_lock():
        current_cookie = tower_cookie()
        if fingerprint(current_cookie) != stale_fingerprint:
            return
        config = read_config()
        email = config["TOWER_EMAIL"]
        password = config["TOWER_PASSWORD"]
        if not email or not password:
            raise TowerSessionError(
                "auth_expired",
                "Tower Cookie 已过期，且未配置邮箱密码；"
                "请在 SpecWeaver 设置页重新配置 Tower",
            )
        try:
            renewed_cookie = await asyncio.to_thread(
                login_tower,
                email,
                password,
                validation_url=validation_url,
            )
        except TowerLoginError as error:
            if error.kind == "credentials":
                status = "auth_expired"
                message = "Tower 邮箱或密码已失效"
            elif error.kind == "verification":
                status = "verification_required"
                message = "Tower 要求验证码或二次验证"
            elif error.kind == "network":
                status = "network_error"
                message = "Tower 登录网络异常"
            else:
                status = "compatibility_error"
                message = "Tower 网页登录流程暂时不可用"
            raise TowerSessionError(
                status,
                f"{message}；{manual_cookie_hint('tower', 'TOWER_COOKIE')}",
            ) from error
        update_config_atomic({"TOWER_COOKIE": renewed_cookie})
        _runtime_cookie = renewed_cookie
        await get_client()


async def request_once(
    url: str,
    *,
    extra_headers: dict[str, str] | None = None,
) -> httpx.Response:
    current = urljoin(TOWER_BASE, url)
    client = await get_client()
    for _ in range(6):
        parsed = urlparse(current)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {
            "tower.im",
            "www.tower.im",
            "attachments.tower.im",
            "tower3-downloads.tower.im",
        }:
            raise ValueError("Tower 请求或重定向目标不是受信任的 HTTPS 域名")
        response = await client.get(
            current,
            headers={**HEADERS, **(extra_headers or {})},
            follow_redirects=False,
        )
        if not response.is_redirect:
            response.raise_for_status()
            return response
        location = response.headers.get("location")
        if not location:
            break
        current = urljoin(str(response.url), location)
    raise RuntimeError(f"Tower 资源重定向次数过多: {url}")


async def request(
    url: str,
    *,
    extra_headers: dict[str, str] | None = None,
) -> httpx.Response:
    async def operation(_token: str) -> httpx.Response:
        return await request_once(url, extra_headers=extra_headers)

    def raise_still_expired(error: BaseException | None) -> None:
        raise TowerSessionError(
            "auth_expired",
            "Tower 自动续期后仍需要登录；"
            + manual_cookie_hint("tower", "TOWER_COOKIE"),
        ) from error

    return await with_session(
        operation,
        get_token=tower_cookie,
        refresh=lambda stale_fingerprint: refresh_tower_session(
            urljoin(TOWER_BASE, url), stale_fingerprint
        ),
        is_expired_result=is_login_page,
        raise_still_expired=raise_still_expired,
    )


def is_login_page(response: httpx.Response) -> bool:
    return is_login_response(response)


async def post_comment(
    meta: dict[str, str],
    comment_html: str,
    referer: str,
) -> httpx.Response:
    action = meta["action"] or (
        f"/projects/{meta['project_guid']}/todos/{meta['todo_guid']}/comments?is_html=1"
    )
    response = await (await get_client()).post(
        urljoin(TOWER_BASE, action),
        data={
            "conn_guid": str(uuid4()),
            "utf8": "✓",
            "comment[content]": comment_html,
            "attach_guids": "",
        },
        headers={
            **HEADERS,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-CSRF-Token": meta["csrf_token"],
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
            "Origin": TOWER_BASE,
        },
        follow_redirects=False,
    )
    if response.status_code in {401, 403} or response.is_redirect:
        # 抛 TowerSessionError 而不是裸 RuntimeError，`submit_comment` 的
        # `with_session` 才认得出这是「该续期」而不是「该放弃」。
        raise TowerSessionError("auth_expired", "Tower 登录态或 CSRF 校验已失效")
    if not 200 <= response.status_code < 300:
        message = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)[:200]
        raise RuntimeError(f"Tower 返回 {response.status_code}: {message or '未知错误'}")
    return response


def is_session_expired_error(error: BaseException) -> bool:
    return isinstance(error, TowerSessionError) and error.status == "auth_expired"


async def fetch_comment_context(url: str) -> tuple[dict[str, str], str]:
    """抓任务页并解析出发评论需要的 CSRF 上下文与标题（单次，不含续期）。"""
    response = await request_once(url)
    if is_login_page(response):
        raise TowerSessionError("auth_expired", "Tower 登录态已失效")
    # lxml 解析是纯 CPU 计算，扔线程池跑，避免大页面卡住事件循环。
    return await asyncio.to_thread(parse_comment_context, response.text)


async def submit_comment(url: str, comment_html: str) -> httpx.Response:
    """发布评论，Cookie 过期时自动续期后重试一次。

    重试必须连「抓页面取 CSRF」一起重来：csrf_token 与 Cookie 同属一个会话，
    续期换掉 Cookie 之后，续期前抓到的 token 也一并失效，只重发 POST 必然再失败。
    """

    async def operation(_token: str) -> httpx.Response:
        meta, _title = await fetch_comment_context(url)
        return await post_comment(meta, comment_html, url)

    def raise_still_expired(error: BaseException | None) -> None:
        raise TowerSessionError(
            "auth_expired",
            "Tower 自动续期后仍需要登录；"
            + manual_cookie_hint("tower", "TOWER_COOKIE"),
        ) from error

    return await with_session(
        operation,
        get_token=tower_cookie,
        refresh=lambda stale_fingerprint: refresh_tower_session(
            urljoin(TOWER_BASE, url), stale_fingerprint
        ),
        is_expired_error=is_session_expired_error,
        raise_still_expired=raise_still_expired,
    )


async def expand_stream_fragments(soup: BeautifulSoup) -> int:
    visited: set[str] = set()
    stream_count = 0

    async def expand(container: BeautifulSoup) -> None:
        nonlocal stream_count
        while True:
            placeholder = next((
                item
                for item in container.select(
                    "[data-comment-streams-range][data-url]"
                )
                if not item.find_parent(class_="desc-content")
                and not item.find_parent(class_="comment-content")
            ), None)
            if placeholder is None:
                return
            source_url = urljoin(
                TOWER_BASE,
                str(placeholder.get("data-url") or ""),
            )
            if not source_url or source_url in visited:
                placeholder.decompose()
                continue
            visited.add(source_url)
            fragment = parse_stream_response(
                (await request(source_url)).text
            )
            stream_count += 1
            fragment_soup = BeautifulSoup(fragment, "lxml")
            await expand(fragment_soup)
            fragment_root = fragment_soup.body or fragment_soup
            for node in list(fragment_root.contents):
                placeholder.insert_before(node)
            placeholder.decompose()

    await expand(soup)
    return stream_count


async def load_member_todo_list(url: str) -> tuple[BeautifulSoup, int]:
    response = await request(url)
    if is_login_page(response):
        raise RuntimeError("Tower Cookie 已过期，请在 SpecWeaver 设置页重新配置 Tower 后重试")
    soup = await asyncio.to_thread(BeautifulSoup, response.text, "lxml")
    stream_count = await expand_stream_fragments(soup)
    return soup, stream_count


async def load_todo_data(url: str) -> dict:
    response = await request(url)
    if is_login_page(response):
        raise RuntimeError("Tower Cookie 已过期，请在 SpecWeaver 设置页重新配置 Tower 后重试")
    # 大页面的 lxml 解析和后面 parse_todo 里的 markdownify 转换都是纯 CPU
    # 计算、没有 IO，扔进线程池跑，不然会卡住 MCP 心跳（同进程里还有其他并发
    # 请求在等事件循环）。中间 expand_stream_fragments 需要一边 await 网络
    # 请求一边改 soup 树，天然离不开事件循环，留在原地。
    soup = await asyncio.to_thread(BeautifulSoup, response.text, "lxml")
    stream_count = await expand_stream_fragments(soup)
    data = await asyncio.to_thread(parse_todo, soup, url)
    if not data["title"]:
        raise RuntimeError("无法解析 Tower 任务")

    ordered_comments = []
    stable_positions: dict[str, int] = {}
    for comment in data["comments"]:
        comment_id = comment.get("id") or ""
        if comment_id and comment_id in stable_positions:
            ordered_comments[stable_positions[comment_id]] = comment
            continue
        if comment_id:
            stable_positions[comment_id] = len(ordered_comments)
        ordered_comments.append(comment)
    data["comments"] = ordered_comments
    data["image_occurrences"] = build_image_occurrences(data)
    data["attachment_occurrences"] = build_attachment_occurrences(data)
    data["attachments"] = unique_attachments(data["attachment_occurrences"])
    # 提取归 provider（collect_external_links），分类归能力声明（classify_links）：
    # 哪个域名属于哪类不在这里维护，加 Figma / 语雀不用回来改这行。
    # requirement 自己除外——指向别的 Tower 任务的链接已在提取时按自身域名滤掉。
    data["external_sources"] = classify_links(
        collect_external_links(data),
        exclude={"requirement"},
    )
    data["stream_count"] = stream_count
    return data


async def discover_extensionless_image_attachments(data: dict) -> list[dict]:
    failures = []
    occurrences = data.get("attachment_occurrences", [])
    for candidate_index, attachment in enumerate(data.get("attachments", []), 1):
        if attachment.get("kind") != "file" or attachment.get("media_type"):
            continue
        source_url = str(attachment["source_url"])
        try:
            response = await request(
                source_url,
                extra_headers={"Range": "bytes=0-0"},
            )
            content_type = (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
        except Exception as error:
            detail = tool_error(error)
            failures.append({
                "source_index": candidate_index,
                "source_url": source_url,
                "name": attachment.get("name") or "未提供",
                "status": detail.get("status", "download_error"),
                "error": (
                    "无法识别附件是否为图片: "
                    f"{detail.get('message') or str(error)}"
                ),
            })
            continue
        if content_type not in IMAGE_EXTENSIONS:
            continue
        attachment["kind"] = "image"
        attachment["media_type"] = content_type
        for occurrence in occurrences:
            if occurrence.get("source_url") == source_url:
                occurrence["kind"] = "image"
                occurrence["media_type"] = content_type
    return failures


def _classify_tower_download_error(error: BaseException) -> tuple[str, str]:
    if isinstance(error, TowerSessionError):
        return error.status, str(error)
    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        status = (
            "auth_expired" if code == 401
            else "forbidden" if code == 403
            else "not_found" if code == 404
            else "api_error"
        )
        return status, f"Tower 返回 HTTP {code}"
    if isinstance(error, httpx.HTTPError):
        return "network_error", str(error)
    return "download_error", str(error)


async def _fetch_tower_attachment(
    semaphore: asyncio.Semaphore, source_url: str
) -> httpx.Response:
    async with semaphore:
        return await request(source_url)


async def download_tower_image_attachments(
    data: dict,
    output_dir: Path,
    *,
    file_prefix: str,
) -> dict:
    target_dir = prepare_output_dir(str(output_dir))
    failures = await discover_extensionless_image_attachments(data)
    images = [
        attachment
        for attachment in data.get("attachments", [])
        if attachment.get("kind") == "image"
    ]
    downloaded = []
    hashes: dict[str, dict] = {}
    saved_names: set[str] = set()
    source_results: dict[str, dict] = {
        item["source_url"]: item
        for item in failures
    }

    def record_failure(
        source_index: int,
        attachment: dict,
        status: str,
        error: str,
    ) -> None:
        item = {
            "source_index": source_index,
            "source_url": attachment["source_url"],
            "name": attachment.get("name") or "未提供",
            "status": status,
            "error": error,
        }
        failures.append(item)
        source_results[attachment["source_url"]] = item

    # 网络请求并发拉取（信号量限流），哈希去重/落盘按原始顺序串行处理，
    # 保证文件命名与去重结果只取决于 images 顺序，与网络返回先后无关。
    semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
    responses = await asyncio.gather(
        *(
            _fetch_tower_attachment(semaphore, str(attachment["source_url"]))
            for attachment in images
        ),
        return_exceptions=True,
    )

    for source_index, (attachment, result) in enumerate(zip(images, responses), 1):
        source_url = str(attachment["source_url"])
        if isinstance(result, BaseException):
            status, message = _classify_tower_download_error(result)
            record_failure(source_index, attachment, status, message)
            continue
        response = result
        try:
            if is_login_page(response):
                record_failure(
                    source_index,
                    attachment,
                    "auth_expired",
                    "Tower Cookie 已过期",
                )
                continue
            content_type = (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            extension = IMAGE_EXTENSIONS.get(content_type)
            if not extension:
                raise ValueError(
                    f"不支持的图片类型: {content_type or '未知类型'}"
                )
            content_hash = hashlib.sha256(response.content).hexdigest()
            if content_hash in hashes:
                original = hashes[content_hash]
                item = {
                    "source_index": source_index,
                    "source_url": source_url,
                    "name": attachment.get("name") or "未提供",
                    "status": "success",
                    "file_name": original["file_name"],
                    "path": original["path"],
                    "content_type": content_type,
                    "sha256": content_hash,
                    "duplicate": True,
                    "duplicate_of": original["file_name"],
                }
                downloaded.append(item)
                source_results[source_url] = item
                continue
            file_name = f"{file_prefix}-{len(hashes) + 1:03d}{extension}"
            file_path = target_dir / file_name
            write_cached_image(file_path, response.content)
            item = {
                "source_index": source_index,
                "source_url": source_url,
                "name": attachment.get("name") or "未提供",
                "status": "success",
                "file_name": file_name,
                "path": str(file_path),
                "content_type": content_type,
                "bytes": len(response.content),
                "sha256": content_hash,
                "duplicate": False,
            }
            hashes[content_hash] = item
            saved_names.add(file_name)
            downloaded.append(item)
            source_results[source_url] = item
        except Exception as error:
            status, message = _classify_tower_download_error(error)
            record_failure(source_index, attachment, status, message)

    owned_pattern = re.compile(
        rf"{re.escape(file_prefix)}-\d{{3}}\.(gif|jpe?g|png|svg|webp)$",
        re.I,
    )
    for old_file in target_dir.iterdir():
        if (
            old_file.is_file()
            and owned_pattern.fullmatch(old_file.name)
            and old_file.name not in saved_names
        ):
            old_file.unlink()

    occurrences = []
    for occurrence in data.get("attachment_occurrences", []):
        if occurrence.get("kind") != "image":
            continue
        occurrences.append({
            **occurrence,
            **source_results.get(occurrence["source_url"], {
                "status": "not_downloaded",
                "error": "未找到对应的图片缓存结果",
            }),
        })

    status = "success" if not failures else "partial"
    if failures and not downloaded:
        failure_statuses = {item["status"] for item in failures}
        if len(failure_statuses) == 1:
            status = failure_statuses.pop()
    return {
        "status": status,
        "output_dir": str(target_dir),
        "source_count": len(images),
        "saved_count": len(hashes),
        "images": downloaded,
        "occurrences": occurrences,
        "failures": failures,
    }


async def cache_tower_images(data: dict, url: str) -> dict:
    target_dir = prepare_output_dir(str(tower_image_cache_dir(data, url)))
    target_dir.parent.chmod(0o700)
    target_dir.chmod(0o700)
    return await download_tower_image_attachments(
        data,
        target_dir,
        file_prefix="tower-image",
    )


def tool_error(error: Exception) -> dict[str, str]:
    if isinstance(error, UnsafePathError):
        return {
            "status": "invalid_output",
            "platform": "tower",
            "message": str(error),
        }
    if isinstance(error, TowerSessionError):
        return {
            "status": error.status,
            "platform": "tower",
            "message": str(error),
        }
    mapped = http_error_result(
        error,
        platform="tower",
        status_by_code={401: "auth_expired", 403: "forbidden", 404: "not_found"},
        default_status="api_error",
        network_prefix="Tower 网络请求失败: ",
    )
    if mapped:
        return mapped
    return {
        "status": "api_error",
        "platform": "tower",
        "message": str(error),
    }


async def check_auth(url: str = "") -> dict[str, str]:
    """检查 Tower Cookie；提供任务链接时同时检查任务访问权限。"""
    url = url or TOWER_BASE
    if not tower_cookie():
        return {"status": "missing_config", "platform": "tower", "message": "未设置 TOWER_COOKIE"}
    if url != TOWER_BASE:
        url_error = validate_tower_todo_url(url)
        if url_error:
            return {"status": "invalid_input", "platform": "tower", "message": url_error}
    try:
        response = await request(url)
    except TowerSessionError as error:
        return {
            "status": error.status,
            "platform": "tower",
            "message": str(error),
        }
    except httpx.HTTPStatusError as error:
        status = "auth_expired" if error.response.status_code == 401 else "forbidden" if error.response.status_code == 403 else "network_error"
        return {"status": status, "platform": "tower", "message": f"Tower 返回 HTTP {error.response.status_code}"}
    except Exception as error:
        return {"status": "network_error", "platform": "tower", "message": str(error)}
    if is_login_page(response):
        return {"status": "auth_expired", "platform": "tower", "message": "Tower Cookie 已过期"}
    return {"status": "success", "platform": "tower", "message": "Tower 认证有效"}


async def read_todo(url: str) -> dict[str, Any]:
    """读取 Tower 原始事实与图片到用户缓存，只返回路径、数量和来源摘要。"""
    if not tower_cookie():
        return {
            "status": "missing_config",
            "platform": "tower",
            "message": "未设置 TOWER_COOKIE",
        }
    url_error = validate_tower_todo_url(url)
    if url_error:
        return {
            "status": "invalid_input",
            "platform": "tower",
            "message": url_error,
        }
    try:
        data = await load_todo_data(url)
        image_cache = await cache_tower_images(data, url)
        cache_file = write_tower_raw(data, url, image_cache)
    except Exception as error:
        return tool_error(error)
    return tower_read_summary(data, cache_file, image_cache)


async def list_member_todos(url: str) -> dict[str, Any]:
    """读取成员任务清单最小集，不读正文、不写缓存。"""
    if not tower_cookie():
        return {
            "status": "missing_config",
            "platform": "tower",
            "message": "未设置 TOWER_COOKIE",
        }
    url_error = validate_tower_member_todo_url(url)
    if url_error:
        return {
            "status": "invalid_input",
            "platform": "tower",
            "message": url_error,
        }
    try:
        list_url, member_guid, kind = parse_member_todo_url(url)
        soup, stream_count = await load_member_todo_list(list_url)
        items = await asyncio.to_thread(
            parse_member_todo_items,
            soup,
            status=MEMBER_LIST_STATUS[kind],
        )
    except Exception as error:
        return tool_error(error)

    unresolved: list[str] = []
    if kind == "all":
        unresolved.append("all 清单不按任务区分完成状态，status 为「未提供」")
    if stream_count:
        unresolved.append(f"已展开 {stream_count} 个延迟加载区间")
    if not items:
        unresolved.append("未解析到任务卡片，页面结构可能已变化或清单为空")
    return {
        "status": "partial" if not items else "success",
        "platform": "tower",
        "message": "未解析到任务卡片" if not items else f"已读取 {len(items)} 条任务",
        "list_url": list_url,
        "member_guid": member_guid,
        "count": len(items),
        "items": items,
        "unresolved": unresolved,
    }


async def download_images(url: str, output_dir: str) -> dict[str, Any]:
    """下载 Tower 正文和全部评论中的附件图片，并返回本地文件与来源映射。"""
    if not tower_cookie():
        return {
            "status": "missing_config",
            "platform": "tower",
            "message": "未设置 TOWER_COOKIE",
        }
    url_error = validate_tower_todo_url(url)
    if url_error:
        return {
            "status": "invalid_input",
            "platform": "tower",
            "message": url_error,
        }
    try:
        data = await load_todo_data(url)
        result = await download_tower_image_attachments(
            data,
            Path(output_dir).expanduser(),
            file_prefix="tower",
        )
    except Exception as error:
        return tool_error(error)
    return {
        **result,
        "platform": "tower",
        "task_title": data["title"],
        "task_url": url,
        "project_sections": data["project_sections"],
        "stale_files_removed": True,
    }


async def add_comment(
    url: str,
    content: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """向 Tower 发布 Markdown 评论；默认只预览，dry_run=false 才真正发布。"""
    if not tower_cookie():
        return {
            "status": "missing_config",
            "platform": "tower",
            "message": "未设置 TOWER_COOKIE",
        }
    url_error = validate_tower_todo_url(url)
    if url_error:
        return {
            "status": "invalid_input",
            "platform": "tower",
            "message": url_error,
        }
    try:
        comment_html = text_to_comment_html(content)
        response = await request(url)
    except Exception as error:
        return tool_error(error)
    if is_login_page(response):
        return {
            "status": "auth_expired",
            "platform": "tower",
            "message": "Tower Cookie 已过期，请在 SpecWeaver 设置页重新配置 Tower 后重试",
        }
    # lxml 解析是纯 CPU 计算，扔线程池跑，避免大页面卡住事件循环。
    meta, title = await asyncio.to_thread(parse_comment_context, response.text)
    missing = [
        name for name in ("project_guid", "todo_guid", "csrf_token")
        if not meta[name]
    ]
    if missing:
        return {
            "status": "api_error",
            "platform": "tower",
            "message": f"Tower 页面缺少评论参数: {', '.join(missing)}",
        }

    if dry_run:
        # 预览是 preview 而不是 success：调用方走「先预览、等用户确认、再发布」
        # 两段式，两个状态一旦重合，评论没发出去也会被当成已经同步完成。
        return {
            "status": "preview",
            "platform": "tower",
            "task_title": title or "",
            "task_url": url,
            "comment_html": comment_html,
            "message": "Tower 评论预览（未发布）；确认无误后将 dry_run 设为 false 才会真正发布",
        }
    try:
        # 走 submit_comment 而不是直接 post_comment：它会在 Cookie 过期时续期，
        # 并重新抓一份与新 Cookie 配套的 CSRF 上下文。
        result = await submit_comment(url, comment_html)
    except Exception as error:
        return tool_error(error)
    return {
        "status": "success",
        "platform": "tower",
        "task_title": title or "",
        "task_url": url,
        "http_status": result.status_code,
        "message": f"Tower 评论发布成功（HTTP {result.status_code}）",
    }
