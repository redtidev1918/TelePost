"""Independent schedule watchdog: a slot silently missing its terminal
notification must not leave the operator (or the wait-cycle user) guessing."""
import datetime
import logging

logger = logging.getLogger(__name__)

# If a schedule has EVER emitted a terminal outcome but has gone this long
# without one, treat it as a silent gap and alert independently.
# Format: Days, then as ISO alerting. Default 26h (daily cadence + slack).
WATCHDOG_MAX_HOURS = float(__import__("os").getenv("SCHEDULE_WATCHDOG_MAX_HOURS", "26"))


async def schedule_watchdog_job(context) -> None:
    """Repeating job: alert when a recognized schedule is silent too long.

    Recognized schedules come from schedule_outcome_notifications history, so a
    brand-new DB with no history stays quiet until the first real outcome.
    Idempotency is per (schedule, UTC date): at most one independent alert per
    stale day, using the same claim ledger as API notifications.
    """
    try:
        from database import db_manager
        from utils.api_server import REVIEW_CHAT_ID, claim_api_notification, mark_api_notification_sent

        if not REVIEW_CHAT_ID:
            return
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        cutoff = now - WATCHDOG_MAX_HOURS * 3600
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                """
                SELECT slot_id, sent_at, status
                FROM schedule_outcome_notifications
                """
            )
            rows = await cur.fetchall()
        last_by_schedule = {}
        for r in rows:
            slot = r["slot_id"] or ""
            schedule_id = slot.split("@", 1)[0] if "@" in slot else ""
            if not schedule_id:
                continue
            sent = float(r["sent_at"] or 0)
            prev = last_by_schedule.get(schedule_id)
            if prev is None or sent > prev[1]:
                last_by_schedule[schedule_id] = (sent, r["status"] or "")
        stale = [
            (sid, sent, status)
            for sid, (sent, status) in last_by_schedule.items()
            if sent < cutoff
        ]
        for schedule_id, last_sent, last_status in stale:
            utc_date = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
            key = f"schedule-watchdog:{schedule_id}:{utc_date}"
            if not await claim_api_notification(0, key):
                continue
            try:
                last_iso = datetime.datetime.fromtimestamp(
                    float(last_sent), datetime.timezone.utc
                ).isoformat()
                text = (
                    f"⚠️ 计划静默告警：{schedule_id}\n"
                    f"最近一次终态通知：{last_iso}（{last_status or '未知'}）\n"
                    f"已超过 {WATCHDOG_MAX_HOURS:.0f} 小时没有新的 Slot 终态，"
                    "用户可能仍在等待作品，请检查 PixivFlow / 时钟 / Webhook。"
                )
                logger.warning("schedule watchdog alert: schedule=%s last_sent=%s", schedule_id, last_iso)
                message = await context.bot.send_message(chat_id=REVIEW_CHAT_ID, text=text)
                await mark_api_notification_sent(0, key, message.message_id)
            except Exception:
                from utils.api_server import release_api_notification
                await release_api_notification(0, key)
                logger.warning("schedule watchdog send failed: schedule=%s", schedule_id, exc_info=True)
    except Exception:
        # Watchdog must never break the resident service.
        logger.debug("schedule watchdog pass failed", exc_info=True)
