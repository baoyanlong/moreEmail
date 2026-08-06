"""从 Steam 邮件正文里提取验证码和激活链接。

这里的正则依赖 Steam 的邮件模板，模板一改就会失效。因此调用方**必须**
同时保留原始 ``text``：解析不出来时接口仍然可用，肉眼就能看到验证码，
不至于整条链路瘫痪。所有模式都是模块级常量，改起来只动这一个文件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Steam 的验证码是 5 位大写字母数字。裸匹配 [A-Z0-9]{5} 会命中正文里的
# 普通单词，所以优先用带上下文的模式，全局兜底放在最后。
_CONTEXT_CODE_PATTERNS = [
    re.compile(
        r"(?:verification|confirmation|security)\s+code[^A-Z0-9]{0,20}([A-Z0-9]{5})",
        re.IGNORECASE,
    ),
    re.compile(r"code\s+is[^A-Z0-9]{0,10}([A-Z0-9]{5})", re.IGNORECASE),
    re.compile(r"验证码[^A-Z0-9]{0,10}([A-Z0-9]{5})"),
    re.compile(r"校验码[^A-Z0-9]{0,10}([A-Z0-9]{5})"),
]

_FALLBACK_CODE_RE = re.compile(r"\b([A-Z0-9]{5})\b")

# 全大写的常见词，避免兜底模式把它们当成验证码
_CODE_STOPWORDS = frozenset(
    {
        "STEAM",
        "VALVE",
        "EMAIL",
        "LOGIN",
        "ADMIN",
        "HELLO",
        "ABOUT",
        "PLEAS",
        "THANK",
        "HTTPS",
        "INDEX",
        "TOKEN",
    }
)

# steampowered.com 必须出现在**主机名**位置。写成 [^\s]*steampowered\.com 会让
# https://evil.com/steampowered.com/x 也命中 —— 那正是钓鱼邮件的标准手法。
#
# 结尾的否定断言同样重要：没有它，https://steampowered.com.evil.example.com/x
# 会被截断匹配成 https://steampowered.com，照样能骗过人眼。
_LINK_RE = re.compile(
    r"https://(?:[a-zA-Z0-9-]+\.)*steampowered\.com(?![a-zA-Z0-9.-])"
    r"(?:/[^\s<>\"')]*)?"
)

_MAX_LINKS = 10


@dataclass
class ParsedContent:
    code: str | None = None
    links: list[str] = field(default_factory=list)


def extract_code(text: str) -> str | None:
    """先按上下文找，找不到再退化到全局 5 位匹配。"""
    for pattern in _CONTEXT_CODE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).upper()

    # findall 的返回被标注为 list[Any]，显式收窄以满足 strict 模式
    candidates: list[str] = _FALLBACK_CODE_RE.findall(text)
    for candidate in candidates:
        upper = candidate.upper()
        if upper in _CODE_STOPWORDS:
            continue
        # 纯字母的 5 字符串大概率是普通单词，验证码通常字母数字混合
        if upper.isalpha():
            continue
        return upper
    return None


def extract_links(text: str) -> list[str]:
    """提取 steampowered.com 下的链接，保序去重。"""
    seen: set[str] = set()
    result: list[str] = []
    for raw in _LINK_RE.findall(text):
        link = raw.rstrip(".,;:!?")
        if link in seen:
            continue
        seen.add(link)
        result.append(link)
        if len(result) >= _MAX_LINKS:
            break
    return result


def parse_steam_message(text: str | None) -> ParsedContent:
    if not text:
        return ParsedContent()
    return ParsedContent(code=extract_code(text), links=extract_links(text))
