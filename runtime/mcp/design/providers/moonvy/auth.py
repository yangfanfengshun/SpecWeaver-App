"""月维 JWT 探活。令牌由用户从已登录网页复制，不回传登录响应。"""
from __future__ import annotations

import httpx

from common import read_config

API_BASE = "https://api.moonvy.com"


class MoonvyAuthError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def token_from_config() -> str:
    return str(read_config().get("MOONVY_TOKEN") or "").strip()


def headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "SpecWeaver/1.0",
    }


def verify_token(
    token: str,
    *,
    timeout: float = 20,
    transport: httpx.BaseTransport | None = None,
) -> None:
    value = token.strip()
    if not value:
        raise MoonvyAuthError("missing", "请填写月维 Token")
    try:
        with httpx.Client(
            base_url=API_BASE,
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        ) as client:
            response = client.get("/auth/user-info", headers=headers(value))
        if response.status_code == 401:
            raise MoonvyAuthError(
                "credentials",
                "月维 Token 无效或已过期（约 180 天），请从已登录的月维页面重新复制",
            )
        if response.status_code == 403:
            raise MoonvyAuthError(
                "forbidden",
                "当前月维 Token 没有访问权限",
            )
        if response.status_code >= 400:
            response.raise_for_status()
    except MoonvyAuthError:
        raise
    except httpx.HTTPError as error:
        raise MoonvyAuthError(
            "network",
            f"月维连接失败: {error.__class__.__name__}",
        ) from error
