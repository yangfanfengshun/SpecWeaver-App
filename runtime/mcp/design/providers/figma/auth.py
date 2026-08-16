"""Figma Personal Access Token 探活。"""
from __future__ import annotations

import httpx

from common import read_config

API_BASE = "https://api.figma.com"


class FigmaAuthError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def token_from_config() -> str:
    return str(read_config().get("FIGMA_TOKEN") or "").strip()


def headers(token: str) -> dict[str, str]:
    return {
        "X-Figma-Token": token,
        "User-Agent": "SpecWeaver/1.0",
    }


def verify_token(
    token: str,
    *,
    timeout: float = 20,
    transport: httpx.BaseTransport | None = None,
) -> str:
    value = token.strip()
    if not value:
        raise FigmaAuthError("missing", "请填写 Figma Token")
    try:
        with httpx.Client(
            base_url=API_BASE,
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        ) as client:
            response = client.get("/v1/me", headers=headers(value))
        if response.status_code == 401:
            raise FigmaAuthError(
                "credentials",
                "Figma Token 无效或已过期（个人令牌最长 90 天），请重新生成后再粘贴",
            )
        if response.status_code == 403:
            raise FigmaAuthError(
                "forbidden",
                "当前 Token 没有权限，请确认文件已分享或 Token 权限足够",
            )
        if response.status_code >= 400:
            response.raise_for_status()
        payload = response.json()
        handle = str(payload.get("handle") or payload.get("email") or "").strip()
        return handle
    except FigmaAuthError:
        raise
    except httpx.HTTPError as error:
        raise FigmaAuthError(
            "network",
            f"Figma 连接失败: {error.__class__.__name__}",
        ) from error
    except (ValueError, KeyError) as error:
        raise FigmaAuthError(
            "compatibility",
            f"Figma 响应无法解析: {error}",
        ) from error
