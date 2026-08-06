"""清理 catch-all 兜底邮箱里的历史邮件。

所有未被保存的随机地址的来信都堆在这一个邮箱里，不清理迟早撑满磁盘。
用 cron 每天跑一次：

    0 4 * * * docker compose exec -T backend python app/cleanup_catchall.py

保留天数由 ``CATCHALL_RETENTION_DAYS`` 控制。已保存的邮箱有自己的箱子，
不受这个脚本影响。
"""

import logging
import sys
from datetime import UTC, datetime, timedelta

from app.clients import imap
from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    if not settings.catchall_enabled:
        logger.error("IMAP_HOST / CATCHALL_ADDRESS / CATCHALL_PASSWORD 未配置，跳过")
        return 1

    cutoff = datetime.now(UTC) - timedelta(days=settings.CATCHALL_RETENTION_DAYS)
    logger.info(
        "cleaning catch-all mailbox, removing messages before %s",
        cutoff.date().isoformat(),
    )
    try:
        removed = imap.delete_messages_before(
            host=settings.IMAP_HOST,
            port=settings.IMAP_PORT,
            address=settings.CATCHALL_ADDRESS,
            password=settings.CATCHALL_PASSWORD,
            cutoff=cutoff,
        )
    except imap.ImapError:
        logger.exception("catch-all cleanup failed")
        return 1
    logger.info("removed %d messages", removed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
