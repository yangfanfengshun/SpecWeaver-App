#!/usr/bin/env python3
"""倒入口表：路由 → 页面壳 → 壳子 import 的实现。不评判、不写 .knowledge。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PAGE_EXTS = (".tsx", ".jsx", ".ts", ".js")
SKIP_IMPORT_SUFFIXES = (".css", ".scss", ".sass", ".less", ".png", ".jpg", ".svg", ".json")
IMPORT_RE = re.compile(
    r"""(?:import\s+(?:type\s+)?(?:[^'"\n]+?\s+from\s+)?|export\s+.+?\s+from\s+)['"]([^'"]+)['"]""",
    re.MULTILINE,
)


def find_git_root(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def strip_js_comments(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    state = "code"
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block"
                i += 2
                continue
            if ch in "'\"`":
                state = ch
                out.append(ch)
                i += 1
                continue
            out.append(ch)
            i += 1
            continue
        if state == "line":
            if ch == "\n":
                state = "code"
                out.append(ch)
            i += 1
            continue
        if state == "block":
            if ch == "*" and nxt == "/":
                state = "code"
                i += 2
                continue
            i += 1
            continue
        out.append(ch)
        if ch == "\\" and i + 1 < n:
            out.append(text[i + 1])
            i += 2
            continue
        if ch == state:
            state = "code"
        i += 1
    return "".join(out)


def matching_bracket(text: str, open_idx: int) -> int:
    open_ch = text[open_idx]
    close_ch = {"[": "]", "{": "}", "(": ")"}[open_ch]
    depth = 0
    i = open_idx
    n = len(text)
    in_str = None
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in "'\"`":
            in_str = ch
            i += 1
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def iter_objects(text: str, start: int, end: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    i = start
    in_str = None
    while i < end:
        ch = text[i]
        if in_str:
            if ch == "\\" and i + 1 < end:
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in "'\"`":
            in_str = ch
            i += 1
            continue
        if ch == "{":
            close = matching_bracket(text, i)
            if close < 0:
                break
            spans.append((i, close))
            i += 1
            continue
        i += 1
    return spans


def skip_ws(text: str, i: int, end: int) -> int:
    while i < end and text[i].isspace():
        i += 1
    return i


def parse_scalar_fields(obj: str) -> dict[str, str | bool]:
    wanted = {"path", "name", "component", "redirect", "hideInMenu", "layout"}
    fields: dict[str, str | bool] = {}
    end = len(obj) - 1
    i = 1
    in_str = None
    depth = 1
    while i < end:
        ch = obj[i]
        if in_str:
            if ch == "\\" and i + 1 < end:
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in "'\"`":
            in_str = ch
            i += 1
            continue
        if ch in "{[":
            close = matching_bracket(obj, i)
            if close < 0:
                break
            i = close + 1
            continue
        if ch in "}]":
            i += 1
            continue
        if ch.isalpha() or ch == "_":
            start = i
            while i < end and (obj[i].isalnum() or obj[i] in "_$"):
                i += 1
            key = obj[start:i]
            i = skip_ws(obj, i, end)
            if i >= end or obj[i] != ":":
                continue
            i = skip_ws(obj, i + 1, end)
            if depth != 1 or key not in wanted:
                if i < end and obj[i] in "{[":
                    close = matching_bracket(obj, i)
                    i = (close + 1) if close >= 0 else end
                elif i < end and obj[i] in "'\"`":
                    quote = obj[i]
                    i += 1
                    while i < end:
                        if obj[i] == "\\":
                            i += 2
                            continue
                        if obj[i] == quote:
                            i += 1
                            break
                        i += 1
                else:
                    while i < end and obj[i] not in ",}":
                        i += 1
                continue
            if i < end and obj[i] in "'\"":
                quote = obj[i]
                i += 1
                start_val = i
                while i < end and obj[i] != quote:
                    if obj[i] == "\\":
                        i += 2
                        continue
                    i += 1
                fields[key] = obj[start_val:i]
                i += 1
            elif obj.startswith("true", i):
                fields[key] = True
                i += 4
            elif obj.startswith("false", i):
                fields[key] = False
                i += 5
            continue
        i += 1
    return fields


def extract_string_array(text: str, key: str) -> list[str]:
    match = re.search(rf"\b{re.escape(key)}\s*:", text)
    if not match:
        return []
    i = skip_ws(text, match.end(), len(text))
    if i >= len(text) or text[i] != "[":
        return []
    close = matching_bracket(text, i)
    if close < 0:
        return []
    return re.findall(r"""['"]([^'"]+)['"]""", text[i : close + 1])


def find_page_file(base: Path, rel: str) -> Path | None:
    rel = rel.strip().lstrip("./").rstrip("/")
    candidate = base / rel
    if candidate.is_file():
        return candidate
    for ext in PAGE_EXTS:
        file_path = Path(str(candidate) + ext)
        if file_path.is_file():
            return file_path
    if candidate.is_dir():
        for ext in PAGE_EXTS:
            index = candidate / f"index{ext}"
            if index.is_file():
                return index
    parent = candidate.parent
    if parent.is_dir():
        for ext in PAGE_EXTS:
            index = parent / f"{candidate.name}{ext}"
            if index.is_file():
                return index
    return None


def rel_to_root(git_root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(git_root.resolve()))
    except ValueError:
        return str(path)


def local_imports(page_file: Path) -> list[str]:
    text = page_file.read_text(encoding="utf-8", errors="replace")
    found: list[str] = []
    for spec in IMPORT_RE.findall(text):
        if spec.startswith(("http://", "https://")):
            continue
        if spec.endswith(SKIP_IMPORT_SUFFIXES):
            continue
        if spec.startswith((".", "@/", "~/", "@components", "@base")):
            if spec not in found:
                found.append(spec)
    return found


def resolve_import(git_root: Path, src_root: Path, from_file: Path, spec: str) -> Path | None:
    if spec.startswith("."):
        return find_page_file(from_file.parent, spec)
    if spec.startswith("@/"):
        return find_page_file(git_root / "src", spec[2:]) or find_page_file(src_root, spec[2:])
    if spec.startswith("~/"):
        return find_page_file(src_root, spec[2:])
    return None


def is_pages_impl(spec: str) -> bool:
    path = spec.replace("\\", "/")
    if "_pages" not in path:
        return False
    if path.rstrip("/").endswith("_utils") or "/_utils/" in path:
        return False
    return True


def enrich_entry(git_root: Path, src_root: Path, entry: dict) -> dict:
    page_rel = entry.get("page_file")
    if not page_rel:
        entry["imports"] = []
        entry["impl_file"] = None
        entry["impl_imports"] = []
        return entry
    page_file = git_root / page_rel
    imports = local_imports(page_file) if page_file.is_file() else []
    entry["imports"] = imports
    pages_all = [spec for spec in imports if "_pages" in spec.replace("\\", "/")]
    pages_impls = [spec for spec in pages_all if is_pages_impl(spec)]
    impl_file = None
    impl_imports: list[str] = []
    if len(pages_all) == 1 and len(pages_impls) == 1:
        impl_file = resolve_import(git_root, src_root, page_file, pages_impls[0])
        if impl_file is not None and impl_file.is_file():
            impl_imports = local_imports(impl_file)
        else:
            impl_imports = pages_impls
    elif pages_all:
        impl_imports = pages_all
    entry["impl_file"] = rel_to_root(git_root, impl_file)
    entry["impl_imports"] = impl_imports
    return entry


def parse_umi_routes(git_root: Path, route_file: Path) -> dict:
    text = strip_js_comments(route_file.read_text(encoding="utf-8", errors="replace"))
    src_root = git_root / "src"
    pages_root = src_root / "pages"
    default_idx = text.find("export default")
    scan_from = default_idx if default_idx >= 0 else 0
    array_idx = text.find("[", scan_from)
    if array_idx < 0:
        return {"kind": "umi", "entries": [], "message": "routes 文件里没有找到数组"}
    array_end = matching_bracket(text, array_idx)
    if array_end < 0:
        return {"kind": "umi", "entries": [], "message": "routes 数组括号不配对"}

    entries: list[dict] = []
    seen: set[str] = set()
    for start, close in iter_objects(text, array_idx, array_end):
        fields = parse_scalar_fields(text[start : close + 1])
        path = fields.get("path")
        component = fields.get("component")
        redirect = fields.get("redirect")
        if not isinstance(path, str) or not path or path in {"*", "/*"}:
            continue
        if path in seen:
            continue
        if not component and not redirect:
            continue
        if isinstance(component, str) and component.rstrip("/").endswith("404"):
            continue
        seen.add(path)
        page_file = None
        page_status = "redirect"
        if isinstance(component, str) and component and "404" not in component:
            page_file = find_page_file(pages_root, component)
            page_status = "ok" if page_file else "missing"
        elif isinstance(component, str) and "404" in component:
            page_status = "skip"
        entry = {
            "path": path,
            "name": fields.get("name") if isinstance(fields.get("name"), str) else "",
            "hide_in_menu": bool(fields.get("hideInMenu") is True),
            "layout": fields.get("layout") is not False,
            "redirect": redirect if isinstance(redirect, str) else None,
            "component": component if isinstance(component, str) else None,
            "page_file": rel_to_root(git_root, page_file),
            "page_file_status": page_status,
            "group": "/" + path.strip("/").split("/")[0] if path.strip("/") else "/",
        }
        entries.append(enrich_entry(git_root, src_root, entry))
    return {
        "kind": "umi",
        "src_root": rel_to_root(git_root, src_root),
        "entries": entries,
    }


def parse_tab_bar(text: str) -> dict[str, str]:
    names: dict[str, str] = {}
    idx = text.find("tabBar")
    if idx < 0:
        return names
    brace = text.find("{", idx)
    if brace < 0:
        return names
    close = matching_bracket(text, brace)
    if close < 0:
        return names
    block = text[brace : close + 1]
    list_idx = block.find("list")
    if list_idx < 0:
        return names
    arr = block.find("[", list_idx)
    if arr < 0:
        return names
    arr_end = matching_bracket(block, arr)
    if arr_end < 0:
        return names
    for start, end in iter_objects(block, arr, arr_end):
        obj = block[start : end + 1]
        path_match = re.search(r"""pagePath\s*:\s*['"]([^'"]+)['"]""", obj)
        text_match = re.search(r"""text\s*:\s*['"]([^'"]+)['"]""", obj)
        if path_match:
            names[path_match.group(1)] = text_match.group(1) if text_match else ""
    return names


def parse_taro_config(git_root: Path, config_file: Path) -> dict:
    text = strip_js_comments(config_file.read_text(encoding="utf-8", errors="replace"))
    src_root = config_file.parent
    pages = extract_string_array(text, "pages")
    tab_name = parse_tab_bar(text)

    entries: list[dict] = []
    seen: set[str] = set()

    def add_page(page: str, package_root: str | None) -> None:
        key = f"{package_root or ''}/{page}".strip("/")
        if key in seen:
            return
        seen.add(key)
        rel = f"{package_root}/{page}" if package_root else page
        page_file = find_page_file(src_root, rel)
        route = "/" + rel.replace("\\", "/").removesuffix("/index")
        entry = {
            "path": route,
            "name": tab_name.get(page, ""),
            "hide_in_menu": page not in tab_name,
            "layout": True,
            "redirect": None,
            "component": rel,
            "page_file": rel_to_root(git_root, page_file),
            "page_file_status": "ok" if page_file else "missing",
            "group": package_root or "main",
            "in_tab_bar": page in tab_name,
        }
        entries.append(enrich_entry(git_root, src_root, entry))

    for page in pages:
        add_page(page, None)

    pkg_idx = text.find("subPackages")
    if pkg_idx >= 0:
        brace = text.find("[", pkg_idx)
        if brace >= 0:
            close = matching_bracket(text, brace)
            if close >= 0:
                for start, end in iter_objects(text, brace, close):
                    obj = text[start : end + 1]
                    roots = extract_string_array(obj, "root")
                    pkg_pages = extract_string_array(obj, "pages")
                    root = roots[0] if roots else None
                    if not root:
                        root_match = re.search(r"""root\s*:\s*['"]([^'"]+)['"]""", obj)
                        root = root_match.group(1) if root_match else None
                    if not root:
                        continue
                    for page in pkg_pages:
                        add_page(page, root)

    return {
        "kind": "taro",
        "src_root": rel_to_root(git_root, src_root),
        "entries": entries,
    }


def find_sources(git_root: Path) -> tuple[Path | None, Path | None]:
    umi = None
    for name in ("routes.ts", "routes.js", "routes.tsx"):
        candidate = git_root / "config" / name
        if candidate.is_file():
            umi = candidate
            break
    taro = None
    for rel in (
        "src/app.config.ts",
        "src/app.config.js",
        "client/src/app.config.ts",
        "client/src/app.config.js",
    ):
        candidate = git_root / rel
        if candidate.is_file():
            taro = candidate
            break
    return umi, taro


def emit(payload: dict, as_json: bool) -> None:
    if as_json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return
    print(f"status: {payload['status']}")
    if payload.get("kind"):
        print(f"kind: {payload['kind']}")
    if payload.get("route_file"):
        print(f"route_file: {payload['route_file']}")
    if payload.get("message"):
        print(payload["message"])
    entries = payload.get("entries") or []
    print(f"entries: {len(entries)}")
    for item in entries:
        flag = []
        if item.get("hide_in_menu"):
            flag.append("hideInMenu")
        if item.get("redirect"):
            flag.append(f"redirect→{item['redirect']}")
        if item.get("page_file_status") == "missing":
            flag.append("missing")
        extra = f" ({', '.join(flag)})" if flag else ""
        print(f"- {item['path']}  {item.get('name') or ''}{extra}")
        if item.get("page_file"):
            print(f"    壳: {item['page_file']}")
        if item.get("impl_file"):
            print(f"    实现: {item['impl_file']}")
        if item.get("impl_imports"):
            print(f"    实现import: {', '.join(item['impl_imports'][:8])}")
        elif item.get("imports"):
            print(f"    import: {', '.join(item['imports'][:8])}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="倒入口表：路由 → 页面壳 → import。不评判、不写知识库。"
    )
    parser.add_argument("--cwd", default=".", help="从该目录向上找 Git 根，默认当前目录")
    parser.add_argument(
        "--text",
        action="store_true",
        help="打印可读文本；默认 JSON",
    )
    args = parser.parse_args()

    cwd = Path(args.cwd)
    git_root = find_git_root(cwd)
    as_json = not args.text
    if git_root is None:
        emit(
            {
                "status": "no_git",
                "cwd": str(cwd.resolve()),
                "entries": [],
                "message": "不是 Git 仓库，无法定位项目根",
            },
            as_json,
        )
        return 2

    umi, taro = find_sources(git_root)
    if umi is None and taro is None:
        emit(
            {
                "status": "no_routes",
                "git_root": str(git_root),
                "entries": [],
                "message": "没有 config/routes.ts 或 app.config，改手工读入口并在报告里标明",
            },
            as_json,
        )
        return 0

    route_file = umi or taro
    parsed = parse_umi_routes(git_root, umi) if umi else parse_taro_config(git_root, taro)  # type: ignore[arg-type]
    extra = []
    if umi and taro:
        extra.append(f"同时发现 {rel_to_root(git_root, taro)}，本次只用 umi 路由表")

    emit(
        {
            "status": "ok",
            "git_root": str(git_root),
            "route_file": rel_to_root(git_root, route_file),
            "kind": parsed.get("kind"),
            "src_root": parsed.get("src_root"),
            "entry_count": len(parsed.get("entries") or []),
            "entries": parsed.get("entries") or [],
            "notes": extra,
            "message": parsed.get("message"),
        },
        as_json,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
