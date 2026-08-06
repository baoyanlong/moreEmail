"""同步 IMAP 读取实现。

移植自参考项目 steamgenerateaccount，去掉了其线程池/信号量包装 —— FastAPI
的同步路由本身就跑在线程池里，直接调用即可。

安全与资源边界：

* 始终使用 ``ssl.create_default_context()`` 校验证书链和主机名，
  没有任何关闭校验的开关。
* 连接超时 5 秒，连接后对 Socket 设置读写超时，保证调用一定会结束。
* 两阶段拉取：先只取元数据，再对选中的邮件做有界正文拉取。
* 单封上限、单请求总量上限都在拉取过程中实时递减。
"""

from __future__ import annotations

import email
import html as html_module
import imaplib
import logging
import re
import ssl
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email import policy
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any

import nh3

logger = logging.getLogger(__name__)

_UID_RE = re.compile(rb"UID (\d+)")
_SIZE_RE = re.compile(rb"RFC822\.SIZE (\d+)")
_INTERNALDATE_RE = re.compile(rb'INTERNALDATE "([^"]+)"')
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_HEADER_FETCH_BYTES = 16 * 1024
_METADATA_BATCH = 50


class ImapError(RuntimeError):
    """IMAP 交互失败。消息里不包含密码或邮件内容。"""


class ImapAuthError(ImapError):
    """认证失败。"""


class ImapTimeout(ImapError):
    """连接或读取超时。"""


@dataclass
class AttachmentInfo:
    id: str
    filename: str | None
    content_type: str
    size: int | None


@dataclass
class MessageInfo:
    id: str
    uid: int
    from_: list[dict[str, str]]
    to: list[dict[str, str]]
    subject: str
    received_at: datetime
    size: int
    text: str | None = None
    html: str | None = None
    attachments: list[AttachmentInfo] = field(default_factory=list)
    truncated: bool = False
    body_loaded: bool = True


@dataclass(frozen=True)
class FetchLimits:
    minutes: int
    limit: int
    max_candidates: int = 200
    max_message_bytes: int = 10 * 1024 * 1024
    max_body_bytes_per_message: int = 1024 * 1024
    max_fetch_bytes_per_request: int = 20 * 1024 * 1024
    connect_timeout: float = 5.0
    read_timeout: float = 10.0
    return_html: bool = False


def _clean_text(value: str | None) -> str:
    """去掉 CR/LF 和控制字符，避免日志或响应注入。"""
    if not value:
        return ""
    return _CONTROL_CHARS.sub("", value.replace("\r", " ").replace("\n", " ")).strip()


def _decode_header_value(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return _clean_text(str(make_header(decode_header(raw))))
    except UnicodeDecodeError, LookupError, ValueError:
        # 单封坏邮件不能让整个列表失败。
        return _clean_text(raw)


def _parse_addresses(message: Message[str, str], header: str) -> list[dict[str, str]]:
    raw_values = message.get_all(header, [])
    if not raw_values:
        return []
    result: list[dict[str, str]] = []
    for name, address in getaddresses([str(v) for v in raw_values])[:20]:
        result.append(
            {"name": _decode_header_value(name), "address": _clean_text(address)[:320]}
        )
    return result


def _decode_payload(part: Message[str, str], limit: int) -> str:
    try:
        payload = part.get_payload(decode=True)
    except Exception:
        # 异常 MIME 结构不应中断整封解析。
        return ""
    if not payload or not isinstance(payload, bytes | bytearray):
        return ""
    raw = bytes(payload)[:limit]
    charset = part.get_content_charset()
    # 声明的字符集优先，失败后按常见编码降级，最后用替换字符兜底。
    for candidate in [charset, "utf-8", "gbk", "latin-1"]:
        if not candidate:
            continue
        try:
            return raw.decode(candidate, errors="strict")
        except UnicodeDecodeError, LookupError:
            continue
    return raw.decode("utf-8", errors="replace")


def _build_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _connect(
    host: str, port: int, timeout: float, read_timeout: float
) -> imaplib.IMAP4_SSL:
    try:
        conn = imaplib.IMAP4_SSL(
            host, port, ssl_context=_build_ssl_context(), timeout=timeout
        )
    except TimeoutError as exc:
        raise ImapTimeout("IMAP 连接超时") from exc
    except (OSError, ssl.SSLError) as exc:
        raise ImapError("IMAP 连接失败") from exc
    try:
        conn.sock.settimeout(read_timeout)
    except OSError:  # pragma: no cover - 极端情况下 socket 已失效
        pass
    return conn


def _logout_quietly(conn: imaplib.IMAP4_SSL | None) -> None:
    """无论成功失败都尝试 logout 并关闭 Socket，防止连接泄漏。"""
    if conn is None:
        return
    try:
        conn.logout()
    except Exception:
        logger.debug("imap logout failed", exc_info=False)
    finally:
        try:
            conn.shutdown()
        except Exception:
            logger.debug("imap shutdown failed", exc_info=False)


def verify_login(
    host: str, port: int, address: str, password: str, *, timeout: float = 5.0
) -> bool:
    """只做认证检查，不读取任何邮件内容。"""
    conn = None
    try:
        conn = _connect(host, port, timeout, timeout)
        conn.login(address, password)
        return True
    except imaplib.IMAP4.error:
        return False
    except TimeoutError as exc:
        raise ImapTimeout("IMAP 认证超时") from exc
    finally:
        _logout_quietly(conn)


def fetch_recent_messages(
    *,
    host: str,
    port: int,
    address: str,
    password: str,
    limits: FetchLimits,
    now: datetime | None = None,
) -> list[MessageInfo]:
    """两阶段拉取最近 ``limits.minutes`` 分钟的邮件。"""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(minutes=limits.minutes)
    conn = None
    try:
        conn = _connect(host, port, limits.connect_timeout, limits.read_timeout)
        try:
            conn.login(address, password)
        except imaplib.IMAP4.error as exc:
            raise ImapAuthError("IMAP 认证失败") from exc

        status, _ = conn.select("INBOX", readonly=True)
        if status != "OK":
            raise ImapError("无法选择 INBOX")

        uids = _search_candidates(conn, cutoff, limits.max_candidates)
        if not uids:
            return []

        candidates = _fetch_metadata(conn, uids, cutoff, limits)
        candidates.sort(key=lambda m: (m.received_at, m.uid), reverse=True)
        selected = candidates[: limits.limit]
        _fetch_bodies(conn, selected, limits)
        return selected
    except TimeoutError as exc:
        raise ImapTimeout("IMAP 读取超时") from exc
    except imaplib.IMAP4.abort as exc:
        raise ImapError("IMAP 连接中断") from exc
    except OSError as exc:
        raise ImapError("IMAP 网络错误") from exc
    finally:
        _logout_quietly(conn)


def fetch_raw_messages_for(
    *,
    host: str,
    port: int,
    address: str,
    password: str,
    recipient: str,
    days: int = 7,
    max_messages: int = 50,
    max_message_bytes: int = 10 * 1024 * 1024,
    connect_timeout: float = 5.0,
    read_timeout: float = 10.0,
) -> list[tuple[bytes, datetime]]:
    """取出投递给 ``recipient`` 的原始邮件字节，用于 APPEND 搬运到新邮箱。

    返回 ``(原始 RFC822 字节, 收信时间)``。搬运必须保留原始字节，
    任何重新编码都可能破坏签名和 MIME 结构。
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    target = recipient.strip().lower()
    conn = None
    results: list[tuple[bytes, datetime]] = []
    try:
        conn = _connect(host, port, connect_timeout, read_timeout)
        try:
            conn.login(address, password)
        except imaplib.IMAP4.error as exc:
            raise ImapAuthError("IMAP 认证失败") from exc

        status, _ = conn.select("INBOX", readonly=True)
        if status != "OK":
            raise ImapError("无法选择 INBOX")

        # 服务端先按收件人粗筛，再在应用层精确比对（TO 是子串匹配，不可信）。
        search_status, data = conn.uid(
            "SEARCH", "SINCE", cutoff.strftime("%d-%b-%Y"), "TO", target
        )
        if search_status != "OK" or not data or not data[0]:
            return []
        uids = sorted((int(item) for item in data[0].split()), reverse=True)

        for uid in uids[:max_messages]:
            fetch_status, payload = conn.uid("FETCH", str(uid), "(INTERNALDATE RFC822)")
            if fetch_status != "OK" or not payload:
                continue
            raw = b""
            internaldate = datetime.now(UTC)
            for item in payload:
                if isinstance(item, tuple) and len(item) >= 2:
                    prefix, body = item[0], item[1]
                    if isinstance(body, bytes | bytearray):
                        raw = bytes(body)
                    if isinstance(prefix, bytes):
                        match = _INTERNALDATE_RE.search(prefix)
                        if match:
                            parsed = _parse_internaldate(
                                match.group(1).decode("ascii", "replace")
                            )
                            if parsed is not None:
                                internaldate = parsed
            if not raw or len(raw) > max_message_bytes:
                continue
            if not _is_addressed_to(raw, target):
                continue
            results.append((raw, internaldate))
        return results
    except TimeoutError as exc:
        raise ImapTimeout("IMAP 读取超时") from exc
    except imaplib.IMAP4.abort as exc:
        raise ImapError("IMAP 连接中断") from exc
    except OSError as exc:
        raise ImapError("IMAP 网络错误") from exc
    finally:
        _logout_quietly(conn)


def _is_addressed_to(raw: bytes, target: str) -> bool:
    """精确比对 To/Cc/Delivered-To，挡掉 IMAP TO 子串匹配的误命中。"""
    try:
        parsed = email.message_from_bytes(raw, policy=policy.default)
    except Exception:
        return False
    for header in ("To", "Cc", "Delivered-To", "X-Original-To"):
        for entry in _parse_addresses(parsed, header):
            if entry["address"].lower() == target:
                return True
    return False


def append_message(
    *,
    host: str,
    port: int,
    address: str,
    password: str,
    raw_message: bytes,
    received_at: datetime | None = None,
    connect_timeout: float = 5.0,
    read_timeout: float = 10.0,
) -> None:
    """把一封原始邮件写入目标邮箱的 INBOX，保留原收信时间。"""
    conn = None
    try:
        conn = _connect(host, port, connect_timeout, read_timeout)
        try:
            conn.login(address, password)
        except imaplib.IMAP4.error as exc:
            raise ImapAuthError("IMAP 认证失败") from exc
        stamp = imaplib.Time2Internaldate(
            (received_at or datetime.now(UTC)).timestamp()
        )
        status, _ = conn.append("INBOX", "\\Seen", stamp, raw_message)
        if status != "OK":
            raise ImapError("APPEND 失败")
    except TimeoutError as exc:
        raise ImapTimeout("IMAP 写入超时") from exc
    except imaplib.IMAP4.abort as exc:
        raise ImapError("IMAP 连接中断") from exc
    except OSError as exc:
        raise ImapError("IMAP 网络错误") from exc
    finally:
        _logout_quietly(conn)


def delete_messages_before(
    *,
    host: str,
    port: int,
    address: str,
    password: str,
    cutoff: datetime,
    connect_timeout: float = 5.0,
    read_timeout: float = 30.0,
) -> int:
    """删除 ``cutoff`` 之前的邮件并 EXPUNGE，返回删除条数。"""
    conn = None
    try:
        conn = _connect(host, port, connect_timeout, read_timeout)
        try:
            conn.login(address, password)
        except imaplib.IMAP4.error as exc:
            raise ImapAuthError("IMAP 认证失败") from exc

        status, _ = conn.select("INBOX")
        if status != "OK":
            raise ImapError("无法选择 INBOX")

        # BEFORE 只精确到日期，宁可少删也不能误删当天的新邮件。
        search_status, data = conn.uid("SEARCH", "BEFORE", cutoff.strftime("%d-%b-%Y"))
        if search_status != "OK" or not data or not data[0]:
            return 0
        uids = [item.decode("ascii") for item in data[0].split()]
        if not uids:
            return 0
        for start in range(0, len(uids), 200):
            batch = ",".join(uids[start : start + 200])
            conn.uid("STORE", batch, "+FLAGS", "(\\Deleted)")
        conn.expunge()
        return len(uids)
    except TimeoutError as exc:
        raise ImapTimeout("IMAP 清理超时") from exc
    except imaplib.IMAP4.abort as exc:
        raise ImapError("IMAP 连接中断") from exc
    except OSError as exc:
        raise ImapError("IMAP 网络错误") from exc
    finally:
        _logout_quietly(conn)


def _search_candidates(
    conn: imaplib.IMAP4_SSL, cutoff: datetime, max_candidates: int
) -> list[int]:
    # SINCE 只精确到日期，这里往前多取一天，真正的分钟级过滤在应用层完成。
    since = (cutoff - timedelta(days=1)).strftime("%d-%b-%Y")
    status, data = conn.uid("SEARCH", "SINCE", since)
    if status != "OK" or not data or not data[0]:
        return []
    uids = [int(item) for item in data[0].split()]
    uids.sort(reverse=True)
    return uids[:max_candidates]


def _fetch_metadata(
    conn: imaplib.IMAP4_SSL, uids: list[int], cutoff: datetime, limits: FetchLimits
) -> list[MessageInfo]:
    results: list[MessageInfo] = []
    for start in range(0, len(uids), _METADATA_BATCH):
        batch = uids[start : start + _METADATA_BATCH]
        query = (
            f"(UID INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER]<0.{_HEADER_FETCH_BYTES}>)"
        )
        status, data = conn.uid("FETCH", ",".join(str(u) for u in batch), query)
        if status != "OK" or not data:
            continue
        for item in data:
            parsed = _parse_metadata_item(item, cutoff, limits)
            if parsed is not None:
                results.append(parsed)
    return results


def _parse_metadata_item(
    item: Any, cutoff: datetime, limits: FetchLimits
) -> MessageInfo | None:
    if not isinstance(item, tuple) or len(item) < 2:
        return None
    prefix, raw_header = item[0], item[1]
    if not isinstance(prefix, bytes) or not isinstance(raw_header, bytes | bytearray):
        return None

    uid_match = _UID_RE.search(prefix)
    if uid_match is None:
        return None
    uid = int(uid_match.group(1))

    size_match = _SIZE_RE.search(prefix)
    size = int(size_match.group(1)) if size_match else 0

    date_match = _INTERNALDATE_RE.search(prefix)
    received_at: datetime | None = None
    if date_match:
        received_at = _parse_internaldate(
            date_match.group(1).decode("ascii", "replace")
        )
    if received_at is None:
        return None
    # 应用层按 UTC 做精确的分钟级过滤。
    if received_at < cutoff:
        return None

    try:
        header_msg = email.message_from_bytes(bytes(raw_header), policy=policy.default)
    except Exception:
        return None

    oversized = size > limits.max_message_bytes
    return MessageInfo(
        id=f"imap-uid-{uid}",
        uid=uid,
        from_=_parse_addresses(header_msg, "From"),
        to=_parse_addresses(header_msg, "To"),
        subject=_decode_header_value(header_msg.get("Subject")),
        received_at=received_at,
        size=size,
        body_loaded=not oversized,
    )


def _parse_internaldate(raw: str) -> datetime | None:
    """INTERNALDATE -> 带 UTC 时区的 datetime。

    ``Internaldate2tuple`` 返回本地时区的 ``struct_time``，``mktime`` 把它
    还原成 epoch 秒，再按 UTC 还原成 datetime，全过程不依赖运行环境时区。
    """
    try:
        parsed = imaplib.Internaldate2tuple(f'INTERNALDATE "{raw}"'.encode())
        if parsed is not None:
            return datetime.fromtimestamp(time.mktime(parsed), tz=UTC)
    except ValueError, OverflowError, OSError:
        pass
    try:
        return parsedate_to_datetime(raw).astimezone(UTC)
    except TypeError, ValueError:
        return None


def _fetch_bodies(
    conn: imaplib.IMAP4_SSL, messages: list[MessageInfo], limits: FetchLimits
) -> None:
    budget = limits.max_fetch_bytes_per_request
    for message in messages:
        if not message.body_loaded:
            # 超过单封上限：只返回元数据，不拉取正文。
            continue
        if budget <= 0:
            message.body_loaded = False
            message.truncated = True
            continue
        want = min(
            message.size or limits.max_body_bytes_per_message,
            limits.max_body_bytes_per_message,
            budget,
        )
        if want <= 0:
            message.body_loaded = False
            message.truncated = True
            continue
        status, data = conn.uid("FETCH", str(message.uid), f"(BODY.PEEK[]<0.{want}>)")
        if status != "OK" or not data:
            message.body_loaded = False
            continue
        raw = b""
        for item in data:
            if (
                isinstance(item, tuple)
                and len(item) >= 2
                and isinstance(item[1], bytes | bytearray)
            ):
                raw = bytes(item[1])
                break
        if not raw:
            message.body_loaded = False
            continue
        budget -= len(raw)
        message.truncated = len(raw) < (message.size or len(raw))
        _populate_body(message, raw, limits)


def _populate_body(message: MessageInfo, raw: bytes, limits: FetchLimits) -> None:
    try:
        parsed = email.message_from_bytes(raw, policy=policy.default)
    except Exception:
        message.body_loaded = False
        return

    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[AttachmentInfo] = []
    part_index = 0

    for part in parsed.walk():
        if part.is_multipart():
            continue
        part_index += 1
        content_type = (
            _clean_text(part.get_content_type()) or "application/octet-stream"
        )
        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        if disposition == "attachment" or (
            filename and not content_type.startswith("text/")
        ):
            payload = part.get_payload(decode=True)
            attachments.append(
                AttachmentInfo(
                    id=f"part-{part_index}",
                    # 文件名只作为展示元数据，绝不用于拼接服务器路径。
                    filename=_decode_header_value(filename)[:255] or None,
                    content_type=content_type,
                    size=len(payload) if isinstance(payload, bytes) else None,
                )
            )
            continue
        if content_type == "text/plain" and len(text_parts) < 5:
            text_parts.append(_decode_payload(part, limits.max_body_bytes_per_message))
        elif content_type == "text/html" and len(html_parts) < 5:
            html_parts.append(_decode_payload(part, limits.max_body_bytes_per_message))

    message.attachments = attachments[:50]
    text_value = "\n".join(p for p in text_parts if p).strip()
    html_value = "\n".join(p for p in html_parts if p).strip()

    if text_value:
        message.text = text_value[: limits.max_body_bytes_per_message]
    elif html_value:
        # 没有纯文本部件时，用清理后的 HTML 退化出可读文本。
        message.text = _html_to_text(html_value)[: limits.max_body_bytes_per_message]

    if limits.return_html and html_value:
        message.html = sanitize_html(html_value)[: limits.max_body_bytes_per_message]


def sanitize_html(raw_html: str) -> str:
    """白名单清理：移除脚本、表单、事件属性、远程资源和危险协议。"""
    return nh3.clean(
        raw_html,
        tags={
            "a",
            "b",
            "blockquote",
            "br",
            "code",
            "div",
            "em",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "hr",
            "i",
            "li",
            "ol",
            "p",
            "pre",
            "span",
            "strong",
            "table",
            "tbody",
            "td",
            "th",
            "thead",
            "tr",
            "u",
            "ul",
        },
        attributes={"a": {"href", "title"}, "*": {"title"}},
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
    )


def _html_to_text(raw_html: str) -> str:
    cleaned = sanitize_html(raw_html)
    text = re.sub(r"<br\s*/?>", "\n", cleaned, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return _CONTROL_CHARS.sub("", html_module.unescape(text)).strip()


__all__ = [
    "AttachmentInfo",
    "FetchLimits",
    "ImapAuthError",
    "ImapError",
    "ImapTimeout",
    "MessageInfo",
    "append_message",
    "delete_messages_before",
    "fetch_raw_messages_for",
    "fetch_recent_messages",
    "sanitize_html",
    "verify_login",
]
