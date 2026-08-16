#!/usr/bin/env python3
"""Query a SpecWeaver design JSON (unified schema) without loading extras into context."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


def load_design(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"设计文件不存在: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"设计文件不是有效 JSON: {exc}") from None
    if not isinstance(data, dict) or "id" not in data or "frame" not in data:
        raise ValueError("设计文件缺少 id/frame，不是统一 schema")
    return data


def flatten(
    node: dict[str, Any],
    names: list[str] | None = None,
    depth: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    trail = names or []
    name = str(node.get("name") or "")
    node_id = str(node.get("id") or "")
    row = {
        "node": node,
        "id": node_id,
        "name": name,
        "path": " / ".join([*trail, name]) if name or trail else "",
        "depth": depth,
    }
    rows.append(row)
    if node_id:
        by_id[node_id] = row
    for child in node.get("children") or []:
        if isinstance(child, dict):
            child_rows, child_ids = flatten(child, [*trail, name], depth + 1)
            rows.extend(child_rows)
            by_id.update(child_ids)
    return rows, by_id


def compact(row: dict[str, Any]) -> dict[str, Any]:
    node = row["node"]
    result = {
        "id": row["id"],
        "name": row["name"],
        "type": node.get("type"),
        "path": row["path"],
        "depth": row["depth"],
        "frame": node.get("frame"),
    }
    for key in ("layout", "text", "fill", "radius", "asset", "visible"):
        value = node.get(key)
        if value not in (None, False, "", [], {}):
            result[key] = value
        elif key == "visible" and value is False:
            result[key] = False
    return result


def brief(row: dict[str, Any]) -> dict[str, Any]:
    node = row["node"]
    return {
        "id": row["id"],
        "name": row["name"],
        "type": node.get("type"),
        "path": row["path"],
        "frame": node.get("frame"),
    }


def frame_values(row: dict[str, Any]) -> tuple[float, float, float, float] | None:
    frame = row["node"].get("frame")
    if not isinstance(frame, dict):
        return None
    try:
        return tuple(float(frame[key]) for key in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None


def is_visible(row: dict[str, Any]) -> bool:
    return row["node"].get("visible") is not False


def region_hits(
    rows: list[dict[str, Any]], x: float, y: float, width: float, height: float
) -> list[dict[str, Any]]:
    right = x + width
    bottom = y + height
    hits = []
    for row in rows:
        if not is_visible(row):
            continue
        values = frame_values(row)
        if values is None:
            continue
        left, top, node_width, node_height = values
        if left < right and left + node_width > x and top < bottom and top + node_height > y:
            hits.append(row)
    return sorted(hits, key=lambda item: (item["depth"], item["path"]))


def node_result(row: dict[str, Any]) -> dict[str, Any]:
    node = dict(row["node"])
    children = []
    for child in node.pop("children", None) or []:
        if isinstance(child, dict):
            children.append({
                "id": child.get("id"),
                "name": child.get("name"),
                "type": child.get("type"),
                "frame": child.get("frame"),
            })
    return {"node": node, "path": row["path"], "children": children}


def measure(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    first_frame = frame_values(first)
    second_frame = frame_values(second)
    if first_frame is None or second_frame is None:
        raise ValueError("待测节点缺少有效 frame")
    ax, ay, aw, ah = first_frame
    bx, by, bw, bh = second_frame
    horizontal_gap = max(bx - (ax + aw), ax - (bx + bw), 0)
    vertical_gap = max(by - (ay + ah), ay - (by + bh), 0)
    return {
        "from": compact(first),
        "to": compact(second),
        "horizontal_gap": horizontal_gap,
        "vertical_gap": vertical_gap,
        "center_delta": {
            "x": (bx + bw / 2) - (ax + aw / 2),
            "y": (by + bh / 2) - (ay + ah / 2),
        },
        "overlaps": horizontal_gap == 0 and vertical_gap == 0,
    }


def text_blob(node: dict[str, Any]) -> str:
    text = node.get("text")
    if isinstance(text, dict):
        return str(text.get("content") or "")
    return ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("design_file", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("summary")
    search = commands.add_parser("search")
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=20)
    node = commands.add_parser("node")
    node.add_argument("--id", required=True)
    region = commands.add_parser("region")
    region.add_argument("--x", type=float, required=True)
    region.add_argument("--y", type=float, required=True)
    region.add_argument("--w", "--width", dest="width", type=float, required=True)
    region.add_argument("--h", "--height", dest="height", type=float, required=True)
    region.add_argument("--limit", type=int, default=50)
    distance = commands.add_parser("measure")
    distance.add_argument("--from-id", required=True)
    distance.add_argument("--to-id", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        data = load_design(args.design_file)
        rows, by_id = flatten(data)
        if args.command == "summary":
            result = {
                "id": data.get("id"),
                "name": data.get("name"),
                "canvas": data.get("canvas"),
                "children": [
                    brief(row) for row in rows if row["depth"] == 1
                ] or [brief(row) for row in rows if row["depth"] == 0],
            }
        elif args.command == "search":
            query = args.query.casefold()
            matches = [
                row
                for row in rows
                if query in row["name"].casefold()
                or query in text_blob(row["node"]).casefold()
            ]
            result = {
                "query": args.query,
                "total": len(matches),
                "matches": [compact(row) for row in matches[: args.limit]],
            }
        elif args.command == "node":
            row = by_id.get(args.id)
            if row is None:
                raise ValueError(f"未找到节点: {args.id}")
            result = node_result(row)
        elif args.command == "region":
            matches = region_hits(rows, args.x, args.y, args.width, args.height)
            result = {
                "region": {
                    "x": args.x,
                    "y": args.y,
                    "width": args.width,
                    "height": args.height,
                },
                "total": len(matches),
                "matches": [compact(row) for row in matches[: args.limit]],
            }
        else:
            first = by_id.get(args.from_id)
            second = by_id.get(args.to_id)
            if first is None:
                raise ValueError(f"未找到节点: {args.from_id}")
            if second is None:
                raise ValueError(f"未找到节点: {args.to_id}")
            result = measure(first, second)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
