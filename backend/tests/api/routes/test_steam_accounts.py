import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.clients import imap
from app.core.config import settings
from app.models import SteamAccount
from app.services import mailbox

API = f"{settings.API_V1_STR}/steam-accounts"


@pytest.fixture
def key_headers() -> dict[str, str]:
    return {"X-API-Key": settings.STEAM_API_KEY}


def _message(
    *, to: str, text: str = "", subject: str = "Steam", uid: int = 1
) -> imap.MessageInfo:
    return imap.MessageInfo(
        id=f"imap-uid-{uid}",
        uid=uid,
        from_=[{"name": "Steam", "address": "noreply@steampowered.com"}],
        to=[{"name": "", "address": to}],
        subject=subject,
        received_at=datetime.now(UTC),
        size=len(text),
        text=text,
    )


# ------------------------------------------------------------------ 鉴权


def test_endpoints_reject_missing_api_key(client: TestClient) -> None:
    assert client.post(f"{API}/generate").status_code == 401
    assert client.get(f"{API}/").status_code == 401
    assert client.get(f"{API}/messages", params={"email": "a@b.com"}).status_code == 401


def test_endpoints_reject_wrong_api_key(client: TestClient) -> None:
    r = client.post(f"{API}/generate", headers={"X-API-Key": "nope"})
    assert r.status_code == 401


# ------------------------------------------------------------------ 生成


def test_generate_returns_four_credentials(
    client: TestClient, key_headers: dict[str, str]
) -> None:
    r = client.post(f"{API}/generate", headers=key_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["email"].rpartition("@")[2] in settings.mail_domains
    assert len(body["email_password"]) >= 12
    assert len(body["steam_password"]) >= 12
    assert body["steam_username"][0].isalpha()


def test_generate_accepts_explicit_domain(
    client: TestClient, key_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MAIL_DOMAINS", ["a.com", "b.com"])
    r = client.post(f"{API}/generate", headers=key_headers, params={"domain": "B.COM"})
    assert r.status_code == 200
    # 大小写不敏感，输出统一小写
    assert r.json()["email"].endswith("@b.com")


def test_generate_rejects_unknown_domain(
    client: TestClient, key_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MAIL_DOMAINS", ["a.com"])
    r = client.post(
        f"{API}/generate", headers=key_headers, params={"domain": "evil.com"}
    )
    assert r.status_code == 400


def test_generate_spreads_across_domains(
    client: TestClient, key_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """不指定域名时应在所有可用域名间随机分布，而不是固定用第一个。"""
    monkeypatch.setattr(settings, "MAIL_DOMAINS", ["a.com", "b.com"])
    seen = {
        client.post(f"{API}/generate", headers=key_headers)
        .json()["email"]
        .rpartition("@")[2]
        for _ in range(40)
    }
    assert seen == {"a.com", "b.com"}


def test_generate_is_random(client: TestClient, key_headers: dict[str, str]) -> None:
    first = client.post(f"{API}/generate", headers=key_headers).json()
    second = client.post(f"{API}/generate", headers=key_headers).json()
    assert first["email"] != second["email"]
    assert first["steam_username"] != second["steam_username"]


def test_generate_does_not_touch_db(
    client: TestClient, key_headers: dict[str, str], db: Session
) -> None:
    """没保存就不入库 —— 这是需求里的硬性约束。"""
    before = len(db.exec(select(SteamAccount)).all())
    client.post(f"{API}/generate", headers=key_headers)
    client.post(f"{API}/generate", headers=key_headers)
    db.expire_all()
    assert len(db.exec(select(SteamAccount)).all()) == before


# ------------------------------------------------------------------ 保存


def test_create_account_persists_after_provisioning(
    client: TestClient,
    key_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provisioned: list[tuple[str, str]] = []
    monkeypatch.setattr(
        mailbox,
        "provision_mailbox",
        lambda *, email, password: provisioned.append((email, password)),
    )
    monkeypatch.setattr(mailbox, "migrate_history", lambda *, email, password: 2)

    payload = {
        "email": "Save1@3dbody.top",
        "steam_username": "saveuser1",
        "email_password": "mailpw",
        "steam_password": "steampw",
    }
    r = client.post(f"{API}/", headers=key_headers, json=payload)
    assert r.status_code == 200
    body = r.json()
    # 地址统一转小写，避免大小写不同的同一邮箱被存成两条
    assert body["email"] == "save1@3dbody.top"
    assert body["email_password"] == "mailpw"
    assert provisioned == [("save1@3dbody.top", "mailpw")]

    stored = db.exec(
        select(SteamAccount).where(SteamAccount.email == "save1@3dbody.top")
    ).first()
    assert stored is not None


def test_create_account_does_not_persist_when_mailcow_fails(
    client: TestClient,
    key_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """建邮箱失败必须不落库，否则会留下指向不存在邮箱的记录。"""

    def boom(*, email: str, password: str) -> None:
        raise RuntimeError("mailcow down")

    monkeypatch.setattr(mailbox, "provision_mailbox", boom)

    payload = {
        "email": "fail@3dbody.top",
        "steam_username": "failuser",
        "email_password": "pw",
        "steam_password": "pw",
    }
    r = client.post(f"{API}/", headers=key_headers, json=payload)
    assert r.status_code == 502

    db.expire_all()
    assert (
        db.exec(
            select(SteamAccount).where(SteamAccount.email == "fail@3dbody.top")
        ).first()
        is None
    )


def test_create_account_survives_migration_failure(
    client: TestClient, key_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """邮箱已建好，搬历史信失败不能让整个保存回滚。"""
    monkeypatch.setattr(mailbox, "provision_mailbox", lambda *, email, password: None)

    def boom(*, email: str, password: str) -> int:
        raise RuntimeError("imap down")

    monkeypatch.setattr(mailbox, "migrate_history", boom)

    payload = {
        "email": "partial@3dbody.top",
        "steam_username": "partialuser",
        "email_password": "pw",
        "steam_password": "pw",
    }
    r = client.post(f"{API}/", headers=key_headers, json=payload)
    assert r.status_code == 200


def test_create_account_rejects_duplicates(
    client: TestClient, key_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mailbox, "provision_mailbox", lambda *, email, password: None)
    monkeypatch.setattr(mailbox, "migrate_history", lambda *, email, password: 0)

    payload = {
        "email": "dup@3dbody.top",
        "steam_username": "dupuser",
        "email_password": "pw",
        "steam_password": "pw",
    }
    assert client.post(f"{API}/", headers=key_headers, json=payload).status_code == 200

    assert client.post(f"{API}/", headers=key_headers, json=payload).status_code == 409

    other_username = dict(payload, steam_username="dupuser2")
    assert (
        client.post(f"{API}/", headers=key_headers, json=other_username).status_code
        == 409
    )

    other_email = dict(payload, email="dup2@3dbody.top")
    assert (
        client.post(f"{API}/", headers=key_headers, json=other_email).status_code == 409
    )


def test_create_account_rejects_unknown_domain(
    client: TestClient,
    key_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """域名不在白名单时应给出明确的 400，且不触碰 Mailcow 和数据库。"""
    monkeypatch.setattr(settings, "MAIL_DOMAINS", ["3dbody.top"])

    def must_not_run(*, email: str, password: str) -> None:
        raise AssertionError("域名校验失败时不应调用 Mailcow")

    monkeypatch.setattr(mailbox, "provision_mailbox", must_not_run)

    r = client.post(
        f"{API}/",
        headers=key_headers,
        json={
            "email": "someone@notmine.com",
            "steam_username": "outsideuser",
            "email_password": "pw",
            "steam_password": "pw",
        },
    )
    assert r.status_code == 400
    db.expire_all()
    assert (
        db.exec(
            select(SteamAccount).where(SteamAccount.email == "someone@notmine.com")
        ).first()
        is None
    )


def test_create_account_returns_503_when_unconfigured(
    client: TestClient, key_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MAILCOW_BASE_URL", "")
    monkeypatch.setattr(settings, "MAILCOW_API_KEY", "")
    payload = {
        "email": "unconf@3dbody.top",
        "steam_username": "unconfuser",
        "email_password": "pw",
        "steam_password": "pw",
    }
    r = client.post(f"{API}/", headers=key_headers, json=payload)
    assert r.status_code == 503


# ------------------------------------------------------------------ 查询


def test_list_hides_passwords_detail_reveals_them(
    client: TestClient, key_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mailbox, "provision_mailbox", lambda *, email, password: None)
    monkeypatch.setattr(mailbox, "migrate_history", lambda *, email, password: 0)
    created = client.post(
        f"{API}/",
        headers=key_headers,
        json={
            "email": "list@3dbody.top",
            "steam_username": "listuser",
            "email_password": "secret-mail",
            "steam_password": "secret-steam",
        },
    ).json()

    listing = client.get(f"{API}/", headers=key_headers).json()
    assert listing["count"] >= 1
    for entry in listing["data"]:
        assert "email_password" not in entry
        assert "steam_password" not in entry

    detail = client.get(f"{API}/{created['id']}", headers=key_headers).json()
    assert detail["email_password"] == "secret-mail"
    assert detail["steam_password"] == "secret-steam"


def test_detail_404_for_unknown_id(
    client: TestClient, key_headers: dict[str, str]
) -> None:
    r = client.get(f"{API}/{uuid.uuid4()}", headers=key_headers)
    assert r.status_code == 404


# ------------------------------------------------------------------ 读信


def test_messages_filters_by_recipient(
    client: TestClient, key_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """catch-all 里混着所有地址的信，必须只返回目标收件人的。"""
    monkeypatch.setattr(settings, "CATCHALL_ADDRESS", "catchall@3dbody.top")
    monkeypatch.setattr(settings, "CATCHALL_PASSWORD", "pw")
    monkeypatch.setattr(
        imap,
        "fetch_recent_messages",
        lambda **kwargs: [
            _message(to="wanted@3dbody.top", text="Your code is ABC12", uid=1),
            _message(to="other@3dbody.top", text="Your code is ZZZ99", uid=2),
        ],
    )

    r = client.get(
        f"{API}/messages", headers=key_headers, params={"email": "wanted@3dbody.top"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["data"][0]["to"][0]["address"] == "wanted@3dbody.top"
    assert body["data"][0]["extracted_code"] == "ABC12"


def test_messages_keeps_raw_text_when_parsing_fails(
    client: TestClient, key_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """正则失效时原文必须还在，否则接口彻底不可用。"""
    monkeypatch.setattr(settings, "CATCHALL_ADDRESS", "catchall@3dbody.top")
    monkeypatch.setattr(settings, "CATCHALL_PASSWORD", "pw")
    monkeypatch.setattr(
        imap,
        "fetch_recent_messages",
        lambda **kwargs: [_message(to="x@3dbody.top", text="没有任何可识别的内容")],
    )

    body = client.get(
        f"{API}/messages", headers=key_headers, params={"email": "x@3dbody.top"}
    ).json()
    assert body["data"][0]["extracted_code"] is None
    assert body["data"][0]["text"] == "没有任何可识别的内容"


def test_messages_reads_own_mailbox_once_saved(
    client: TestClient, key_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """已保存的邮箱新信不再进 catch-all，必须直连它自己。"""
    monkeypatch.setattr(mailbox, "provision_mailbox", lambda *, email, password: None)
    monkeypatch.setattr(mailbox, "migrate_history", lambda *, email, password: 0)
    client.post(
        f"{API}/",
        headers=key_headers,
        json={
            "email": "own@3dbody.top",
            "steam_username": "ownuser",
            "email_password": "ownpw",
            "steam_password": "pw",
        },
    )

    logins: list[str] = []

    def fake_fetch(**kwargs: object) -> list[imap.MessageInfo]:
        logins.append(str(kwargs["address"]))
        return [_message(to="own@3dbody.top", text="Your code is QW3RT")]

    monkeypatch.setattr(imap, "fetch_recent_messages", fake_fetch)

    body = client.get(
        f"{API}/messages", headers=key_headers, params={"email": "own@3dbody.top"}
    ).json()
    assert logins == ["own@3dbody.top"]
    assert body["data"][0]["extracted_code"] == "QW3RT"


def test_messages_returns_503_when_catchall_unconfigured(
    client: TestClient, key_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "CATCHALL_ADDRESS", "")
    monkeypatch.setattr(settings, "CATCHALL_PASSWORD", "")
    r = client.get(
        f"{API}/messages", headers=key_headers, params={"email": "any@3dbody.top"}
    )
    assert r.status_code == 503


def test_messages_clamps_window_and_limit(
    client: TestClient, key_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """超大的 minutes/limit 必须被压到上限，不能让调用方拖垮 IMAP。"""
    monkeypatch.setattr(settings, "CATCHALL_ADDRESS", "catchall@3dbody.top")
    monkeypatch.setattr(settings, "CATCHALL_PASSWORD", "pw")
    captured: dict[str, object] = {}

    def fake_fetch(**kwargs: object) -> list[imap.MessageInfo]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(imap, "fetch_recent_messages", fake_fetch)

    client.get(
        f"{API}/messages",
        headers=key_headers,
        params={"email": "x@3dbody.top", "minutes": 99999, "limit": 99999},
    )
    limits = captured["limits"]
    assert isinstance(limits, imap.FetchLimits)
    assert limits.minutes == settings.MAIL_MAX_WINDOW_MINUTES
    assert limits.limit == settings.MAIL_MAX_LIMIT
