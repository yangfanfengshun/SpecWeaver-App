"""把 Figma REST 节点折成统一 schema；切图按 Figma 自己的规矩走。"""
from __future__ import annotations

from typing import Any

from design.schema import (
    compact_num,
    node_type,
    num,
    solid_hex,
    wrap_document,
)


def _layout(node: dict[str, Any]) -> dict[str, Any] | None:
    mode = node.get("layoutMode")
    if mode not in {"HORIZONTAL", "VERTICAL"}:
        return None
    layout: dict[str, Any] = {"mode": "row" if mode == "HORIZONTAL" else "column"}
    gap = num(node.get("itemSpacing"))
    if gap:
        layout["gap"] = compact_num(gap)
    padding: dict[str, Any] = {}
    for side, key in (
        ("top", "paddingTop"),
        ("right", "paddingRight"),
        ("bottom", "paddingBottom"),
        ("left", "paddingLeft"),
    ):
        value = num(node.get(key))
        if value:
            padding[side] = compact_num(value)
    if padding:
        layout["padding"] = padding
    return layout


def _text(node: dict[str, Any]) -> dict[str, Any] | None:
    content = node.get("characters")
    if content is None:
        return None
    style = node.get("style") if isinstance(node.get("style"), dict) else {}
    text: dict[str, Any] = {"content": str(content)}
    size = num(style.get("fontSize"))
    if size is not None:
        text["size"] = compact_num(size)
    weight = num(style.get("fontWeight"))
    if weight is not None:
        text["weight"] = int(weight)
    family = style.get("fontFamily")
    if family:
        text["family"] = family
    color = solid_hex(node.get("fills"))
    if color:
        text["color"] = color
    return text


def bounding_box(node: dict[str, Any]) -> tuple[float, float, float, float] | None:
    box = node.get("absoluteBoundingBox")
    if not isinstance(box, dict):
        return None
    try:
        return (
            float(box["x"]),
            float(box["y"]),
            float(box["width"]),
            float(box["height"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _fully_outside(
    box: tuple[float, float, float, float],
    frame: tuple[float, float, float, float],
) -> bool:
    x, y, w, h = box
    fx, fy, fw, fh = frame
    return x + w < fx or y + h < fy or x > fx + fw or y > fy + fh


def _convert(
    node: dict[str, Any],
    *,
    origin: tuple[float, float],
    assets: dict[str, str],
    is_root: bool = False,
    clip_box: tuple[float, float, float, float] | None = None,
) -> dict[str, Any] | None:
    box = bounding_box(node)
    if box is None:
        box = (origin[0], origin[1], 0, 0)
    if clip_box and not is_root and _fully_outside(box, clip_box):
        return None
    x, y, w, h = box
    node_id = str(node.get("id") or "")
    asset = assets.get(node_id) or assets.get(node_id.replace(":", "-"))
    text = _text(node)
    children: list[dict[str, Any]] = []
    for child in node.get("children") or []:
        if not isinstance(child, dict):
            continue
        converted = _convert(
            child,
            origin=origin,
            assets=assets,
            clip_box=clip_box,
        )
        if converted:
            children.append(converted)
    result: dict[str, Any] = {
        "id": node_id,
        "name": str(node.get("name") or ""),
        "type": node_type(
            str(node.get("type") or ""),
            text=text,
            asset=asset,
            width=w,
            height=h,
            children=children,
        ),
        "frame": {
            "x": compact_num(0 if is_root else x - origin[0]),
            "y": compact_num(0 if is_root else y - origin[1]),
            "w": compact_num(w),
            "h": compact_num(h),
        },
    }
    if node.get("visible") is False:
        result["visible"] = False
    layout = _layout(node)
    if layout:
        result["layout"] = layout
    fill = solid_hex(node.get("fills"))
    if fill and not text:
        result["fill"] = fill
    radius = num(node.get("cornerRadius"))
    if radius:
        result["radius"] = compact_num(radius)
    if asset:
        result["asset"] = asset
    if text:
        result["text"] = text
    if children:
        result["children"] = children
    return result


def document_from_figma(
    frame: dict[str, Any],
    assets: dict[str, str] | None = None,
) -> dict[str, Any]:
    box = bounding_box(frame)
    if box is None:
        raise ValueError("Figma 节点缺少 absoluteBoundingBox")
    origin = (box[0], box[1])
    converted = _convert(
        frame,
        origin=origin,
        assets=assets or {},
        is_root=True,
        clip_box=box,
    )
    if converted is None:
        raise ValueError("Figma 画板无法归一化")
    return wrap_document(converted)


VECTOR_LEAVES = frozenset({
    "VECTOR",
    "BOOLEAN_OPERATION",
    "STAR",
    "LINE",
    "ELLIPSE",
    "REGULAR_POLYGON",
    "SLICE",
})
ICON_CONTAINERS = frozenset({
    "GROUP",
    "FRAME",
    "COMPONENT",
    "INSTANCE",
    "BOOLEAN_OPERATION",
    "COMPONENT_SET",
})
GENERIC_VECTOR_NAMES = frozenset({"vector", "path", "ellipse", "star", "line", "shape"})
CHROME_NAMES = (
    "状态栏",
    "tab栏",
    "底部tab",
    "status bar",
    "statusbar",
    "tab bar",
    "tabbar",
    "home indicator",
)
MAX_ICON_SIDE = 128


def _visible(node: dict[str, Any]) -> bool:
    return node.get("visible", True) is not False


def _has_image_fill(node: dict[str, Any]) -> bool:
    fills = node.get("fills") or []
    if not isinstance(fills, list):
        return False
    return any(
        isinstance(item, dict)
        and str(item.get("type") or "").upper() == "IMAGE"
        and item.get("visible", True) is not False
        for item in fills
    )


def _has_export_settings(node: dict[str, Any]) -> bool:
    settings = node.get("exportSettings") or []
    return isinstance(settings, list) and bool(settings)


def _name_looks_like_icon(name: str) -> bool:
    lowered = name.strip().lower()
    if not lowered or lowered in GENERIC_VECTOR_NAMES:
        return False
    return lowered == "icon" or lowered.startswith("icon") or "icon_" in lowered


def _size_ok(box: tuple[float, float, float, float], max_side: float) -> bool:
    width, height = box[2], box[3]
    return min(width, height) >= 8 and max(width, height) <= max_side


def _full_bleed(box: tuple[float, float, float, float], frame_width: float) -> bool:
    return frame_width > 0 and box[2] >= frame_width * 0.9


def _is_chrome(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in CHROME_NAMES)


def _vector_only(node: dict[str, Any]) -> bool:
    if str(node.get("type") or "") == "TEXT":
        return False
    if _has_image_fill(node):
        return False
    children = [child for child in (node.get("children") or []) if isinstance(child, dict)]
    if not children:
        return str(node.get("type") or "") in VECTOR_LEAVES
    return all(_vector_only(child) for child in children)


def figma_image_slices(frame: dict[str, Any]) -> list[dict[str, Any]]:
    """画板内可渲染切图：图片填充、SLICE、图标名、小矢量编组。

    父节点入选后不再切子 path。跳过画板外节点、整页背景、状态栏/Tab。
    """
    frame_box = bounding_box(frame)
    if frame_box is None:
        return []
    _, _, frame_w, frame_h = frame_box
    image_max = max(frame_w, frame_h) * 0.5
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def consider(node: dict[str, Any]) -> bool:
        if node is frame or not _visible(node):
            return False
        box = bounding_box(node)
        if box is None:
            return False
        name = str(node.get("name") or "")
        node_type = str(node.get("type") or "")
        if _has_image_fill(node):
            return _size_ok(box, image_max)
        if _full_bleed(box, frame_w):
            return False
        if node_type == "SLICE" and _size_ok(box, image_max):
            return True
        if _has_export_settings(node) and _size_ok(box, image_max):
            return True
        if _name_looks_like_icon(name) and _size_ok(box, MAX_ICON_SIDE):
            return True
        return (
            node_type in ICON_CONTAINERS
            and _vector_only(node)
            and _size_ok(box, MAX_ICON_SIDE)
        )

    def walk(node: dict[str, Any], ancestor_taken: bool) -> None:
        if node is not frame:
            if not _visible(node) or _is_chrome(str(node.get("name") or "")):
                return
            box = bounding_box(node)
            if box and _fully_outside(box, frame_box):
                return
        taken = ancestor_taken
        node_id = str(node.get("id") or "")
        if not ancestor_taken and consider(node) and node_id and node_id not in seen:
            seen.add(node_id)
            found.append(node)
            taken = True
        for child in node.get("children") or []:
            if isinstance(child, dict):
                walk(child, taken)

    walk(frame, False)
    return found
