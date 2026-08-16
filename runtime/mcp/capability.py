"""能力包的 provider 发现与路由（requirement / design / apidoc 共用）。

每个能力包的目录形状：`<capability>/providers/<name>/` 一个目录一个平台。
公共部分**不点名任何 provider**：靠扫目录发现，靠 `claims_url` 认领链接。
加平台 = 加目录，删平台 = 删目录，公共代码零改动——这是既定的隔离契约，
不要在能力包里写 `from ..providers.figma import ...` 这种死引用绕过它。

provider 契约：`providers/<name>/__init__.py` 必须暴露

- `PLATFORM: str` — 平台名（与认证配置、返回体里的 platform 字段一致）
- `claims_url(url: str) -> bool` — 这条链接归不归我

其余接口按能力各自约定（见各能力 core.py 的调用点）。
"""
from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

MCP_ROOT = Path(__file__).resolve().parent


def discover_providers(capability: str) -> dict[str, ModuleType]:
    """扫 `<capability>/providers/` 得到 {目录名: provider 模块}。

    不满足契约的目录直接抛错而不是跳过：静默跳过意味着「平台列不出来
    也不报错」，正是这个仓库最难察觉的那类失败。
    """
    providers_dir = MCP_ROOT / capability / "providers"
    result: dict[str, ModuleType] = {}
    if not providers_dir.is_dir():
        return result
    for child in sorted(providers_dir.iterdir()):
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        module = importlib.import_module(
            f"{capability}.providers.{child.name}"
        )
        if not callable(getattr(module, "claims_url", None)):
            raise RuntimeError(
                f"provider {capability}/providers/{child.name} 缺少 claims_url，"
                "不符合 provider 契约"
            )
        result[child.name] = module
    return result


def resolve_provider(
    providers: dict[str, ModuleType],
    url: str = "",
) -> tuple[str, ModuleType] | None:
    """按链接找认领的 provider。

    显式给了链接时必须由 `claims_url` 认领；没人认领就返回 None。
    只有调用本身不带 URL（例如查认证状态）且当前恰好只有一家时，
    才可以兜底给它。
    """
    if url:
        for name, module in providers.items():
            if module.claims_url(url):
                return name, module
        return None
    if len(providers) == 1:
        return next(iter(providers.items()))
    return None


def discover_capabilities(exclude: frozenset[str] | set[str] = frozenset()) -> list[str]:
    """列出带 providers/ 目录的能力包（按名字排序，保证问询顺序稳定）。"""
    return sorted(
        child.name
        for child in MCP_ROOT.iterdir()
        if child.is_dir()
        and (child / "providers").is_dir()
        and child.name not in exclude
        and not child.name.startswith(("_", "."))
    )


def discover_source_adapters(
    exclude: frozenset[str] | set[str] = frozenset({"requirement"}),
) -> dict[str, ModuleType]:
    """发现可由需求编排层收集的能力适配器。

    能力目录只要增加 `source.py` 并实现统一接口，就会自动进入
    候选、scope、收集、清单和验证流程；requirement 核心不再点名能力。
    """
    required = {
        "LABEL",
        "discover_candidates",
        "suggest_scope",
        "normalize_scope",
        "collect",
        "managed_paths",
        "cache_paths",
        "clean",
        "verify",
        "unresolved",
        "count",
    }
    result: dict[str, ModuleType] = {}
    for capability in discover_capabilities(exclude=exclude):
        source_file = MCP_ROOT / capability / "source.py"
        if not source_file.is_file():
            continue
        module = importlib.import_module(f"{capability}.source")
        missing = sorted(
            name for name in required
            if not hasattr(module, name)
        )
        if missing:
            raise RuntimeError(
                f"能力 {capability} 的 source.py 缺少接口: "
                + ", ".join(missing)
            )
        result[capability] = module
    return result


def capability_claims(capability: str, url: str) -> bool:
    """问一个能力包「这条链接你们吃不吃」——任一 provider 认领即算认领。"""
    return any(
        module.claims_url(url)
        for module in discover_providers(capability).values()
    )


def classify_links(
    urls: list[str],
    *,
    exclude: frozenset[str] | set[str] = frozenset(),
) -> dict[str, list[str]]:
    """把链接按能力分桶：谁的 provider 认领归谁，没人认领的进 "other"。

    key 是动态发现的能力名 + "other"，没有手抄清单：新能力建目录后自动多一个
    桶；拿掉某个 provider 后它原本认领的链接自动落回 "other"——不报错，也不
    静默丢链接（"other" 会原样出现在 raw.md 的来源线索里）。
    """
    providers_by_capability = {
        capability: discover_providers(capability)
        for capability in discover_source_adapters(exclude=exclude)
    }
    result: dict[str, list[str]] = {
        capability: [] for capability in providers_by_capability
    }
    result["other"] = []
    for url in urls:
        owner = next(
            (
                capability
                for capability, providers in providers_by_capability.items()
                if any(module.claims_url(url) for module in providers.values())
            ),
            "other",
        )
        result[owner].append(url)
    return result
