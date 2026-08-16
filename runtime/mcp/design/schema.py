"""设计稿中立结构：各平台折成同一套字段后再交给 Agent。

画板树、切图规则、平台方言都留在各自 provider 里。这里只负责出口形状：
五种 `type`、相对画板的 `frame`、`#RRGGBB`、落盘和切图路径回写。
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from common import atomic_write_text

NODE_TYPES = frozenset({"text", "image", "icon", "container", "shape"})


def num(value: Any, default: float | int | None = None) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return value
    return default


def compact_num(value: float | int) -> float | int:
    number = float(value)
    return int(number) if number.is_integer() else round(number, 2)


def hex_from_rgb(color: dict[str, Any] | None) -> str | None:
    if not isinstance(color, dict):
        return None
    raw = color.get("value")
    if isinstance(raw, str) and raw.startswith("#"):
        return raw.upper()
    if isinstance(raw, str) and raw.startswith("rgba"):
        match = re.match(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", raw)
        if match:
            return "#{:02X}{:02X}{:02X}".format(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
            )
    red = num(color.get("r", color.get("red")))
    green = num(color.get("g", color.get("green")))
    blue = num(color.get("b", color.get("blue")))
    if red is None or green is None or blue is None:
        return None
    if red <= 1 and green <= 1 and blue <= 1:
        red, green, blue = red * 255, green * 255, blue * 255
    return "#{:02X}{:02X}{:02X}".format(
        max(0, min(255, round(red))),
        max(0, min(255, round(green))),
        max(0, min(255, round(blue))),
    )


def solid_hex(fills: Any) -> str | None:
    if isinstance(fills, dict):
        fills = [fills]
    if not isinstance(fills, list):
        return None
    for item in fills:
        if not isinstance(item, dict):
            continue
        if item.get("visible") is False or item.get("isEnabled") is False:
            continue
        kind = str(item.get("type") or "").upper()
        if kind in {"SOLID", "COLOR", ""} or "color" in item:
            found = hex_from_rgb(
                item.get("color") if isinstance(item.get("color"), dict) else item
            )
            if found:
                return found
    return None


def node_type(
    raw_type: str,
    *,
    text: dict[str, Any] | None,
    asset: str | None,
    width: float,
    height: float,
    children: list[Any],
) -> str:
    kind = (raw_type or "").lower()
    if text:
        return "text"
    if asset:
        return "icon" if max(width, height) <= 128 else "image"
    if "text" in kind:
        return "text"
    if any(word in kind for word in ("bitmap", "image")):
        return "icon" if max(width, height) <= 128 else "image"
    if children or any(
        word in kind
        for word in ("frame", "group", "instance", "component", "artboard", "canvas", "section")
    ):
        return "container"
    return "shape"


def wrap_document(root: dict[str, Any]) -> dict[str, Any]:
    frame = root["frame"]
    document = {
        "id": root["id"],
        "name": root["name"],
        "type": root["type"],
        "canvas": {"w": frame["w"], "h": frame["h"]},
        "frame": frame,
    }
    for key in ("layout", "fill", "children", "text", "visible", "asset", "radius"):
        if key in root:
            document[key] = root[key]
    return document


def count_nodes(node: dict[str, Any]) -> int:
    return 1 + sum(count_nodes(child) for child in node.get("children") or [])


def navigation(node: dict[str, Any], depth: int = 0) -> list[dict[str, Any]]:
    if depth >= 3:
        return []
    result = []
    for child in (node.get("children") or [])[:30]:
        if not isinstance(child, dict):
            continue
        item = {
            "id": child.get("id"),
            "name": child.get("name"),
            "type": child.get("type"),
            "frame": child.get("frame"),
            "child_count": len(child.get("children") or []),
        }
        nested = navigation(child, depth + 1)
        if nested:
            item["children"] = nested
        result.append(item)
    return result


def write_design_document(document: dict[str, Any], output_file: str) -> Path:
    path = Path(output_file).expanduser()
    if not path.is_absolute():
        raise ValueError("output_file 必须是绝对路径")
    if path.suffix.lower() != ".json":
        raise ValueError("output_file 必须是 .json 文件")
    return atomic_write_text(
        path,
        json.dumps(document, ensure_ascii=False, indent=2),
    )


def slice_asset_map(files: list[tuple[str, str]]) -> dict[str, str]:
    """[(node_id, relative_path), ...] → schema.asset 映射。"""
    mapping: dict[str, str] = {}
    for node_id, path in files:
        mapping[node_id] = path
        mapping[node_id.replace(":", "-")] = path
    return mapping


def attach_assets(node: dict[str, Any], mapping: dict[str, str]) -> None:
    """把切图相对路径写回节点；已有 type=text 的不改类型。"""
    node_id = str(node.get("id") or "")
    path = mapping.get(node_id) or mapping.get(node_id.replace(":", "-"))
    if path:
        node["asset"] = path
        if node.get("type") not in {"text", "image", "icon"}:
            frame = node.get("frame") if isinstance(node.get("frame"), dict) else {}
            width = float(num(frame.get("w"), 0) or 0)
            height = float(num(frame.get("h"), 0) or 0)
            node["type"] = "icon" if max(width, height) <= 128 else "image"
    for child in node.get("children") or []:
        if isinstance(child, dict):
            attach_assets(child, mapping)
