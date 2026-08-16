from __future__ import annotations

from html.parser import HTMLParser
from http.cookies import CookieError, SimpleCookie
import re
from typing import Final

import httpx


TOWER_BASE: Final = "https://tower.im"
TOWER_LOGIN_URL: Final = f"{TOWER_BASE}/users/account_sign_in"
TOWER_CHECK_URL: Final = f"{TOWER_BASE}/launchpad/"
TOWER_HEADERS: Final = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36"
    ),
}


class TowerLoginError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


class _CsrfParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.token = ""

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() != "meta":
            return
        values = {name.lower(): value or "" for name, value in attrs}
        if values.get("name", "").lower() == "csrf-token":
            self.token = values.get("content", "")


def extract_csrf_token(html: str) -> str:
    parser = _CsrfParser()
    parser.feed(html)
    if not parser.token:
        raise TowerLoginError(
            "compatibility",
            "Tower 登录页结构已变化，无法获取 CSRF token",
        )
    return parser.token


def is_login_response(response: httpx.Response) -> bool:
    path = response.url.path.lower()
    sample = response.text[:10000].lower()
    return (
        "/users/account_sign_in" in path
        or "/login" in path
        or "/sign_in" in path
        or (
            re.search(r"type\s*=\s*['\"]password['\"]", sample) is not None
            and "account_sign_in" in sample
        )
    )


def _classify_login_failure(response: httpx.Response) -> TowerLoginError | None:
    sample = response.text[:20000].lower()
    credential_markers = (
        "登录邮箱或密码错误",
        "邮箱或密码错误",
        "email or password",
        '"target":"password"',
        '"target": "password"',
    )
    verification_markers = (
        "验证码",
        "二次验证",
        "两步验证",
        "two-factor",
        "two factor",
        "captcha",
        "otp",
    )
    if any(marker in sample for marker in credential_markers):
        return TowerLoginError("credentials", "Tower 登录邮箱或密码错误")
    if any(marker in sample for marker in verification_markers):
        return TowerLoginError(
            "verification",
            "Tower 要求验证码或二次验证",
        )
    return None


def cookie_header(client: httpx.Client) -> str:
    request = client.build_request("GET", TOWER_BASE)
    return request.headers.get("cookie", "")


def cookies_from_header(value: str) -> httpx.Cookies:
    cookies = httpx.Cookies()
    if not value:
        return cookies
    parsed = SimpleCookie()
    try:
        parsed.load(value)
    except CookieError as error:
        raise ValueError("TOWER_COOKIE 格式无效") from error
    if not parsed:
        raise ValueError("TOWER_COOKIE 格式无效")
    for name, morsel in parsed.items():
        cookies.set(name, morsel.value, domain=".tower.im", path="/")
    return cookies


def login_tower(
    email: str,
    password: str,
    *,
    validation_url: str = TOWER_CHECK_URL,
    timeout: float = 20,
    transport: httpx.BaseTransport | None = None,
) -> str:
    if not email.strip() or not password:
        raise TowerLoginError("credentials", "Tower 邮箱和密码不能为空")

    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers=TOWER_HEADERS,
            transport=transport,
        ) as client:
            login_page = client.get(TOWER_LOGIN_URL)
            login_page.raise_for_status()
            csrf_token = extract_csrf_token(login_page.text)
            response = client.post(
                TOWER_LOGIN_URL,
                data={
                    "email": email.strip(),
                    "password": password,
                    "remember_me": "1",
                    "authenticity_token": csrf_token,
                },
                headers={
                    "Accept": (
                        "text/javascript, application/javascript, "
                        "application/ecmascript, */*; q=0.5"
                    ),
                    "Content-Type": (
                        "application/x-www-form-urlencoded; charset=UTF-8"
                    ),
                    "Origin": TOWER_BASE,
                    "Referer": TOWER_LOGIN_URL,
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            classified = _classify_login_failure(response)
            if classified:
                raise classified
            if response.status_code >= 500:
                raise TowerLoginError(
                    "service",
                    f"Tower 登录服务返回 HTTP {response.status_code}",
                )

            validation = client.get(validation_url)
            classified = _classify_login_failure(validation)
            if classified:
                raise classified
            if is_login_response(validation):
                raise TowerLoginError(
                    "verification",
                    "Tower 登录后仍要求人工验证",
                )
            if validation.status_code >= 500:
                raise TowerLoginError(
                    "service",
                    f"Tower 验证服务返回 HTTP {validation.status_code}",
                )

            value = cookie_header(client)
            if not value:
                raise TowerLoginError(
                    "compatibility",
                    "Tower 登录成功后未获得可用 Cookie",
                )
            return value
    except TowerLoginError:
        raise
    except httpx.HTTPError as error:
        raise TowerLoginError(
            "network",
            f"Tower 网络请求失败: {error.__class__.__name__}",
        ) from error
