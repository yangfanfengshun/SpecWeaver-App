from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx
from common import manual_cookie_hint, read_config, update_config_atomic
from design.providers.lanhu.auth import LANHU_SUCCESS_CODES, LanhuLoginError, login_lanhu
from session import fingerprint, with_session

Result = TypeVar("Result")
_runtime_cookie = ""
_login_lock: asyncio.Lock | None = None
_login_lock_loop: asyncio.AbstractEventLoop | None = None


class LanhuSessionError(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def lanhu_settings() -> tuple[str, str, str]:
    config = read_config()
    return (
        _runtime_cookie or config["LANHU_COOKIE"],
        config["LANHU_PHONE"],
        config["LANHU_PASSWORD"],
    )


def login_lock() -> asyncio.Lock:
    """返回绑定到当前事件循环的登录锁。

    `asyncio.Lock` 首次 `acquire` 时会绑定到当时的 running loop；模块级单例如果
    跨多个 `asyncio.run()`（例如同进程内先后起了两个新 loop）复用，第二次会抛
    `RuntimeError: ... is bound to a different event loop`。这里按「当前 loop」
    做惰性重建：同一个 loop 内始终拿到同一把锁（互斥有效），loop 变了就换一把新的。
    """
    global _login_lock, _login_lock_loop
    running_loop = asyncio.get_running_loop()
    if _login_lock is None or _login_lock_loop is not running_loop:
        _login_lock = asyncio.Lock()
        _login_lock_loop = running_loop
    return _login_lock


def is_auth_expired_error(error: Exception) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {401, 418}
    return isinstance(error, RuntimeError) and str(error).startswith("auth_expired:")


def is_auth_expired_result(result: Any) -> bool:
    if isinstance(result, dict):
        if result.get("status") == "auth_expired":
            return True
        code = str(result.get("code", ""))
        if code and code not in LANHU_SUCCESS_CODES:
            message = str(result.get("msg") or result.get("message") or "").lower()
            if any(
                marker in message
                for marker in ("登录", "login", "cookie", "token", "认证")
            ):
                return True
        return any(is_auth_expired_result(item) for item in result.get("failures", []))
    if isinstance(result, (list, tuple)):
        return any(is_auth_expired_result(item) for item in result)
    return False


async def refresh_lanhu_session(stale_fingerprint: str) -> None:
    global _runtime_cookie
    async with login_lock():
        current_cookie, _, _ = lanhu_settings()
        if fingerprint(current_cookie) != stale_fingerprint:
            return
        config = read_config()
        account = config["LANHU_PHONE"]
        password = config["LANHU_PASSWORD"]
        if not account or not password:
            raise LanhuSessionError(
                "auth_expired",
                "蓝湖 Cookie 已过期，且未配置手机号/邮箱和密码；"
                + manual_cookie_hint("lanhu", "LANHU_COOKIE"),
            )
        try:
            renewed_cookie = await asyncio.to_thread(
                login_lanhu,
                account,
                password,
            )
        except LanhuLoginError as error:
            if error.kind == "credentials":
                status = "auth_expired"
                message = "蓝湖手机号/邮箱或密码已失效"
            elif error.kind == "locked":
                status = "auth_expired"
                message = "蓝湖账号已被锁定"
            elif error.kind == "verification":
                status = "verification_required"
                message = "蓝湖要求人机验证或手机号认证"
            elif error.kind == "network":
                status = "network_error"
                message = "蓝湖登录网络异常"
            else:
                status = "compatibility_error"
                message = "蓝湖网页登录流程暂时不可用"
            raise LanhuSessionError(
                status,
                f"{message}；{manual_cookie_hint('lanhu', 'LANHU_COOKIE')}",
            ) from error
        update_config_atomic({"LANHU_COOKIE": renewed_cookie})
        _runtime_cookie = renewed_cookie


async def run_with_lanhu_session(
    operation: Callable[[str], Awaitable[Result]],
) -> Result:
    def raise_still_expired(error: BaseException | None) -> None:
        raise LanhuSessionError(
            "auth_expired",
            "蓝湖自动续期后认证仍然失效；"
            + manual_cookie_hint("lanhu", "LANHU_COOKIE"),
        ) from error

    return await with_session(
        operation,
        get_token=lambda: lanhu_settings()[0],
        refresh=refresh_lanhu_session,
        is_expired_error=is_auth_expired_error,
        is_expired_result=is_auth_expired_result,
        raise_still_expired=raise_still_expired,
    )
