from __future__ import annotations

import base64
import json
import re
from http.cookies import CookieError, SimpleCookie
from typing import Any, Final
from urllib.parse import urljoin, urlparse

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

LANHU_BASE: Final = "https://lanhuapp.com"
LANHU_SSO_URL: Final = f"{LANHU_BASE}/sso/"
LANHU_LOGIN_URL: Final = f"{LANHU_BASE}/api/passport/login"
LANHU_AUTH_URL: Final = f"{LANHU_BASE}/api/auth"
LANHU_REDIRECT_URL: Final = f"{LANHU_BASE}/web"
LANHU_HEADERS: Final = {
    "Accept": "application/json, text/plain, */*",
    "Origin": LANHU_BASE,
    "Referer": f"{LANHU_BASE}/sso/#/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    ),
    "User-From": "lanhu",
    "request-from": "web",
}

# 蓝湖接口 `code` 字段的业务状态码。之前散落在 classify_login_failure 里的裸数字，
# 现在集中在这里命名；session.py、scripts/auth_cli.py 判断登录是否成功时也从
# 这里导入 LANHU_SUCCESS_CODES，不再各自重复一份 {"0", "00000"}。
LANHU_SUCCESS_CODES: Final = frozenset({"0", "00000"})
LANHU_CODE_INVALID_CREDENTIALS: Final = "1302"
LANHU_CODE_ACCOUNT_LOCKED: Final = "1300"
LANHU_CODES_VERIFICATION_REQUIRED: Final = frozenset({"10105", "2401"})
LANHU_CODE_SERVICE_ERROR: Final = "2403"


class LanhuLoginError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def decode_json_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload: Any = response.json()
        for _ in range(2):
            if not isinstance(payload, str):
                break
            payload = json.loads(payload)
    except (json.JSONDecodeError, ValueError) as error:
        raise LanhuLoginError(
            "compatibility",
            "蓝湖登录响应格式已变化",
        ) from error
    if not isinstance(payload, dict):
        raise LanhuLoginError("compatibility", "蓝湖登录响应不是 JSON 对象")
    return payload


def extract_app_bundle_url(html: str) -> str:
    match = re.search(
        r"<script[^>]+src=[\"']((?:[^\"']*/)?app\.[^\"']+\.js)[\"']",
        html,
        re.IGNORECASE,
    )
    if not match:
        raise LanhuLoginError(
            "compatibility",
            "蓝湖登录页结构已变化，无法定位密码加密脚本",
        )
    return urljoin(LANHU_SSO_URL, match.group(1))


def is_lanhu_https_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (
        host == "lanhuapp.com" or host.endswith(".lanhuapp.com")
    )


def extract_public_key(bundle: str) -> str:
    match = re.search(
        r"-----BEGIN RSA PUBLIC KEY-----.*?-----END RSA PUBLIC KEY-----",
        bundle,
        re.DOTALL,
    )
    if not match:
        raise LanhuLoginError(
            "compatibility",
            "蓝湖密码加密方式已变化，无法获取公钥",
        )
    return match.group(0).replace("\\n", "\n")


def encrypt_password(password: str, public_key_pem: str) -> str:
    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode())
        encrypted = public_key.encrypt(password.encode(), padding.PKCS1v15())
    except (TypeError, ValueError) as error:
        raise LanhuLoginError(
            "compatibility",
            "蓝湖密码加密公钥无法解析",
        ) from error
    return base64.b64encode(encrypted).decode()


def cookie_header(client: httpx.Client) -> str:
    request = client.build_request("GET", LANHU_BASE)
    return request.headers.get("cookie", "")


def cookies_from_header(value: str) -> httpx.Cookies:
    cookies = httpx.Cookies()
    if not value:
        return cookies
    parsed = SimpleCookie()
    try:
        parsed.load(value)
    except CookieError as error:
        raise ValueError("LANHU_COOKIE 格式无效") from error
    if not parsed:
        raise ValueError("LANHU_COOKIE 格式无效")
    for name, morsel in parsed.items():
        cookies.set(name, morsel.value, domain=".lanhuapp.com", path="/")
    return cookies


def classify_login_failure(code: Any, message: Any) -> LanhuLoginError:
    normalized_code = str(code)
    text = str(message or "").lower()
    if normalized_code == LANHU_CODE_INVALID_CREDENTIALS or any(
        marker in text for marker in ("密码错误", "账号或密码", "用户不存在")
    ):
        return LanhuLoginError("credentials", "蓝湖手机号/邮箱或密码错误")
    if normalized_code == LANHU_CODE_ACCOUNT_LOCKED or any(
        marker in text for marker in ("账号锁定", "用户锁定", "已锁定")
    ):
        return LanhuLoginError("locked", "蓝湖账号已被锁定")
    if normalized_code in LANHU_CODES_VERIFICATION_REQUIRED or any(
        marker in text for marker in ("验证码", "人机验证", "安全验证", "captcha")
    ):
        return LanhuLoginError("verification", "蓝湖要求人机验证")
    if normalized_code == LANHU_CODE_SERVICE_ERROR:
        return LanhuLoginError("service", "蓝湖登录服务请求异常")
    return LanhuLoginError(
        "compatibility",
        f"蓝湖网页登录流程暂时不可用（错误码 {normalized_code}）",
    )


def login_lanhu(
    phone_or_email: str,
    password: str,
    *,
    timeout: float = 20,
    transport: httpx.BaseTransport | None = None,
) -> str:
    account = phone_or_email.strip()
    if not account or not password:
        raise LanhuLoginError("credentials", "蓝湖手机号/邮箱和密码不能为空")

    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers=LANHU_HEADERS,
            transport=transport,
        ) as client:
            login_page = client.get(LANHU_SSO_URL)
            login_page.raise_for_status()
            bundle_url = extract_app_bundle_url(login_page.text)
            if not is_lanhu_https_url(bundle_url):
                raise LanhuLoginError(
                    "compatibility",
                    "蓝湖密码加密脚本地址不受信任",
                )
            bundle = client.get(bundle_url)
            bundle.raise_for_status()
            encrypted_password = encrypt_password(
                password,
                extract_public_key(bundle.text),
            )
            response = client.post(
                LANHU_LOGIN_URL,
                json={
                    "mobile_or_email": account,
                    "password": encrypted_password,
                },
                headers={"Content-Type": "application/json;charset=UTF-8;"},
            )
            response.raise_for_status()
            payload = decode_json_payload(response)
            code = payload.get("code", payload.get("status"))
            if str(code) not in LANHU_SUCCESS_CODES:
                raise classify_login_failure(
                    code,
                    payload.get("message", payload.get("msg")),
                )
            result = payload.get("data") or payload.get("result")
            if not isinstance(result, dict) or not result.get("ticket"):
                raise LanhuLoginError(
                    "compatibility",
                    "蓝湖登录成功后未返回可用 ticket",
                )
            if result.get("auth_mobile") is False:
                raise LanhuLoginError(
                    "verification",
                    "蓝湖账号需要先完成手机号认证",
                )

            auth = client.get(
                LANHU_AUTH_URL,
                params={
                    "request_from": "web",
                    "sso_ticket": result["ticket"],
                    "redirect_uri": LANHU_REDIRECT_URL,
                    "action": result.get("action", ""),
                    "action_type": result.get("action_type", ""),
                },
            )
            auth.raise_for_status()
            if not is_lanhu_https_url(str(auth.url)):
                raise LanhuLoginError(
                    "compatibility",
                    "蓝湖登录后的跳转目标不受信任",
                )
            value = cookie_header(client)
            if not value:
                raise LanhuLoginError(
                    "compatibility",
                    "蓝湖登录成功后未获得可用 Cookie",
                )
            return value
    except LanhuLoginError:
        raise
    except httpx.HTTPError as error:
        raise LanhuLoginError(
            "network",
            f"蓝湖网络请求失败: {error.__class__.__name__}",
        ) from error
