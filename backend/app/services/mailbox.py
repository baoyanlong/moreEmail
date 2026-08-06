"""邮箱业务逻辑：catch-all 读信、保存时真实创建邮箱并搬运历史邮件。

架构要点（catch-all 兜底 + 保存时才真建）：

* ``/generate`` 只产出随机字符串，此时地址在 Mailcow 里并不存在，
  来信由 catch-all 规则兜进 ``CATCHALL_ADDRESS``。
* ``/messages`` 登 catch-all 账号读信，再按收件人精确过滤。
* 保存时才调 Mailcow 建真实邮箱。Postfix 精确匹配优先于 catch-all，
  之后来信直接进独立邮箱，账号可以整套转让给别人。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.clients import imap
from app.clients.mailcow import MailcowClient, MailcowDuplicate
from app.core.config import settings
from app.models import MailAddress, MailAttachment, MailMessage
from app.services.steam_parser import parse_steam_message

logger = logging.getLogger(__name__)


class MailboxServiceError(RuntimeError):
    """邮件服务不可用或未配置。"""


def _require_imap_host() -> str:
    """登任何邮箱都要先有服务器地址，与用哪套凭据无关。"""
    if not settings.IMAP_HOST:
        raise MailboxServiceError("邮件服务器未配置（IMAP_HOST）")
    return settings.IMAP_HOST


def _require_catchall() -> tuple[str, str]:
    if not settings.catchall_enabled:
        raise MailboxServiceError(
            "catch-all 邮箱未配置（IMAP_HOST / CATCHALL_ADDRESS / CATCHALL_PASSWORD）"
        )
    return settings.CATCHALL_ADDRESS, settings.CATCHALL_PASSWORD


def build_mailcow_client() -> MailcowClient:
    if not settings.mailcow_enabled:
        raise MailboxServiceError(
            "Mailcow 未配置（MAILCOW_BASE_URL / MAILCOW_API_KEY）"
        )
    return MailcowClient(
        settings.MAILCOW_BASE_URL,
        settings.MAILCOW_API_KEY,
        disable_outbound=settings.MAILBOX_DISABLE_OUTBOUND,
    )


def _to_mail_message(info: imap.MessageInfo) -> MailMessage:
    parsed = parse_steam_message(info.text)
    return MailMessage(
        id=info.id,
        sender=[MailAddress(**entry) for entry in info.from_],
        to=[MailAddress(**entry) for entry in info.to],
        subject=info.subject,
        received_at=info.received_at,
        size=info.size,
        text=info.text,
        html=info.html,
        attachments=[
            MailAttachment(
                id=a.id,
                filename=a.filename,
                content_type=a.content_type,
                size=a.size,
            )
            for a in info.attachments
        ],
        truncated=info.truncated,
        body_loaded=info.body_loaded,
        extracted_code=parsed.code,
        extracted_links=parsed.links,
    )


def _addressed_to(info: imap.MessageInfo, target: str) -> bool:
    return any(entry.get("address", "").lower() == target for entry in info.to)


def read_messages(
    *, email: str, minutes: int, limit: int
) -> tuple[list[MailMessage], datetime]:
    """从 catch-all 兜底邮箱里读出投递给 ``email`` 的最近邮件。

    用于**尚未保存**的邮箱。邮箱一旦保存（在 Mailcow 里真实创建），新邮件
    就直接进它自己的箱子、不再经过 catch-all，那种情况要用
    ``read_messages_from_own_mailbox``。分支判断由路由层按库里有无记录决定。
    """
    target = email.strip().lower()
    window_start = datetime.now(UTC) - timedelta(minutes=minutes)
    limits = imap.FetchLimits(
        minutes=minutes,
        limit=limit,
        return_html=settings.MAIL_RETURN_HTML,
    )

    catchall_address, catchall_password = _require_catchall()
    try:
        infos = imap.fetch_recent_messages(
            host=settings.IMAP_HOST,
            port=settings.IMAP_PORT,
            address=catchall_address,
            password=catchall_password,
            limits=limits,
        )
    except imap.ImapError as exc:
        raise MailboxServiceError(str(exc)) from exc

    messages = [_to_mail_message(i) for i in infos if _addressed_to(i, target)]
    return messages, window_start


def read_messages_from_own_mailbox(
    *, email: str, password: str, minutes: int, limit: int
) -> tuple[list[MailMessage], datetime]:
    """直接登目标邮箱读信，用于已保存（已真实创建）的邮箱。"""
    host = _require_imap_host()
    window_start = datetime.now(UTC) - timedelta(minutes=minutes)
    limits = imap.FetchLimits(
        minutes=minutes,
        limit=limit,
        return_html=settings.MAIL_RETURN_HTML,
    )
    try:
        infos = imap.fetch_recent_messages(
            host=host,
            port=settings.IMAP_PORT,
            address=email,
            password=password,
            limits=limits,
        )
    except imap.ImapError as exc:
        raise MailboxServiceError(str(exc)) from exc
    return [_to_mail_message(i) for i in infos], window_start


def provision_mailbox(*, email: str, password: str) -> None:
    """在 Mailcow 里真实创建邮箱。已存在视为成功（幂等）。"""
    client = build_mailcow_client()
    try:
        client.create_mailbox(email, password, settings.MAILBOX_QUOTA_BYTES)
    except MailcowDuplicate:
        logger.info("mailbox already exists, treating as success")


def migrate_history(*, email: str, password: str) -> int:
    """把 catch-all 里投递给 ``email`` 的历史邮件搬进它自己的邮箱。

    返回搬运成功的封数。这一步是锦上添花，调用方应当吞掉异常 ——
    邮箱此时已经创建成功，不能因为搬信失败就让整个保存操作回滚。
    """
    if settings.MAIL_MIGRATE_LOOKBACK_DAYS <= 0:
        return 0
    catchall_address, catchall_password = _require_catchall()

    raw_messages = imap.fetch_raw_messages_for(
        host=settings.IMAP_HOST,
        port=settings.IMAP_PORT,
        address=catchall_address,
        password=catchall_password,
        recipient=email,
        days=settings.MAIL_MIGRATE_LOOKBACK_DAYS,
    )
    migrated = 0
    for raw, received_at in raw_messages:
        try:
            imap.append_message(
                host=settings.IMAP_HOST,
                port=settings.IMAP_PORT,
                address=email,
                password=password,
                raw_message=raw,
                received_at=received_at,
            )
            migrated += 1
        except imap.ImapError:
            # 单封失败不影响其余邮件
            logger.warning("failed to migrate one message", exc_info=False)
    return migrated
