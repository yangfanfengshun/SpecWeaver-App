from __future__ import annotations

import hashlib
import json
from typing import Final

import httpx

LOGIN_PATH: Final = "/server/index.php?g=Web&c=Guest&o=login"
SUCCESS_CODE: Final = "000000"
HEADERS: Final = {
    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "X-Requested-With": "XMLHttpRequest",
}


class EolinkLoginError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def login_eolink(
    base_url: str,
    user: str,
    password: str,
    *,
    timeout: float = 30,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """同步登录校验，给设置页 `auth_cli` 用。MCP 会话登录走 client.ensure_login。"""
    base = base_url.strip().rstrip("/")
    account = user.strip()
    if not base or not account or not password:
        raise EolinkLoginError("missing", "请填写 Eolink Base URL、账号和密码")

    try:
        with httpx.Client(
            base_url=base,
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        ) as client:
            client.get("/")
            response = client.post(
                LOGIN_PATH,
                data={
                    "loginName": account,
                    "loginPassword": hashlib.md5(password.encode()).hexdigest(),
                },
                headers={
                    **HEADERS,
                    "Origin": base,
                    "Referer": f"{base}/",
                },
            )
            response.raise_for_status()
            data = response.json()
        status_code = str(data.get("statusCode") or "")
        if status_code != SUCCESS_CODE:
            raise EolinkLoginError(
                "credentials",
                f"Eolink 认证信息无效（状态码 {status_code or '未知'}）",
            )
    except EolinkLoginError:
        raise
    except httpx.HTTPError as error:
        raise EolinkLoginError(
            "network",
            f"Eolink 连接失败: {error.__class__.__name__}",
        ) from error
    except (json.JSONDecodeError, ValueError) as error:
        raise EolinkLoginError(
            "compatibility",
            f"Eolink 响应无法解析: {error}",
        ) from error
