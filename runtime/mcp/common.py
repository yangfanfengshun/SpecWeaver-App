from __future__ import annotations

import os
from pathlib import Path
import tempfile

from dotenv import dotenv_values
import httpx


CONFIG_KEYS = (
    "TOWER_EMAIL",
    "TOWER_PASSWORD",
    "TOWER_COOKIE",
    "EOLINK_BASE_URL",
    "EOLINK_USER",
    "EOLINK_PASSWORD",
    "LANHU_PHONE",
    "LANHU_PASSWORD",
    "LANHU_COOKIE",
    # glab 直接认这两个环境变量，且优先级高于它自己的配置文件，
    # 所以 sw 命令只需把它们注入环境，不必调 glab auth login 写第二份凭证。
    # GITLAB_HOST 存主机名（不带协议），这是 glab 期望的形式。
    "GITLAB_HOST",
    "GITLAB_TOKEN",
    "APIFOX_TOKEN",
    "FIGMA_TOKEN",
)
SPECWEAVER_HOME = Path(
    os.getenv("SPECWEAVER_HOME", Path.home() / ".specweaver")
).expanduser()
CONFIG_FILE = SPECWEAVER_HOME / ".env"

IMAGE_EXTENSIONS = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
}

# 批量下载/请求时的默认并发上限：既要比串行快，也不能把宿主接口打崩。
DOWNLOAD_CONCURRENCY = 6


class UnsafePathError(ValueError):
    pass


def _allowed_system_symlink(path: Path) -> bool:
    expected = {
        Path("/var"): Path("/private/var"),
        Path("/tmp"): Path("/private/tmp"),
        Path("/etc"): Path("/private/etc"),
    }.get(path)
    return expected is not None and path.resolve() == expected


def unsafe_symlink_components(path: Path) -> list[Path]:
    absolute = path.expanduser().absolute()
    components = [absolute, *absolute.parents]
    return [
        component
        for component in components
        if component.is_symlink() and not _allowed_system_symlink(component)
    ]


def ensure_no_symlink_components(path: Path) -> None:
    unsafe = unsafe_symlink_components(path)
    if unsafe:
        raise UnsafePathError(
            f"路径中不允许符号链接: {', '.join(map(str, unsafe))}"
        )


def read_config() -> dict[str, str]:
    file_values = dotenv_values(CONFIG_FILE) if CONFIG_FILE.is_file() else {}
    return {
        key: str(os.environ.get(key, file_values.get(key) or ""))
        for key in CONFIG_KEYS
    }


def quote_env(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def needs_env_quoting(value: str) -> bool:
    """判断写 .env 时这个值是否必须加引号。

    能不加引号就不加：python-dotenv 解析单引号值用的正则
    `'((?:\\\\'|[^'])*)'` 只会就近判断"反斜杠+引号"是不是一对转义，不区分
    前面到底有几个反斜杠——只要一个值以反斜杠收尾，quote_env 把它转义成
    `...\\\\'` 后，这个正则会把那个反斜杠和收尾引号误判成"转义引号"，
    吞掉真正的收尾引号，一路错读到下一个带引号的字段，导致两个字段的值
    都静默读空、不报错（实测复现：TOWER_COOKIE 以 `\\` 结尾时，它自己和
    紧跟着的 EOLINK_BASE_URL 会一起从 dotenv_values() 的结果里消失）。
    不加引号的值完全不会走这条正则，天然免疫这个问题；只有真正需要引号
    保护的场景（含首尾空白 / 以引号开头 / 含"空白+#"这种会被当成注释的写法）
    才继续加引号——这类值本身也不太可能又恰好以反斜杠收尾，两个条件同时
    命中的概率极低（Rust 侧 `auth.rs::needs_quoting` 用的是同一套判断，
    改这里务必同步改那边，并更新 tests/fixtures/env_quote_cases.json）。
    """
    if value == "":
        return False
    if value != value.strip():
        return True
    if value[:1] in {"'", '"'}:
        return True
    return any(
        value[index] in " \t" and value[index + 1 : index + 2] == "#"
        for index in range(len(value))
    )


def render_env_value(value: str) -> str:
    return quote_env(value) if needs_env_quoting(value) else value


def update_config_atomic(updates: dict[str, str]) -> None:
    values = dotenv_values(CONFIG_FILE) if CONFIG_FILE.is_file() else {}
    merged = {
        key: str(updates.get(key, values.get(key) or ""))
        for key in CONFIG_KEYS
    }
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=".env.",
        dir=CONFIG_FILE.parent,
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for key in CONFIG_KEYS:
                handle.write(f"{key}={render_env_value(merged[key])}\n")
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.chmod(0o600)
        os.replace(temp_path, CONFIG_FILE)
        CONFIG_FILE.chmod(0o600)
    finally:
        if temp_path.exists():
            temp_path.unlink()


# 错误消息里给用户看的平台名。旧插件的 `specweaver configure <platform>` CLI
# 随插件下线了，现在统一指向 App 的设置页。
PLATFORM_LABELS = {
    "tower": "Tower",
    "lanhu": "蓝湖",
    "eolink": "Eolink",
    "gitlab": "GitLab",
    "apifox": "Apifox",
    "figma": "Figma",
}


def manual_cookie_hint(platform: str, key: str) -> str:
    label = PLATFORM_LABELS.get(platform, platform)
    return (
        f"配置文件：{CONFIG_FILE}；请手动填写 {key}，"
        f"或在 SpecWeaver 设置页重新配置{label}"
    )


def http_error_result(
    error: Exception,
    *,
    platform: str,
    status_by_code: dict[int, str],
    default_status: str,
    network_prefix: str = "",
) -> dict[str, str] | None:
    """把 httpx 的两类异常收敛成 `{status, platform, message}`。

    三个平台的错误分类器过去各写一份同样的 isinstance 链，真正的差异只有状态码
    映射表和文案前缀，所以这里只吃 httpx 异常：认不出来就返回 None，交回调用方
    处理它自己特有的那几种（Tower 的会话错误、蓝湖的结构解析失败等）。
    """
    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        return {
            "status": status_by_code.get(code, default_status),
            "platform": platform,
            "message": f"{PLATFORM_LABELS.get(platform, platform)} 返回 HTTP {code}",
        }
    if isinstance(error, httpx.HTTPError):
        return {
            "status": "network_error",
            "platform": platform,
            "message": f"{network_prefix}{error}",
        }
    return None


def parse_strict_bool(value: str, *, default: bool | None = None) -> bool:
    normalized = value.strip().lower()
    if not normalized and default is not None:
        return default
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError("只接受 true 或 false")


def prepare_output_dir(output_dir: str) -> Path:
    path = Path(output_dir).expanduser()
    if not path.is_absolute():
        raise ValueError("output_dir 必须是绝对路径")
    ensure_no_symlink_components(path)
    path.mkdir(parents=True, exist_ok=True)
    ensure_no_symlink_components(path)
    return path.resolve()


def atomic_write_bytes(path: Path, content: bytes, *, mode: int = 0o600) -> Path:
    """二进制原子落盘（图片、切图、附件）。

    直接 `path.write_bytes()` 在写到一半被中断时会留下半截文件，而清单/manifest
    已经把它记成"已下载"，后续校验只看存在性、看不出损坏。
    """
    ensure_no_symlink_components(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_no_symlink_components(path)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.chmod(mode)
        os.replace(temp_path, path)
        path.chmod(mode)
        return path.resolve()
    finally:
        if temp_path.exists():
            temp_path.unlink()


def atomic_write_text(path: Path, content: str, *, mode: int = 0o600) -> Path:
    ensure_no_symlink_components(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_no_symlink_components(path)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            if content and not content.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.chmod(mode)
        os.replace(temp_path, path)
        path.chmod(mode)
        return path.resolve()
    finally:
        if temp_path.exists():
            temp_path.unlink()
