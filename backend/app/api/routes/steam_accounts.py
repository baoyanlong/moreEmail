"""Steam 账号 + 临时邮箱接口。

流程：``/generate`` 拿凭据（不落库）→ 手动去 Steam 注册 → ``/messages``
读验证码 → 注册成功后 ``POST /`` 保存（此时才真建邮箱并落库）。

全部接口用固定的 X-API-Key 鉴权，不接 User 体系。
"""

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import col, func, select

from app import crud
from app.api.deps import ApiKeyDep, SessionDep
from app.core.config import settings
from app.models import (
    GeneratedCredentials,
    MailMessagesPublic,
    Message,
    SteamAccount,
    SteamAccountCreate,
    SteamAccountDetail,
    SteamAccountPublic,
    SteamAccountsPublic,
)
from app.services import credentials, mailbox

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/steam-accounts", tags=["steam-accounts"])


def _validate_domain(email: str) -> None:
    """挡住不受管辖的域名。

    Mailcow 本来也会拒绝为未托管的域名建邮箱，但那时报的是难懂的 502，
    在这里拦下来能给出明确的 400。
    """
    domain = email.rpartition("@")[2].lower()
    if domain not in settings.mail_domains:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的邮箱域名 {domain}，可用域名：{', '.join(settings.mail_domains)}",
        )


@router.post("/generate", response_model=GeneratedCredentials)
def generate_credentials(
    _: ApiKeyDep,
    domain: Annotated[str | None, Query(max_length=255)] = None,
) -> Any:
    """生成一组随机邮箱和 Steam 凭据。

    纯计算：不调 Mailcow、不落库。调用方拿去注册，成功了再调保存接口，
    没成功就什么痕迹都不留。

    不传 ``domain`` 时从 ``MAIL_DOMAINS`` 里随机挑一个。
    """
    if domain:
        chosen = domain.strip().lower()
        if chosen not in settings.mail_domains:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的邮箱域名 {chosen}，可用域名：{', '.join(settings.mail_domains)}",
            )
    else:
        chosen = credentials.pick_domain(settings.mail_domains)

    return GeneratedCredentials(
        email=credentials.generate_email(chosen),
        email_password=credentials.generate_password(),
        steam_username=credentials.generate_steam_username(),
        steam_password=credentials.generate_password(),
    )


@router.get("/messages", response_model=MailMessagesPublic)
def read_mailbox_messages(
    _: ApiKeyDep,
    session: SessionDep,
    email: Annotated[str, Query(max_length=255)],
    minutes: Annotated[int, Query(ge=1)] = settings.MAIL_DEFAULT_WINDOW_MINUTES,
    limit: Annotated[int, Query(ge=1)] = settings.MAIL_DEFAULT_LIMIT,
) -> Any:
    """读取该邮箱最近 N 分钟的邮件，并尝试提取验证码和激活链接。"""
    minutes = min(minutes, settings.MAIL_MAX_WINDOW_MINUTES)
    limit = min(limit, settings.MAIL_MAX_LIMIT)
    target = email.strip().lower()

    # 已保存的邮箱有自己的箱子，新邮件不再进 catch-all，必须直连它本身。
    saved = crud.get_steam_account_by_email(session=session, email=target)
    try:
        if saved is not None:
            messages, window_start = mailbox.read_messages_from_own_mailbox(
                email=saved.email,
                password=saved.email_password,
                minutes=minutes,
                limit=limit,
            )
        else:
            messages, window_start = mailbox.read_messages(
                email=target, minutes=minutes, limit=limit
            )
    except mailbox.MailboxServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return MailMessagesPublic(
        email=target,
        window_start=window_start,
        data=messages,
        count=len(messages),
    )


@router.post("/", response_model=SteamAccountDetail)
def create_steam_account(
    _: ApiKeyDep, session: SessionDep, account_in: SteamAccountCreate
) -> Any:
    """保存注册成功的账号：真实创建邮箱 → 搬运历史邮件 → 落库。

    顺序不能反。先落库再建邮箱的话，一旦建邮箱失败就会留下一条指向
    不存在邮箱的记录，而这条记录还占着 email 的唯一索引。
    """
    email = account_in.email.strip().lower()
    _validate_domain(email)

    if crud.get_steam_account_by_email(session=session, email=email):
        raise HTTPException(status_code=409, detail="该邮箱已保存")
    if crud.get_steam_account_by_username(
        session=session, steam_username=account_in.steam_username
    ):
        raise HTTPException(status_code=409, detail="该 Steam 账号已保存")

    try:
        mailbox.provision_mailbox(email=email, password=account_in.email_password)
    except mailbox.MailboxServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("failed to create mailbox in mailcow")
        raise HTTPException(status_code=502, detail="邮箱创建失败") from exc

    # 邮箱已经建好，搬信只是锦上添花，失败不能让保存整体回滚。
    try:
        migrated = mailbox.migrate_history(
            email=email, password=account_in.email_password
        )
        logger.info("migrated %d historical messages", migrated)
    except Exception:
        logger.warning("failed to migrate historical messages", exc_info=True)

    payload = account_in.model_copy(update={"email": email})
    return crud.create_steam_account(session=session, account_in=payload)


@router.get("/", response_model=SteamAccountsPublic)
def read_steam_accounts(
    _: ApiKeyDep, session: SessionDep, skip: int = 0, limit: int = 100
) -> Any:
    """列表。不返回密码，避免一次调用批量泄露所有凭据。"""
    count_statement = select(func.count()).select_from(SteamAccount)
    count = session.exec(count_statement).one()
    statement = (
        select(SteamAccount)
        .order_by(col(SteamAccount.created_at).desc())
        .offset(skip)
        .limit(limit)
    )
    accounts = session.exec(statement).all()
    data = [SteamAccountPublic.model_validate(a) for a in accounts]
    return SteamAccountsPublic(data=data, count=count)


@router.get("/{id}", response_model=SteamAccountDetail)
def read_steam_account(_: ApiKeyDep, session: SessionDep, id: uuid.UUID) -> Any:
    """单条查询，含明文密码 —— 转让账号时用。"""
    account = session.get(SteamAccount, id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")
    return account


@router.delete("/{id}", response_model=Message)
def delete_steam_account(
    _: ApiKeyDep, session: SessionDep, id: uuid.UUID, purge_mailbox: bool = False
) -> Any:
    """删除记录。``purge_mailbox=true`` 时连 Mailcow 里的邮箱一起删。"""
    account = session.get(SteamAccount, id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")

    if purge_mailbox:
        try:
            client = mailbox.build_mailcow_client()
            client.delete_mailbox(account.email)
        except mailbox.MailboxServiceError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception as exc:
            logger.exception("failed to delete mailbox in mailcow")
            raise HTTPException(status_code=502, detail="邮箱删除失败") from exc

    session.delete(account)
    session.commit()
    return Message(message="账号已删除")
