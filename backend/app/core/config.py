import secrets
import warnings
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AnyUrl,
    BeforeValidator,
    EmailStr,
    HttpUrl,
    PostgresDsn,
    computed_field,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",") if i.strip()]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file="../.env",
        env_ignore_empty=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = secrets.token_urlsafe(32)
    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    FRONTEND_HOST: str = "http://localhost:5173"
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"

    BACKEND_CORS_ORIGINS: Annotated[
        list[AnyUrl] | str, BeforeValidator(parse_cors)
    ] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_cors_origins(self) -> list[str]:
        return [str(origin).rstrip("/") for origin in self.BACKEND_CORS_ORIGINS] + [
            self.FRONTEND_HOST
        ]

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None
    POSTGRES_SERVER: str
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> PostgresDsn:
        return PostgresDsn.build(
            scheme="postgresql+psycopg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_SERVER,
            port=self.POSTGRES_PORT,
            path=self.POSTGRES_DB,
        )

    SMTP_TLS: bool = True
    SMTP_SSL: bool = False
    SMTP_PORT: int = 587
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: str | None = None

    @model_validator(mode="after")
    def _set_default_emails_from(self) -> Self:
        if not self.EMAILS_FROM_NAME:
            self.EMAILS_FROM_NAME = self.PROJECT_NAME
        return self

    EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.EMAILS_FROM_EMAIL)

    EMAIL_TEST_USER: EmailStr = "test@example.com"
    FIRST_SUPERUSER: EmailStr
    FIRST_SUPERUSER_PASSWORD: str

    # ------------------------------------------------ Steam 账号 / 临时邮箱
    # 可用邮箱域名，逗号分隔。/generate 默认随机挑一个，也可用 domain 参数指定。
    # 每个域名都需要在 Mailcow 添加并配好 catch-all 别名，见 docs/邮箱域名配置指南.md。
    # 复用 parse_cors —— 它就是个通用的逗号分隔解析器，与 CORS 无关。
    MAIL_DOMAINS: Annotated[list[str] | str, BeforeValidator(parse_cors)] = [
        "3dbody.top"
    ]
    # 邮件服务器地址，由 .env 提供，不在代码里写死具体部署
    IMAP_HOST: str = ""
    IMAP_PORT: int = 993
    # catch-all 兜底邮箱：未被保存的随机地址，来信都落在这里
    CATCHALL_ADDRESS: str = ""
    CATCHALL_PASSWORD: str = ""
    MAILCOW_BASE_URL: str = ""
    MAILCOW_API_KEY: str = ""
    # 保存邮箱时分配的配额
    MAILBOX_QUOTA_BYTES: int = 512 * 1024 * 1024
    # 禁止转让出去的账号外发，保护域名 IP 信誉
    MAILBOX_DISABLE_OUTBOUND: bool = True
    # 读信窗口与数量上限
    MAIL_DEFAULT_WINDOW_MINUTES: int = 10
    MAIL_MAX_WINDOW_MINUTES: int = 60
    MAIL_DEFAULT_LIMIT: int = 20
    MAIL_MAX_LIMIT: int = 50
    MAIL_RETURN_HTML: bool = False
    # 保存时从 catch-all 搬运历史邮件的回溯天数，0 表示不搬
    MAIL_MIGRATE_LOOKBACK_DAYS: int = 7
    # catch-all 清理脚本保留天数
    CATCHALL_RETENTION_DAYS: int = 14
    # 这组接口的固定 API Key，请求头 X-API-Key
    STEAM_API_KEY: str = "changethis"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mail_domains(self) -> list[str]:
        """规范化后的域名列表：统一小写，便于与邮箱地址比对。"""
        raw = self.MAIL_DOMAINS
        values = raw if isinstance(raw, list) else [raw]
        return [str(d).strip().lower() for d in values if str(d).strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mailcow_enabled(self) -> bool:
        return bool(self.MAILCOW_BASE_URL and self.MAILCOW_API_KEY)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def catchall_enabled(self) -> bool:
        return bool(
            self.IMAP_HOST and self.CATCHALL_ADDRESS and self.CATCHALL_PASSWORD
        )

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value == "changethis":
            message = (
                f'The value of {var_name} is "changethis", '
                "for security, please change it, at least for deployments."
            )
            if self.ENVIRONMENT == "local":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        self._check_default_secret("POSTGRES_PASSWORD", self.POSTGRES_PASSWORD)
        self._check_default_secret(
            "FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD
        )
        self._check_default_secret("STEAM_API_KEY", self.STEAM_API_KEY)

        return self


settings = Settings()  # type: ignore
