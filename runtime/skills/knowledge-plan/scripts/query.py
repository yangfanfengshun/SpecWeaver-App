#!/usr/bin/env python3
"""对当前仓库 .knowledge/ 做 tag 精确命中。不认项目名，不分端。不读原文。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CATALOG_FILES = ("catalog-stack.json", "catalog-project.json")


def find_git_root(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def resolve_knowledge_dir(cwd: Path, explicit: Path | None) -> Path | None:
    if explicit is not None:
        target = explicit.expanduser().resolve()
        if (target / "catalog-stack.json").is_file():
            return target
        nested = target / ".knowledge"
        if (nested / "catalog-stack.json").is_file():
            return nested
        return None

    current = cwd.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        knowledge = candidate / ".knowledge"
        if (knowledge / "catalog-stack.json").is_file():
            return knowledge
        if (candidate / "catalog-stack.json").is_file() and candidate.name == ".knowledge":
            return candidate
    return None


def load_cards(knowledge_dir: Path) -> tuple[list[dict], list[str], list[str]]:
    cards: list[dict] = []
    catalogs: list[str] = []
    missing: list[str] = []
    for filename in CATALOG_FILES:
        path = knowledge_dir / filename
        if not path.is_file():
            missing.append(filename)
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        catalog_id = payload.get("id", filename)
        layer = payload.get("layer", "")
        catalogs.append(f"{catalog_id}({layer})")
        for card in payload.get("cards", []):
            cards.append({**card, "catalog": catalog_id, "layer": card.get("layer", layer)})
    return cards, catalogs, missing


def query(cards: list[dict], terms: list[str]) -> dict:
    matched = []
    for card in cards:
        tags = card.get("tags") or []
        hits = [tag for tag in tags if tag in terms]
        if not hits:
            continue
        matched.append(
            {
                "id": card.get("id"),
                "name": card.get("name"),
                "layer": card.get("layer"),
                "catalog": card.get("catalog"),
                "tags": tags,
                "hits": hits,
                "score": len(hits),
                "use_when": card.get("use_when", ""),
                "use_when_not": card.get("use_when_not", ""),
                "source": card.get("source", ""),
                "paths": card.get("paths") or [],
                "constraints": card.get("constraints") or [],
            }
        )
    matched.sort(key=lambda item: (-item["score"], item["layer"] or "", item["id"] or ""))
    hit_tags = {tag for item in matched for tag in item["hits"]}
    missed = [term for term in terms if term not in hit_tags]
    return {"matched": matched, "missed": missed}


def emit(payload: dict, as_json: bool) -> None:
    if as_json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    if payload["status"] == "no_knowledge":
        print("知识库: 无")
        print("去摸代码，不要空编。")
        return
    if payload["status"] == "incomplete":
        print(f"知识库: {payload['knowledge']}")
        print("缺目录文件:", ", ".join(payload["missing_catalogs"]))
        return

    print(f"知识库: {payload['knowledge']}")
    print(f"目录: {', '.join(payload['catalogs'])}")
    print(f"查询词: {payload['terms'] or '（空）'}")
    if not payload["matched"]:
        print("命中: 无，去摸代码")
    else:
        print("命中:")
        for card in payload["matched"]:
            print(
                f"- [{card['layer']}/{card['catalog']}] {card['id']}  {card['name']}  "
                f"命中tag={card['hits']}"
            )
            print(f"  何时用: {card['use_when']}")
            print(f"  约束: {'; '.join(card['constraints'])}")
            print(f"  原文: {card['source']}")
            if card["paths"]:
                print(f"  路径: {', '.join(card['paths'])}")
    if payload["missed"]:
        print(f"缺口: {payload['missed']}")
    else:
        print("缺口: 无")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="读取当前仓库 .knowledge 的两份 catalog，按 tag 精确命中。不输出原文。"
    )
    parser.add_argument("terms", nargs="*", help="要从 catalog 勾出的 tag，精确匹配")
    parser.add_argument(
        "--cwd",
        default=".",
        help="从该目录向上找 Git 根和 .knowledge，默认当前目录",
    )
    parser.add_argument(
        "--knowledge",
        help="直接指定 .knowledge 目录，或含 catalog-stack.json 的目录（样本测试用）",
    )
    parser.add_argument(
        "--text",
        action="store_true",
        help="打印可读文本；默认 JSON",
    )
    args = parser.parse_args()

    cwd = Path(args.cwd)
    knowledge = resolve_knowledge_dir(
        cwd, Path(args.knowledge) if args.knowledge else None
    )
    as_json = not args.text

    if knowledge is None:
        git_root = find_git_root(cwd)
        emit(
            {
                "status": "no_knowledge",
                "cwd": str(cwd.resolve()),
                "git_root": str(git_root) if git_root else None,
                "terms": args.terms,
                "matched": [],
                "missed": args.terms,
                "message": "没有 .knowledge/catalog-stack.json，去摸代码，不要空编",
            },
            as_json,
        )
        return 0

    cards, catalogs, missing = load_cards(knowledge)
    if missing:
        emit(
            {
                "status": "incomplete",
                "knowledge": str(knowledge),
                "missing_catalogs": missing,
                "terms": args.terms,
                "matched": [],
                "missed": args.terms,
            },
            as_json,
        )
        return 0

    if not args.terms:
        emit(
            {
                "status": "error",
                "knowledge": str(knowledge),
                "catalogs": catalogs,
                "terms": [],
                "matched": [],
                "missed": [],
                "message": "先读 catalog 勾 tag，再传给本脚本。不要把需求整句当 tag。",
            },
            as_json,
        )
        return 2

    result = query(cards, args.terms)
    emit(
        {
            "status": "ok",
            "knowledge": str(knowledge),
            "catalogs": catalogs,
            "terms": args.terms,
            **result,
        },
        as_json,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
