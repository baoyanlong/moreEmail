"""随机凭据生成。

全部使用 ``secrets`` 而不是 ``random`` —— 后者是可预测的伪随机数生成器，
拿它生成账号密码等于把凭据公开。
"""

from __future__ import annotations

import secrets
import string
from collections.abc import Sequence

# 去掉 0/O/1/l/I：邮箱地址要人工抄写或口述，易混字符会造成实际麻烦
_ADDRESS_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
# Steam 账户名规则：≥3 字符，仅 a-z A-Z 0-9 和下划线
_USERNAME_ALPHABET = string.ascii_letters + string.digits
# 密码只用字母数字，不含任何符号。原因见 generate_password 的说明。
_PASSWORD_ALPHABET = string.ascii_letters + string.digits

ADDRESS_LENGTH = 10
USERNAME_LENGTH = 12
PASSWORD_LENGTH = 16


def generate_email_local_part(length: int = ADDRESS_LENGTH) -> str:
    return "".join(secrets.choice(_ADDRESS_ALPHABET) for _ in range(length))


def pick_domain(domains: Sequence[str]) -> str:
    """从可用域名里随机挑一个。

    用 secrets 而非 random：域名选择虽不是机密，但混用两套随机源容易在
    日后被误改成可预测的实现。
    """
    if not domains:
        raise ValueError("没有可用的邮箱域名")
    return secrets.choice(list(domains))


def generate_email(domain: str, *, length: int = ADDRESS_LENGTH) -> str:
    return f"{generate_email_local_part(length)}@{domain}"


def generate_steam_username(length: int = USERNAME_LENGTH) -> str:
    """Steam 账户名：≥3 字符，仅允许 a-z A-Z 0-9 和下划线。

    首字符固定为字母纯属保守做法，Steam 本身不禁止数字开头。
    """
    first = secrets.choice(string.ascii_letters)
    rest = "".join(secrets.choice(_USERNAME_ALPHABET) for _ in range(length - 1))
    return first + rest


def generate_password(length: int = PASSWORD_LENGTH) -> str:
    """生成含大小写字母和数字的密码，**刻意不含任何符号**。

    Steam 的密码规则只要求「≥8 字符 + 含大小写字母 + 含数字」，符号并非必需。
    而 Steam 会接受含某些符号的密码却在之后的登录中拒绝它 —— 密码设置成功、
    登录一直报错，社区已知 ``£ ( "`` 有问题，``# @ % / \\`` 建议回避，
    ``$`` 会导致 SteamCMD 无法登录。账号交付给别人后才暴露这种问题极难排查，
    而 62^16 ≈ 2^95 的强度对本用途早已绰绰有余，加符号是纯风险无收益。

    先各取一个必需字符再补足长度，最后洗牌 —— 直接随机取样有极小概率
    缺某一类字符而被 Steam 的密码规则拒绝。
    """
    if length < 8:
        raise ValueError("密码长度至少 8 位")
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
    ]
    rest = [secrets.choice(_PASSWORD_ALPHABET) for _ in range(length - len(required))]
    chars = required + rest
    # SystemRandom 的 shuffle 走的也是 secrets 的熵源
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)
