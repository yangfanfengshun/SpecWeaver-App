"""把月维 genome 画板折成统一 schema。切图只认设计师标过的 slices。"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from design.schema import compact_num, node_type, num, solid_hex, wrap_document

SLICE_FORMATS = ("svg", "max", "base")
_EXTENSIONS = {
    "png": ".png",
    "jpg": ".jpg",
    "jpeg": ".jpg",
    "svg": ".svg",
    "webp": ".webp",
    "gif": ".gif",
}


def _rect(node: dict[str, Any]) -> tuple[float, float, float, float] | None:
    raw = node.get("rect")
    if not isinstance(raw, dict):
        return None
    try:
        return (
            float(raw.get("x") or 0),
            float(raw.get("y") or 0),
            float(raw.get("w") or 0),
            float(raw.get("h") or 0),
        )
    except (TypeError, ValueError):
        return None


def _text(node: dict[str, Any]) -> dict[str, Any] | None:
    box = node.get("textbox")
    if not isinstance(box, dict) or box.get("text") is None:
        return None
    text: dict[str, Any] = {"content": str(box.get("text"))}
    segments = box.get("segments") or []
    segment = segments[0] if segments and isinstance(segments[0], dict) else {}
    size = num(segment.get("fontSize"))
    if size is not None:
        text["size"] = compact_num(size)
    weight = num(segment.get("fontWeight"))
    if weight is not None:
        text["weight"] = int(weight)
    font = segment.get("fontName") if isinstance(segment.get("fontName"), dict) else {}
    family = font.get("family")
    if family:
        text["family"] = family
    color = solid_hex(segment.get("fills"))
    if color:
        text["color"] = color
    return text


def _layout(node: dict[str, Any]) -> dict[str, Any] | None:
    auto = node.get("autoLayout")
    if not isinstance(auto, dict) or auto.get("isAutoLayoutContainer") is not True:
        return None
    direction = auto.get("direction")
    if direction == "HORIZONTAL":
        mode = "row"
    elif direction == "VERTICAL":
        mode = "column"
    else:
        return None
    layout: dict[str, Any] = {"mode": mode}
    gap = num(auto.get("mainAxisGap"))
    if gap:
        layout["gap"] = compact_num(gap)
    padding = auto.get("padding")
    if isinstance(padding, list) and len(padding) == 4:
        mapped: dict[str, Any] = {}
        for side, value in zip(("top", "right", "bottom", "left"), padding, strict=True):
            amount = num(value)
            if amount:
                mapped[side] = compact_num(amount)
        if mapped:
            layout["padding"] = mapped
    return layout


def _convert(
    node: dict[str, Any],
    *,
    origin: tuple[float, float],
    assets: dict[str, str],
    is_root: bool = False,
) -> dict[str, Any] | None:
    rect = _rect(node)
    if rect is None:
        return None
    local_x, local_y, width, height = rect
    x = 0.0 if is_root else origin[0] + local_x
    y = 0.0 if is_root else origin[1] + local_y
    node_id = str(node.get("id") or "")
    asset = assets.get(node_id)
    text = _text(node)
    children: list[dict[str, Any]] = []
    for child in node.get("children") or []:
        if not isinstance(child, dict):
            continue
        converted = _convert(child, origin=(x, y), assets=assets)
        if converted:
            children.append(converted)
    result: dict[str, Any] = {
        "id": node_id,
        "name": str(node.get("name") or ""),
        "type": node_type(
            str(node.get("type") or ""),
            text=text,
            asset=asset,
            width=width,
            height=height,
            children=children,
        ),
        "frame": {
            "x": compact_num(x if not is_root else 0),
            "y": compact_num(y if not is_root else 0),
            "w": compact_num(width),
            "h": compact_num(height),
        },
    }
    layout = _layout(node)
    if layout:
        result["layout"] = layout
    fill = solid_hex(node.get("fills"))
    if fill and not text:
        result["fill"] = fill
    radius = num(node.get("borderRadius"))
    if radius:
        result["radius"] = compact_num(radius)
    if asset:
        result["asset"] = asset
    if text:
        result["text"] = text
    if children:
        result["children"] = children
    return result


def document_from_moonvy(
    page: dict[str, Any],
    assets: dict[str, str] | None = None,
) -> dict[str, Any]:
    converted = _convert(page, origin=(0, 0), assets=assets or {}, is_root=True)
    if converted is None:
        raise ValueError("月维画板缺少 rect")
    return wrap_document(converted)


def artboard_from_genome(genome: dict[str, Any]) -> dict[str, Any]:
    pages = [page for page in genome.get("pages") or [] if isinstance(page, dict)]
    if len(pages) != 1:
        raise ValueError("这份月维设计图不是单页画板")
    return pages[0]


def _extension(image: dict[str, Any], url: str) -> str:
    kind = str(image.get("type") or "").lower().lstrip(".")
    if kind in _EXTENSIONS:
        return _EXTENSIONS[kind]
    path = urlparse(url).path.lower()
    for ext in (".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return ".png"


def moonvy_slices(page: dict[str, Any], images: dict[str, Any]) -> list[dict[str, Any]]:
    """只下载节点上标了 slices 的图层。优先 svg，其次 max，再 base。"""
    found: list[dict[str, Any]] = []

    def walk(node: dict[str, Any]) -> None:
        slices = node.get("slices")
        picked: dict[str, Any] | None = None
        if isinstance(slices, dict):
            for key in SLICE_FORMATS:
                info = slices.get(key)
                if not isinstance(info, dict):
                    continue
                image_id = str(info.get("id") or "")
                image = images.get(image_id)
                if not isinstance(image, dict):
                    continue
                url = str(image.get("url") or "")
                if not url:
                    continue
                picked = {
                    "layer_id": str(node.get("id") or image_id),
                    "layer_name": str(node.get("name") or "slice"),
                    "url": url,
                    "extension": _extension(image, url),
                }
                break
        if picked and picked["layer_id"]:
            found.append(picked)
        for child in node.get("children") or []:
            if isinstance(child, dict):
                walk(child)

    walk(page)
    return found
