"""Mailcow HTTP API 客户端（同步版）。

移植自参考项目 steamgenerateaccount，改 ``httpx.AsyncClient`` 为同步
``httpx.Client``，并只保留本项目用得到的三个操作。

版本相关的路径与字段全部收敛在本文件，业务层只看内部数据结构。
如果部署的 Mailcow 版本字段有差异，只需要改这里的路径常量和 ``_parse_mailbox``。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class MailcowError(RuntimeError):
    """Mailcow 调用失败的基类。"""


class MailcowAuthError(MailcowError):
    """API Key 无效或来源被拒绝。"""


class MailcowNotFound(MailcowError):
    """目标邮箱不存在。"""


class MailcowDuplicate(MailcowError):
    """地址已存在。"""


class MailcowQuotaError(MailcowError):
    """域配额不足。"""


class MailcowUnavailable(MailcowError):
    """5xx、超时或响应不可解析，状态未知。"""


@dataclass(frozen=True)
class MailcowMailbox:
    address: str
    active: bool
    quota_bytes: int


class MailcowClient:
    """调用频率很低（只在保存接口），每次请求新建连接，不做连接池管理。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_seconds: float = 10.0,
        verify: bool = True,
        disable_outbound: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._verify = verify
        self._disable_outbound = disable_outbound

    @property
    def _headers(self) -> dict[str, str]:
        # 该字典只用于发请求，不进入任何日志。
        return {"X-API-Key": self._api_key, "Content-Type": "application/json"}

    def _request(self, method: str, path: str, *, json_body: Any = None) -> Any:
        try:
            with httpx.Client(
                base_url=self._base_url, timeout=self._timeout, verify=self._verify
            ) as client:
                response = client.request(
                    method, path, headers=self._headers, json=json_body
                )
        except httpx.TimeoutException as exc:
            raise MailcowUnavailable("Mailcow 请求超时") from exc
        except httpx.HTTPError as exc:
            raise MailcowUnavailable("Mailcow 连接失败") from exc

        if response.status_code in (401, 403):
            raise MailcowAuthError("Mailcow API Key 被拒绝")
        if response.status_code == 404:
            raise MailcowNotFound("Mailcow 资源不存在")
        if response.status_code >= 500:
            raise MailcowUnavailable(f"Mailcow 返回 {response.status_code}")
        if response.status_code >= 400:
            raise MailcowError(f"Mailcow 返回 {response.status_code}")

        try:
            return response.json()
        except ValueError as exc:
            raise MailcowUnavailable("Mailcow 响应不是合法 JSON") from exc

    @staticmethod
    def _check_result(payload: Any) -> None:
        """Mailcow 的写接口即使失败也返回 200，必须解析 body 里的 type 字段。"""
        entries = payload if isinstance(payload, list) else [payload]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            result_type = str(entry.get("type", "")).lower()
            message = " ".join(str(m) for m in _as_list(entry.get("msg")))
            if result_type in ("success", "info"):
                continue
            lowered = message.lower()
            if "exists" in lowered or "duplicate" in lowered:
                raise MailcowDuplicate(message or "地址已存在")
            if "quota" in lowered:
                raise MailcowQuotaError(message or "配额不足")
            if result_type in ("danger", "error"):
                raise MailcowError(message or "Mailcow 操作失败")

    @staticmethod
    def _parse_mailbox(payload: Any) -> MailcowMailbox | None:
        entry = payload[0] if isinstance(payload, list) and payload else payload
        if not isinstance(entry, dict) or "username" not in entry:
            return None
        # quota 字段在多数版本中单位为字节。
        quota_raw = entry.get("quota") or 0
        try:
            quota_bytes = int(quota_raw)
        except TypeError, ValueError:
            quota_bytes = 0
        return MailcowMailbox(
            address=str(entry["username"]),
            active=str(entry.get("active", "1")) in ("1", "True", "true"),
            quota_bytes=quota_bytes,
        )

    def get_mailbox(self, address: str) -> MailcowMailbox | None:
        """查询邮箱。不存在返回 None（部分版本返回空数组而不是 404）。"""
        try:
            payload = self._request("GET", f"/api/v1/get/mailbox/{address}")
        except MailcowNotFound:
            return None
        return self._parse_mailbox(payload)

    def create_mailbox(self, address: str, password: str, quota_bytes: int) -> None:
        local_part, _, domain = address.partition("@")
        body = {
            "local_part": local_part,
            "domain": domain,
            "name": local_part,
            "password": password,
            "password2": password,
            "quota": _to_mib(quota_bytes),
            "active": "1",
            "force_pw_update": "0",
            "tls_enforce_in": "0",
            "tls_enforce_out": "0",
        }
        payload = self._request("POST", "/api/v1/add/mailbox", json_body=body)
        self._check_result(payload)
        if self._disable_outbound:
            self.disable_outbound(address)

    def disable_outbound(self, address: str) -> None:
        """清空 sender ACL：账号不能以任何地址外发。

        这是保护域名信誉的关键 —— 一旦某个转让出去的账号被拿来发垃圾邮件，
        整个 3dbody.top 的 IP 信誉受损，所有邮箱都会收不到信。
        """
        body = {"items": [address], "attr": {"sender_acl": []}}
        payload = self._request("POST", "/api/v1/edit/mailbox", json_body=body)
        self._check_result(payload)

    def delete_mailbox(self, address: str) -> None:
        """404 视为已完成（幂等）。"""
        try:
            payload = self._request(
                "POST", "/api/v1/delete/mailbox", json_body=[address]
            )
        except MailcowNotFound:
            logger.info("mailcow delete: mailbox already absent")
            return
        try:
            self._check_result(payload)
        except MailcowError as exc:
            if "not found" in str(exc).lower():
                return
            raise


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _to_mib(quota_bytes: int) -> int:
    """Mailcow 配额以 MiB 为单位，向上取整并保证至少 1 MiB。"""
    return max(1, -(-quota_bytes // (1024 * 1024)))


__all__ = [
    "MailcowAuthError",
    "MailcowClient",
    "MailcowDuplicate",
    "MailcowError",
    "MailcowMailbox",
    "MailcowNotFound",
    "MailcowQuotaError",
    "MailcowUnavailable",
]
