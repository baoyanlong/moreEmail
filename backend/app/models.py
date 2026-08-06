import uuid
from datetime import UTC, datetime

from pydantic import EmailStr
from sqlalchemy import DateTime
from sqlmodel import Field, Relationship, SQLModel


def get_datetime_utc() -> datetime:
    return datetime.now(UTC)


# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on update, all are optional
class UserUpdate(SQLModel):
    email: EmailStr | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    is_superuser: bool | None = None
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    items: list[Item] = Relationship(back_populates="owner", cascade_delete=True)


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# Shared properties
class ItemBase(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Properties to receive on item creation
class ItemCreate(ItemBase):
    pass


# Properties to receive on item update
class ItemUpdate(SQLModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Database model, database table inferred from class name
class Item(ItemBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    owner: User | None = Relationship(back_populates="items")


# Properties to return via API, id is always required
class ItemPublic(ItemBase):
    id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime | None = None


class ItemsPublic(SQLModel):
    data: list[ItemPublic]
    count: int


# ---------------------------------------------------------------- Steam 账号
# 与 User 无任何关联：这组接口不接用户体系，只用固定 API Key 鉴权。


class SteamAccountBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    steam_username: str = Field(unique=True, index=True, max_length=64)
    note: str | None = Field(default=None, max_length=255)


# 入参：四项凭据由 /generate 生成后原样回传
class SteamAccountCreate(SteamAccountBase):
    email_password: str = Field(min_length=1, max_length=255)
    steam_password: str = Field(min_length=1, max_length=255)


class SteamAccountUpdate(SQLModel):
    note: str | None = Field(default=None, max_length=255)


class SteamAccount(SteamAccountBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    # 明文存储：这两个密码需要整套交付给账号使用者，不能只存哈希。
    email_password: str = Field(max_length=255)
    steam_password: str = Field(max_length=255)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


# 列表出参：不含密码，避免一次调用批量泄露所有凭据
class SteamAccountPublic(SteamAccountBase):
    id: uuid.UUID
    created_at: datetime | None = None


# 单条出参：含明文密码，转让账号时才调用
class SteamAccountDetail(SteamAccountPublic):
    email_password: str
    steam_password: str


class SteamAccountsPublic(SQLModel):
    data: list[SteamAccountPublic]
    count: int


# /generate 的出参，纯计算结果，不落库
class GeneratedCredentials(SQLModel):
    email: EmailStr
    email_password: str
    steam_username: str
    steam_password: str


class MailAddress(SQLModel):
    name: str = ""
    address: str = ""


class MailAttachment(SQLModel):
    id: str
    filename: str | None = None
    content_type: str
    size: int | None = None


class MailMessage(SQLModel):
    id: str
    # 不叫 from：那是保留字，用 alias 换来的只是和 SQLModel 配置类型打架
    sender: list[MailAddress] = Field(default_factory=list)
    to: list[MailAddress] = Field(default_factory=list)
    subject: str = ""
    received_at: datetime
    size: int
    text: str | None = None
    html: str | None = None
    attachments: list[MailAttachment] = Field(default_factory=list)
    truncated: bool = False
    body_loaded: bool = True
    # 从正文里解析出来的结果，解析失败时为空，原始 text 始终保留
    extracted_code: str | None = None
    extracted_links: list[str] = Field(default_factory=list)


class MailMessagesPublic(SQLModel):
    email: str
    window_start: datetime
    data: list[MailMessage]
    count: int


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)
