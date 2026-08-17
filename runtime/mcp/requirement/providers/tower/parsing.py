"""Tower 页面/评论的纯 HTML 解析与格式化（requirement 的 tower provider）。

这些函数只做"输入 HTML/soup、输出结构化数据或文本"的纯计算，不发请求、不碰
`~/.specweaver` 之外的任何东西，也不依赖 `client.py` 里的会话状态
（cookie、httpx client 等）。
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from markdownify import markdownify as html_to_markdown
import mistune


TOWER_BASE = "https://tower.im"
URL_PATTERN = re.compile(r"https?://[^\s<>()\[\]{}\"']+")
BUG_TAGS = {"bug"}
MEMBER_TODO_URL_PATTERN = re.compile(
    r"^/members/(?P<guid>[0-9a-f]{32})/todos/"
    r"(?P<kind>uncompleted|completed|all)/?$",
    re.I,
)
MEMBER_LIST_STATUS = {
    "uncompleted": "未完成",
    "completed": "已完成",
    "all": "未提供",
}

markdown_to_html = mistune.create_markdown(
    escape=True,
    hard_wrap=True,
    plugins=["strikethrough", "table", "task_lists", "url"],
)


def text_of(element) -> str:
    return element.get_text(" ", strip=True) if element else ""


def is_tower_attachment_url(value: str) -> bool:
    parsed = urlparse(value)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    return (
        host in {
            "attachments.tower.im",
            "tower.im",
            "www.tower.im",
            "tower3-downloads.tower.im",
        }
        and (
            host in {"attachments.tower.im", "tower3-downloads.tower.im"}
            or "/attfiles/" in path
        )
    )


def attachment_kind(name: str, source_url: str) -> tuple[str, str]:
    media_type = (
        mimetypes.guess_type(name)[0]
        or mimetypes.guess_type(urlparse(source_url).path)[0]
        or ""
    )
    if media_type.startswith("image/"):
        return "image", media_type
    if media_type.startswith("video/"):
        return "video", media_type
    if media_type in {
        "application/zip",
        "application/x-7z-compressed",
        "application/x-rar-compressed",
        "application/x-tar",
        "application/gzip",
    }:
        return "archive", media_type
    return "file", media_type


def parse_ordered_content(element) -> dict:
    soup = BeautifulSoup(str(element), "html.parser")
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    images = []
    attachments = []
    links = []
    handled_images: set[int] = set()
    for item in list(soup.find_all(["a", "img"])):
        if item.name == "a":
            raw_href = str(item.get("href") or "").strip()
            if not raw_href:
                continue
            href = urljoin(TOWER_BASE, raw_href)
            item["href"] = href
            label = text_of(item) or str(item.get("download") or "")
            links.append({"text": label, "url": href})
            if not is_tower_attachment_url(href):
                continue
            name = (
                str(item.get("download") or "").strip()
                or label
                or Path(urlparse(href).path).name
                or "未提供"
            )
            kind, media_type = attachment_kind(name, href)
            attachments.append({
                "position": len(attachments) + 1,
                "source_url": href,
                "name": name,
                "kind": kind,
                "media_type": media_type,
                "size": str(item.get("data-size") or "未提供"),
            })
            nested_images = item.find_all("img")
            handled_images.update(id(image) for image in nested_images)
            continue

        image = item
        raw_src = str(image.get("src") or "").strip()
        alt = image.get("alt", "")
        if not raw_src:
            image.replace_with(alt)
            continue
        src = urljoin(TOWER_BASE, raw_src)
        image["src"] = src
        images.append({
            "position": len(images) + 1,
            "source_url": src,
            "alt": alt,
        })
        if id(image) not in handled_images and is_tower_attachment_url(src):
            name = alt or Path(urlparse(src).path).name or "未提供"
            kind, media_type = attachment_kind(name, src)
            attachments.append({
                "position": len(attachments) + 1,
                "source_url": src,
                "name": name,
                "kind": kind,
                "media_type": media_type,
                "size": str(image.get("data-size") or "未提供"),
            })
    markdown = html_to_markdown(
        str(soup),
        heading_style="ATX",
        bullets="-",
    ).strip()
    return {
        "text": re.sub(r"\n{3,}", "\n\n", markdown),
        "images": images,
        "attachments": attachments,
        "links": links,
    }


def clean_html(element) -> str:
    return parse_ordered_content(element)["text"]


def validate_tower_todo_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return "请提供 Tower HTTPS 任务链接"
    if parsed.netloc.lower() not in {"tower.im", "www.tower.im"} or "/todos/" not in parsed.path:
        return "请提供有效的 Tower 任务链接"
    if MEMBER_TODO_URL_PATTERN.match(parsed.path):
        return "请提供 Tower 单条任务链接，而不是成员任务清单"
    return None


def validate_tower_member_todo_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return "请提供 Tower HTTPS 链接"
    if parsed.netloc.lower() not in {"tower.im", "www.tower.im"}:
        return "请提供有效的 Tower 链接"
    if not MEMBER_TODO_URL_PATTERN.match(parsed.path):
        return (
            "请提供 Tower 成员任务清单链接"
            "（路径形如 /members/{guid}/todos/uncompleted/）"
        )
    return None


def parse_member_todo_url(url: str) -> tuple[str, str, str]:
    """返回规范化清单 URL、成员 GUID、清单种类。"""
    parsed = urlparse(url)
    match = MEMBER_TODO_URL_PATTERN.match(parsed.path)
    if match is None:
        raise ValueError("请提供 Tower 成员任务清单链接")
    guid = match.group("guid").lower()
    kind = match.group("kind").lower()
    return (
        f"{parsed.scheme}://{parsed.netloc}/members/{guid}/todos/{kind}/",
        guid,
        kind,
    )


def parse_member_todo_card(
    card,
    *,
    group_name: str = "未提供",
    status: str = "未完成",
) -> dict | None:
    """从 tr-todo-item-plus 卡片提取最小集字段。"""
    todo_id = str(card.get("guid") or card.get("data-guid") or "").strip()
    if not todo_id:
        return None
    title_anchor = card.select_one("a.todo-rest")
    title = text_of(title_anchor).strip() if title_anchor else ""
    if len(title) > 200:
        title = title[:200].rstrip() + "..."
    if not title:
        title = "未提供"
    detail_path = str(card.get("todo-detail-url") or "").strip()
    if detail_path:
        item_url = urljoin(TOWER_BASE, detail_path)
    else:
        href = str(title_anchor.get("href") or "").strip() if title_anchor else ""
        item_url = urljoin(TOWER_BASE, href) if href else f"{TOWER_BASE}/todos/{todo_id}"
    tags: list[str] = []
    for item in card.select(
        "[data-tag-name], [data-label-name], .todo-label, .todo-tag"
    ):
        value = str(
            item.get("data-tag-name") or item.get("data-label-name") or ""
        ).strip() or text_of(item)
        if value and value not in tags:
            tags.append(value)
    due_date = ""
    due_anchor = card.select_one("a.todo-due-at tr-readable-datetime")
    if due_anchor:
        due_date = str(due_anchor.get("date") or "").strip()
    project_el = card.select_one(".label.todo-project")
    project_name = text_of(project_el).strip() if project_el else ""
    return {
        "todo_id": todo_id,
        "title": title,
        "url": item_url,
        "status": status,
        "task_type": task_type_from_tags(tags),
        "tags": tags,
        "group": group_name or "未提供",
        "project": project_name or "未提供",
        "due_date": due_date,
    }


def parse_member_todo_items(soup: BeautifulSoup, *, status: str) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()

    def add_card(card, group_name: str) -> None:
        item = parse_member_todo_card(card, group_name=group_name, status=status)
        if not item or item["todo_id"] in seen:
            return
        seen.add(item["todo_id"])
        items.append(item)

    for group in soup.select("tr-todos-group"):
        group_name = text_of(group.select_one(".group-name")).strip() or "未提供"
        for card in group.select("tr-todo-item-plus.todo-plus"):
            add_card(card, group_name)
    for card in soup.select("tr-todo-item-plus.todo-plus"):
        add_card(card, "未提供")
    return items


def parse_comment_context(html: str) -> tuple[dict[str, str], str]:
    soup = BeautifulSoup(html, "lxml")
    meta = parse_comment_meta(soup)
    title = (soup.select_one(".page-inner") or {}).get("data-page-name", "")
    return meta, title


def parse_comment_meta(soup: BeautifulSoup) -> dict[str, str]:
    page = soup.select_one(".page-inner")
    project_guid = page.get("data-project-guid", "") if page else ""
    todo_guid = page.get("data-page-guid", "") if page else ""
    form = soup.select_one("form[action*='/comments']")
    action = form.get("action", "") if form else ""
    match = re.search(
        r"/projects/([0-9a-f]{32})/todos/([0-9a-f]{32})/comments",
        action,
        re.I,
    )
    if match:
        project_guid, todo_guid = match.groups()
    if not todo_guid:
        todo_page = soup.select_one("tr-todo-page[todo-guid]")
        todo_guid = todo_page.get("todo-guid", "") if todo_page else ""
    csrf = soup.select_one("meta[name='csrf-token']")
    return {
        "project_guid": project_guid,
        "todo_guid": todo_guid,
        "csrf_token": csrf.get("content", "") if csrf else "",
        "action": action,
    }


def adapt_tower_comment_html(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    for deleted in soup.find_all("del"):
        deleted.name = "s"
    for item in soup.select("li.task-list-item"):
        checkbox = item.find("input", attrs={"type": "checkbox"})
        if checkbox:
            checkbox.replace_with("☑ " if checkbox.has_attr("checked") else "☐ ")
        item.attrs.pop("class", None)
    return str(soup).strip()


def text_to_comment_html(content: str) -> str:
    value = content.strip()
    if not value:
        raise ValueError("评论内容不能为空")
    if value.startswith("<"):
        return adapt_tower_comment_html(value)
    return adapt_tower_comment_html(markdown_to_html(value))


def parse_comments(soup: BeautifulSoup) -> list[dict]:
    comments = []
    for content in soup.select("tr-editor-output-renderer .comment-content"):
        if content.find_parent(class_="desc-content"):
            continue
        wrapper = content.find_parent(class_="comment")
        if wrapper is None:
            continue
        body_soup = BeautifulSoup(str(content), "html.parser")
        body = body_soup.select_one(".comment-content") or body_soup
        quotes = []
        for quote in body.select("blockquote, .quote, .comment-quote"):
            quote_text = text_of(quote)
            if quote_text and quote_text not in quotes:
                quotes.append(quote_text)
            quote.decompose()
        ordered_content = parse_ordered_content(body)
        if not ordered_content["text"] and not quotes:
            continue
        author = text_of(wrapper.select_one("a.author"))
        comment_id = ""
        created_at = ""
        reply_to = ""
        comment_id = next((
            str(wrapper.get(name) or "").strip()
            for name in ("data-comment-guid", "data-comment-id", "data-guid", "id")
            if wrapper.get(name)
        ), "")
        time_element = wrapper.select_one("time, [datetime], .time, .date")
        if time_element:
            created_at = next((
                str(time_element.get(name) or "").strip()
                for name in ("datetime", "title", "data-tooltip")
                if time_element.get(name)
            ), "") or text_of(time_element)
        reply_to = next((
            str(wrapper.get(name) or "").strip()
            for name in ("data-reply-to", "data-reply-comment-id")
            if wrapper.get(name)
        ), "")
        if not reply_to:
            reply_to = text_of(
                wrapper.select_one(".reply-to, .comment-reply, .comment-ref")
            )
        comments.append({
            "id": comment_id,
            "author": author,
            "created_at": created_at,
            "reply_to": reply_to,
            "quote": "\n\n".join(quotes),
            "text": ordered_content["text"],
            "images": ordered_content["images"],
            "attachments": ordered_content["attachments"],
            "links": ordered_content["links"],
        })
    return comments


def build_image_occurrences(data: dict) -> list[dict]:
    occurrences = []

    def append(scope: str, scope_index: int, images: list[dict]) -> None:
        for image in images:
            occurrences.append({
                "occurrence_index": len(occurrences) + 1,
                "scope": scope,
                "scope_index": scope_index,
                **image,
            })

    append("description", 1, data.get("description_images", []))
    for comment_index, comment in enumerate(data.get("comments", []), 1):
        append("comment", comment_index, comment.get("images", []))
    return occurrences


def build_attachment_occurrences(data: dict) -> list[dict]:
    occurrences = []

    def append(scope: str, scope_index: int, attachments: list[dict]) -> None:
        for attachment in attachments:
            occurrences.append({
                "occurrence_index": len(occurrences) + 1,
                "scope": scope,
                "scope_index": scope_index,
                **attachment,
            })

    append("description", 1, data.get("description_attachments", []))
    for comment_index, comment in enumerate(data.get("comments", []), 1):
        append("comment", comment_index, comment.get("attachments", []))
    return occurrences


def unique_attachments(occurrences: list[dict]) -> list[dict]:
    items = []
    seen: set[str] = set()
    for occurrence in occurrences:
        source_url = occurrence["source_url"]
        if source_url in seen:
            continue
        seen.add(source_url)
        items.append({
            key: occurrence[key]
            for key in ("source_url", "name", "kind", "media_type", "size")
        })
    return items


TOWER_SELF_HOSTS = {
    "tower.im",
    "www.tower.im",
    "attachments.tower.im",
    "tower3-downloads.tower.im",
}


def collect_external_links(data: dict) -> list[str]:
    """从正文与评论里提取外部链接（保序去重），过滤掉 Tower 自身域名。

    只负责「提取」；链接属于哪个能力（设计稿 / API 文档 / ...）由编排侧
    拿着各能力 provider 的域名声明分类，这里不维护那张表。
    """
    candidates = []
    candidates.extend(data.get("description_links", []))
    for comment in data.get("comments", []):
        candidates.extend(comment.get("links", []))
    for text in [
        data.get("description", ""),
        *(comment.get("text", "") for comment in data.get("comments", [])),
    ]:
        candidates.extend({"url": match.group(0)} for match in URL_PATTERN.finditer(text))
    links: list[str] = []
    for item in candidates:
        value = str(item.get("url") or "").rstrip(".,;:!?，。；：！？")
        host = (urlparse(value).hostname or "").lower()
        if not host or host in TOWER_SELF_HOSTS:
            continue
        if value not in links:
            links.append(value)
    return links


def parse_images(soup: BeautifulSoup) -> list[str]:
    urls = []
    for element in soup.select("img[src], a[href]"):
        value = urljoin(
            TOWER_BASE,
            element.get("src") or element.get("href") or "",
        )
        kind, _ = attachment_kind(text_of(element), value)
        if is_tower_attachment_url(value) and kind == "image" and value not in urls:
            urls.append(value)
    return urls


def parse_project_sections(soup: BeautifulSoup) -> list[dict[str, str]]:
    sections = []
    for item in soup.select("tr-todo-section-links-field-detail-item.section-link"):
        value = {
            "category": text_of(item.select_one("a.container-name")),
            "section": text_of(item.select_one("a.sectionable-name")),
        }
        if (value["category"] or value["section"]) and value not in sections:
            sections.append(value)
    return sections


def parse_tags(soup: BeautifulSoup) -> list[str]:
    tags = []
    selectors = (
        "[data-tag-name], [data-label-name], "
        "tr-todo-labels-field-detail-item, .todo-label, .todo-tag"
    )
    for item in soup.select(selectors):
        if (
            item.find_parent(class_="desc-content")
            or item.find_parent(class_="comment-content")
        ):
            continue
        value = str(item.get("data-tag-name") or item.get("data-label-name") or "")
        value = value.strip() or text_of(item)
        if value and value not in tags:
            tags.append(value)
    return tags


def task_type_from_tags(tags: list[str]) -> str:
    normalized = {
        re.sub(r"\s+", "", tag).strip("#[]【】").lower()
        for tag in tags
    }
    return "bug" if normalized & BUG_TAGS else "requirement"


def decode_javascript_string(value: str) -> str:
    output = []
    index = 0
    simple = {
        "n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f",
        "v": "\v", "0": "\0", "'": "'", '"': '"', "\\": "\\", "/": "/",
    }
    while index < len(value):
        char = value[index]
        if char != "\\" or index + 1 >= len(value):
            output.append(char)
            index += 1
            continue
        escape = value[index + 1]
        if escape in simple:
            output.append(simple[escape])
            index += 2
            continue
        if escape in {"u", "x"}:
            size = 4 if escape == "u" else 2
            digits = value[index + 2:index + 2 + size]
            if len(digits) == size and re.fullmatch(r"[0-9a-fA-F]+", digits):
                output.append(chr(int(digits, 16)))
                index += 2 + size
                continue
        output.append(escape)
        index += 2
    return "".join(output)


def parse_stream_response(javascript: str) -> str:
    match = re.search(r"var\s+list\s*=\s*'((?:\\.|[^'\\])*)';", javascript, re.S)
    if not match:
        raise ValueError("无法解析 Tower 历史记录响应")
    return decode_javascript_string(match.group(1))


def parse_todo(soup: BeautifulSoup, url: str) -> dict:
    page = soup.select_one(".page-inner")
    tags = parse_tags(soup)
    data = {
        "title": page.get("data-page-name", "") if page else "",
        "url": url,
        "created_at": page.get("data-since", "") if page else "",
        "status": "进行中" if soup.select_one("[data-tooltip*='正在进行']") else "待处理",
        "comments": parse_comments(soup),
        "image_urls": parse_images(soup),
        "project_sections": parse_project_sections(soup),
        "tags": tags,
        "task_type": task_type_from_tags(tags),
    }
    data["todo_id"] = text_of(soup.select_one(".original-text"))
    data["assignee"] = text_of(soup.select_one(".addition-content.has-assignee"))
    due = soup.select_one("tr-detail-date-time input[type=hidden]")
    data["due_date"] = due.get("value", "") if due else ""
    description = soup.select_one(".desc-content")
    ordered_description = (
        parse_ordered_content(description)
        if description else {
            "text": "",
            "images": [],
            "attachments": [],
            "links": [],
        }
    )
    data["description"] = ordered_description["text"]
    data["description_images"] = ordered_description["images"]
    data["description_attachments"] = ordered_description["attachments"]
    data["description_links"] = ordered_description["links"]
    data["parents"] = [
        {"title": text_of(item), "url": urljoin(TOWER_BASE, item.get("href", ""))}
        for item in soup.select(".breadcrumb-link")
    ]
    data["sub_todos"] = []
    for row in soup.select("tr-grid-subtodo-row"):
        title = text_of(row.select_one(".todo-content-shadow .todo-rest"))
        if title:
            data["sub_todos"].append({
                "title": title,
                "url": urljoin(TOWER_BASE, row.get("detail-url", "")),
            })
    data["image_occurrences"] = build_image_occurrences(data)
    data["attachment_occurrences"] = build_attachment_occurrences(data)
    data["attachments"] = unique_attachments(data["attachment_occurrences"])
    # external_sources 不在这里生成：本模块只做纯解析，链接的「提取」在
    # collect_external_links，「按能力分类」由调用方拿 capability.classify_links
    # 完成（见 client.load_todo_data）。
    return data


def format_todo(data: dict) -> str:
    lines = [
        f"# {data['title'] or '未提供'}",
        "",
        "## 任务信息",
        "",
        f"- Tower 链接：{data['url']}",
        f"- 任务 ID：{data.get('todo_id') or '未提供'}",
        f"- 任务类型：{'Bug' if data.get('task_type') == 'bug' else '普通需求'}",
        f"- 状态：{data.get('status') or '未提供'}",
        f"- 负责人：{data.get('assignee') or '未提供'}",
        f"- 截止时间：{data.get('due_date') or '未提供'}",
        f"- 创建时间：{data.get('created_at') or '未提供'}",
        f"- Tags：{', '.join(data.get('tags') or []) or '未提供'}",
    ]
    lines.extend(["", "## 所属分类与分组"])
    if data["project_sections"]:
        for item in data["project_sections"]:
            lines.append(
                f"- 分类: {item['category'] or '未提供'} | 分组: {item['section'] or '未提供'}"
            )
    else:
        lines.append("- 分类: 未提供 | 分组: 未提供")
    lines.extend(["", "## 父任务"])
    if data["parents"]:
        lines.extend(f"- {item['title']}: {item['url']}" for item in data["parents"])
    else:
        lines.append("- 未提供")
    lines.extend(["", "## 正文", "", data.get("description") or "未提供"])
    lines.extend(["", f"## 子任务 ({len(data['sub_todos'])})"])
    if data["sub_todos"]:
        lines.extend(f"- {item['title']}: {item['url']}" for item in data["sub_todos"])
    else:
        lines.append("- 无")
    lines.extend(["", f"## 评论 ({len(data['comments'])})"])
    if not data["comments"]:
        lines.append("- 无")
    for index, comment in enumerate(data["comments"], 1):
        lines.extend([
            "",
            f"### 评论 {index}",
            "",
            f"- 评论 ID：{comment.get('id') or '未提供'}",
            f"- 作者：{comment.get('author') or '未提供'}",
            f"- 时间：{comment.get('created_at') or '未提供'}",
            f"- 回复对象：{comment.get('reply_to') or '未提供'}",
            "",
            "#### 引用",
            "",
            comment.get("quote") or "未提供",
            "",
            "#### 正文",
            "",
            comment.get("text") or "未提供",
        ])
    lines.extend([
        "",
        "## 读取完整性",
        "",
        f"- 延迟加载区间：{data.get('stream_count', 0)}",
        "- 评论读取状态：完整",
        "- 子任务读取深度：仅标题和链接，未递归",
    ])
    occurrences = data.get("attachment_occurrences", [])
    lines.extend(["", f"## 附件索引 ({len(occurrences)})"])
    if not occurrences:
        lines.append("- 无")
    for item in occurrences:
        scope = "正文" if item["scope"] == "description" else f"评论 {item['scope_index']}"
        lines.extend([
            "",
            f"### 附件 {item['occurrence_index']}",
            "",
            f"- 出现位置：{scope} / 第 {item['position']} 个附件",
            f"- 名称：{item.get('name') or '未提供'}",
            f"- 类型：{item.get('media_type') or item.get('kind') or '未提供'}",
            f"- 大小：{item.get('size') or '未提供'}",
            f"- 来源：{item['source_url']}",
        ])
    sources = data.get("external_sources", {})
    source_count = sum(len(items) for items in sources.values())
    lines.extend(["", f"## 外部来源线索 ({source_count})"])
    # key 是能力名（由编排侧分类注入），动态遍历而不是写死清单：
    # 新能力接入后若这里漏列，它的链接会静默不出现在 raw.md 里。
    labels = {"design": "设计稿", "apidoc": "API 文档", "other": "其他"}
    for source in sorted(sources, key=lambda key: (key == "other", key)):
        values = sources.get(source, [])
        lines.extend(["", f"### {labels.get(source, source)} ({len(values)})"])
        lines.extend(f"- {value}" for value in values)
        if not values:
            lines.append("- 无")
    return "\n".join(lines)
