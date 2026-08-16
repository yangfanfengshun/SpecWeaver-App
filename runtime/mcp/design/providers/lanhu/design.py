"""把蓝湖 Sketch 折成统一 schema；切图按蓝湖自己的图层 URL 走。"""
from __future__ import annotations

from typing import Any

from design.schema import (
    compact_num,
    hex_from_rgb,
    navigation,
    node_type,
    num,
    solid_hex,
    wrap_document,
    write_design_document,
)

INLINE_NODE_LIMIT = 120

__all__ = [
    "INLINE_NODE_LIMIT",
    "extract_slice_assets",
    "navigation",
    "normalize_design_document",
    "write_design_document",
]


def _box(
    node: dict[str, Any],
    scale: float = 1,
) -> tuple[float, float, float, float] | None:
    factor = scale if scale else 1
    box = node.get("frame")
    if isinstance(box, dict) and ("left" in box or "width" in box):
        return (
            float(box.get("left") or 0) / factor,
            float(box.get("top") or 0) / factor,
            float(box.get("width") or 0) / factor,
            float(box.get("height") or 0) / factor,
        )
    if node.get("left") is not None or node.get("width") is not None:
        return (
            float(num(node.get("left"), 0) or 0) / factor,
            float(num(node.get("top"), 0) or 0) / factor,
            float(num(node.get("width"), 0) or 0) / factor,
            float(num(node.get("height"), 0) or 0) / factor,
        )
    return None


def _text(node: dict[str, Any]) -> dict[str, Any] | None:
    payload = node.get("text")
    if isinstance(payload, dict):
        style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
        font = style.get("font") if isinstance(style.get("font"), dict) else {}
        content = payload.get("value") or style.get("content")
        if content is None:
            return None
        text: dict[str, Any] = {"content": str(content)}
        size = num(font.get("size"))
        if size is not None:
            text["size"] = compact_num(size)
        weight = num(font.get("fontWeight"))
        if font.get("bold") and (weight is None or weight == 400):
            weight = 700
        if weight is not None:
            text["weight"] = int(weight)
        family = font.get("name")
        if family:
            text["family"] = family
        color = hex_from_rgb(style.get("color") if isinstance(style.get("color"), dict) else None)
        if color:
            text["color"] = color
        return text
    info = node.get("textInfo")
    if not isinstance(info, dict):
        return None
    content = info.get("text")
    if content is None:
        return None
    text = {"content": str(content)}
    size = num(info.get("size", info.get("fontSize")))
    if size is not None:
        text["size"] = compact_num(size)
    if info.get("bold"):
        text["weight"] = 700
    family = info.get("fontName", info.get("fontFamily"))
    if family:
        text["family"] = family
    color = hex_from_rgb(info.get("color") if isinstance(info.get("color"), dict) else None)
    if color:
        text["color"] = color
    return text


def _image_url(node: dict[str, Any]) -> str | None:
    urls = _asset_urls(node)
    return urls[0] if urls else None


def _fully_outside(
    box: tuple[float, float, float, float],
    frame: tuple[float, float, float, float],
) -> bool:
    x, y, w, h = box
    fx, fy, fw, fh = frame
    return x + w < fx or y + h < fy or x > fx + fw or y > fy + fh


def _child_frames_are_local(
    sketch: dict[str, Any],
    root: dict[str, Any],
    box: tuple[float, float, float, float],
    scale: float,
) -> bool:
    """Figma 插件进蓝湖：画板 frame 是页面坐标，子图层是画板相对坐标。"""
    if str(root.get("origin") or "").lower() == "figma":
        return True
    host = (sketch.get("meta") or {}).get("host") if isinstance(sketch.get("meta"), dict) else {}
    if isinstance(host, dict) and "figma" in str(host.get("name") or "").lower():
        return True
    fx, fy, fw, fh = box
    if fx == 0 and fy == 0:
        return False
    local_clip = (0.0, 0.0, fw, fh)
    for child in root.get("children") or root.get("layers") or []:
        if not isinstance(child, dict):
            continue
        child_box = _box(child, scale)
        if child_box is None:
            continue
        return _fully_outside(child_box, box) and not _fully_outside(child_box, local_clip)
    return False


def _origin(
    sketch: dict[str, Any],
    root: dict[str, Any],
    box: tuple[float, float, float, float],
    scale: float,
) -> tuple[float, float]:
    if _child_frames_are_local(sketch, root, box, scale):
        return (0.0, 0.0)
    return (box[0], box[1])


def _scale(sketch: dict[str, Any]) -> float:
    scale = num(
        sketch.get("sliceScale", sketch.get("ArtboardScale", sketch.get("exportScale"))),
        1,
    )
    value = float(scale or 1)
    return value if value > 0 else 1


def _document_root(sketch: dict[str, Any]) -> dict[str, Any]:
    artboard = sketch.get("artboard")
    if isinstance(artboard, dict):
        return artboard
    if isinstance(sketch.get("layers"), list):
        nodes = [node for node in sketch["layers"] if isinstance(node, dict)]
        if nodes:
            return nodes[0]
    info = sketch.get("info")
    if isinstance(info, list):
        nodes = [node for node in info if isinstance(node, dict)]
        artboards = [
            node for node in nodes
            if "artboard" in str(node.get("type") or "").lower()
        ]
        chosen = artboards or nodes
        if chosen:
            return chosen[0]
    raise ValueError("蓝湖结构缺少画板")


def _convert(
    node: dict[str, Any],
    *,
    origin: tuple[float, float],
    assets: dict[str, str],
    is_root: bool = False,
    scale: float = 1,
) -> dict[str, Any]:
    box = _box(node, scale)
    if box is None:
        box = (origin[0], origin[1], 0, 0)
    x, y, w, h = box
    node_id = str(node.get("id") or "")
    asset = assets.get(node_id) or assets.get(node_id.replace(":", "-")) or _image_url(node)
    text = _text(node)
    children: list[dict[str, Any]] = []
    for child in node.get("children") or node.get("layers") or []:
        if not isinstance(child, dict):
            continue
        children.append(
            _convert(child, origin=origin, assets=assets, scale=scale)
        )
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
    style = node.get("style") if isinstance(node.get("style"), dict) else {}
    fill = solid_hex(style.get("fills")) or solid_hex(node.get("fill"))
    if fill and not text:
        result["fill"] = fill
    if asset:
        result["asset"] = asset
    if text:
        result["text"] = text
    if children:
        result["children"] = children
    return result


def document_from_lanhu_sketch(
    sketch: dict[str, Any],
    assets: dict[str, str] | None = None,
) -> dict[str, Any]:
    root = _document_root(sketch)
    scale = _scale(sketch)
    box = _box(root, scale) or (0.0, 0.0, 0.0, 0.0)
    origin = _origin(sketch, root, box, scale)
    converted = _convert(
        root,
        origin=origin,
        assets=assets or {},
        is_root=True,
        scale=scale,
    )
    converted["frame"]["w"] = compact_num(box[2])
    converted["frame"]["h"] = compact_num(box[3])
    return wrap_document(converted)


def _asset_urls(node: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    images = node.get("images")
    if isinstance(images, dict):
        urls.extend(str(value) for value in images.values() if isinstance(value, str))
    image = node.get("image")
    if isinstance(image, dict):
        urls.extend(
            str(image[key])
            for key in ("imageUrl", "svgUrl")
            if isinstance(image.get(key), str)
        )
    dds_image = node.get("ddsImage")
    if isinstance(dds_image, dict) and isinstance(dds_image.get("imageUrl"), str):
        urls.append(str(dds_image["imageUrl"]))
    return list(dict.fromkeys(urls))


def _slice_category(node: dict[str, Any], scale: float) -> str:
    frame = node.get("frame") if isinstance(node.get("frame"), dict) else node
    width = float(num(frame.get("width"), 0) or 0)
    height = float(num(frame.get("height"), 0) or 0)
    if max(width, height) <= 128:
        return "icon"
    width /= scale
    height /= scale
    if max(width, height) <= 128:
        return "icon"
    if width >= 600 or height >= 600:
        return "bg"
    return "img"


def _slice_roots(sketch: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(sketch.get("layers"), list):
        return [node for node in sketch["layers"] if isinstance(node, dict)]
    artboard = sketch.get("artboard")
    if isinstance(artboard, dict) and isinstance(artboard.get("layers"), list):
        return [node for node in artboard["layers"] if isinstance(node, dict)]
    info = sketch.get("info")
    if not isinstance(info, list):
        return []
    nodes = [node for node in info if isinstance(node, dict)]
    artboards = [
        node for node in nodes
        if "artboard" in str(node.get("type") or "").lower()
    ]
    return artboards or nodes


def extract_slice_assets(sketch: dict[str, Any]) -> list[dict[str, Any]]:
    """树上带真实图片 URL 的图层，含 ddsImage；不套 Figma 半屏规则。"""
    roots = _slice_roots(sketch)
    scale = _scale(sketch)
    assets: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    stack = list(reversed(roots))
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        layer_id = str(node.get("id") or "")
        for source_url in _asset_urls(node):
            key = (layer_id, source_url)
            if key in seen:
                continue
            seen.add(key)
            assets.append({
                "layer_id": layer_id,
                "layer_name": str(node.get("name") or ""),
                "source_url": source_url,
                "category": _slice_category(node, scale),
                "source": "fact",
                "status": "available",
            })
        children = node.get("layers", node.get("children", []))
        if isinstance(children, list):
            stack.extend(
                reversed([child for child in children if isinstance(child, dict)])
            )
    return assets


def normalize_design_document(
    source_url: str,
    params: dict[str, str | None],
    detail: dict[str, Any],
    sketch: dict[str, Any],
) -> dict[str, Any]:
    del source_url, params, detail
    return document_from_lanhu_sketch(sketch)
