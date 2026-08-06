import string

import pytest

from app.services import credentials
from app.services.steam_parser import extract_code, extract_links, parse_steam_message


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Your verification code is ABC12", "ABC12"),
        ("Steam verification code: 9XY2Z", "9XY2Z"),
        ("The code is  7QW8E ", "7QW8E"),
        ("您的验证码：K3M9P", "K3M9P"),
        ("您的校验码 88XY2", "88XY2"),
        ("no code at all here", None),
    ],
)
def test_extract_code(text: str, expected: str | None) -> None:
    assert extract_code(text) == expected


def test_extract_code_ignores_common_uppercase_words() -> None:
    """兜底模式不能把正文里的普通大写词当成验证码。"""
    assert extract_code("WELCOME TO STEAM PLEAS") is None


def test_extract_code_prefers_context_over_fallback() -> None:
    text = "HELLO ADMIN\nYour verification code is 4RT5Y\nOTHER 8UJ9I"
    assert extract_code(text) == "4RT5Y"


def test_extract_links_dedupes_and_strips_punctuation() -> None:
    text = (
        "Visit https://store.steampowered.com/account/newaccountverification?token=a "
        "or https://store.steampowered.com/account/newaccountverification?token=a. "
        "Also https://help.steampowered.com/en/wizard/HelpWithLogin,"
    )
    links = extract_links(text)
    assert links == [
        "https://store.steampowered.com/account/newaccountverification?token=a",
        "https://help.steampowered.com/en/wizard/HelpWithLogin",
    ]


@pytest.mark.parametrize(
    "text",
    [
        # 钓鱼手法：把 steampowered.com 塞进路径冒充官方域名
        "https://evil.example.com/steampowered.com/x",
        "https://steampowered.com.evil.example.com/x",
        "https://notsteampowered.com/x",
        "http://store.steampowered.com/x",  # 非 https 不收
    ],
)
def test_extract_links_rejects_lookalike_domains(text: str) -> None:
    assert extract_links(text) == []


def test_extract_links_accepts_bare_domain_and_subdomains() -> None:
    assert extract_links("go to https://steampowered.com now") == [
        "https://steampowered.com"
    ]
    assert extract_links("https://store.steampowered.com/join/") == [
        "https://store.steampowered.com/join/"
    ]


def test_parse_handles_none_and_empty() -> None:
    assert parse_steam_message(None).code is None
    assert parse_steam_message("").links == []


def test_generated_password_meets_steam_rules() -> None:
    """Steam 要求 ≥8 字符 + 含大小写 + 含数字。"""
    for _ in range(50):
        pw = credentials.generate_password()
        assert len(pw) == credentials.PASSWORD_LENGTH >= 8
        assert any(c.isupper() for c in pw)
        assert any(c.islower() for c in pw)
        assert any(c.isdigit() for c in pw)


def test_generated_password_contains_no_symbols() -> None:
    """Steam 会接受含符号的密码却在登录时拒绝它，账号交付后才暴露。

    符号并非 Steam 的要求，加了纯属风险。这条断言是防回归的闸门。
    """
    for _ in range(200):
        pw = credentials.generate_password()
        assert pw.isalnum(), f"密码含符号: {pw}"


def test_generate_password_rejects_short_length() -> None:
    with pytest.raises(ValueError):
        credentials.generate_password(4)


def test_generated_email_avoids_ambiguous_characters() -> None:
    for _ in range(50):
        local = credentials.generate_email("x.com").split("@")[0]
        assert not set(local) & set("01lIO")


def test_generated_steam_username_starts_with_letter() -> None:
    for _ in range(50):
        name = credentials.generate_steam_username()
        assert name[0].isalpha()
        assert name.replace("_", "").isalnum()
        assert 3 <= len(name) <= 64


def test_generated_steam_username_charset_is_allowed() -> None:
    """Steam 只接受 a-z A-Z 0-9 和下划线，多一个字符都会被拒。"""
    allowed = set(string.ascii_letters + string.digits + "_")
    for _ in range(200):
        assert set(credentials.generate_steam_username()) <= allowed


def test_generated_steam_username_uses_mixed_case() -> None:
    """200 个样本里必然出现大小写混合，否则说明字符集配错了。"""
    names = [credentials.generate_steam_username() for _ in range(200)]
    assert any(c.isupper() for n in names for c in n)
    assert any(c.islower() for n in names for c in n)
