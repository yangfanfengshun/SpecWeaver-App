"""跨平台会话续期骨架。

Tower / 蓝湖（Eolink 除外，见下）的会话续期模式结构完全一样：
用现有凭据先跑一次操作 -> 判断是不是因为认证失效 -> 加锁续期一次 -> 用新凭据
再跑一次 -> 还失效就放弃报错。之前 tower/server.py 的 `request()` 和
lanhu/session.py 的 `run_with_lanhu_session()` 各写了一份完全同构的实现，
差异只在"怎么判断失效"和"怎么续期"这两点，因此把骨架收敛到这里，
两边只需要提供这两个判断/动作函数，其余（何时重试、重试几次、失败怎么报）
统一交给 `with_session`。

Eolink 没有套进来：它的会话模型是"配置一变就主动重新登录"（`ensure_login`
按账号密码指纹判断要不要登录，登录状态活在 httpx.Client 的 cookie jar 里，
不像 Tower/蓝湖那样把 Cookie 值显式读出来传给每次调用），跟这里"先反应式
跑一次、失败了才续期"的模型不是一回事。硬套的话要么改成反应式（会丢失
"配置一变就立刻感知"这个现有行为，属于范围外的行为变更），要么给
`operation` 塞一个用不上的 token 参数硬凑接口——都不划算，就没有强行统一。
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any, NoReturn, TypeVar

Result = TypeVar("Result")


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def with_session(
    operation: Callable[[str], Awaitable[Result]],
    *,
    get_token: Callable[[], str],
    refresh: Callable[[str], Awaitable[None]],
    raise_still_expired: Callable[[BaseException | None], NoReturn],
    is_expired_error: Callable[[BaseException], bool] = lambda _error: False,
    is_expired_result: Callable[[Any], bool] = lambda _result: False,
) -> Result:
    """试跑一次 `operation`，失效了续期一次再重试，还失效就放弃。

    - `operation(token)`：用当前 token（Cookie 等）执行实际调用；token 本身
      要不要用由调用方决定（Tower 的 operation 会忽略它，靠 `get_client()`
      自己按当前配置重建连接；蓝湖的 operation 会直接用它发请求）。
    - `get_token()`：读取当前配置里的 token。
    - `refresh(stale_fingerprint)`：真正的续期动作（加锁、判断是否已被别的
      并发调用续过、登录失败分类、落盘），完全交给调用方实现。
    - `is_expired_error`/`is_expired_result`：分别判断一次调用是因为抛异常
      还是返回值本身，能看出认证已经失效；默认都是"从不判定为失效"，即
      只有真正提供了对应判断函数的那种失效方式才会触发续期重试。
    - `raise_still_expired(error)`：续期后再跑一次仍然失效时调用，各平台在
      这里抛自己的 SessionError 子类（保留原有异常类型，调用方
      `except XxxSessionError` 不用改）；`error` 是触发失效的异常对象
      （结果判定触发时为 `None`），实现里通常 `raise XxxError(...) from error`。
    """
    stale_token = get_token()
    try:
        result = await operation(stale_token)
    except Exception as error:
        if not is_expired_error(error):
            raise
    else:
        if not is_expired_result(result):
            return result

    await refresh(fingerprint(stale_token))
    fresh_token = get_token()
    try:
        result = await operation(fresh_token)
    except Exception as error:
        if not is_expired_error(error):
            raise
        raise_still_expired(error)
        raise
    if is_expired_result(result):
        raise_still_expired(None)
        raise RuntimeError("raise_still_expired 未按约定抛出异常")
    return result
