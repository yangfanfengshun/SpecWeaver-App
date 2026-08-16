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


def figma_image_slices(frame: dict[str, Any]) -> list[dict[str, Any]]:
    """画板内的图片填充图层，排除整页背景；不看 exportSettings。"""
    frame_box = bounding_box(frame)
    if frame_box is None:
        return []
    _, _, fw, fh = frame_box
    max_side = max(fw, fh) * 0.5
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(node: dict[str, Any]) -> None:
        box = bounding_box(node)
        if node is not frame and box and _fully_outside(box, frame_box):
            return
        fills = node.get("fills") or []
        has_image = any(
            isinstance(item, dict)
            and str(item.get("type") or "").upper() == "IMAGE"
            and item.get("visible", True) is not False
            for item in fills
            if isinstance(fills, list)
        )
        if has_image and node is not frame and box is not None:
            width, height = box[2], box[3]
            node_id = str(node.get("id") or "")
            if (
                node_id
                and min(width, height) >= 8
                and max(width, height) <= max_side
                and node_id not in seen
            ):
                seen.add(node_id)
                found.append(node)
        for child in node.get("children") or []:
            if isinstance(child, dict):
                walk(child)

    walk(frame)
    return found
