import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from uuid import UUID

from faststream.rabbit import RabbitBroker, RabbitQueue
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import OutboxEvent
from app.retries import retry_delay

logger = logging.getLogger(__name__)


async def publish_pending_events(broker: RabbitBroker, batch_size: int = 100) -> None:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        result = await session.scalars(
            select(OutboxEvent.id)
            .where(OutboxEvent.published_at.is_(None), OutboxEvent.next_attempt_at <= now)
            .order_by(OutboxEvent.created_at)
            .limit(batch_size)
        )
        event_ids = list(result.all())

    for event_id in event_ids:
        await _publish_one(event_id, broker)


async def _publish_one(event_id: UUID, broker: RabbitBroker) -> None:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        async with session.begin():
            event = await session.scalar(
                select(OutboxEvent)
                .where(
                    OutboxEvent.id == event_id,
                    OutboxEvent.published_at.is_(None),
                    OutboxEvent.next_attempt_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
            if event is None:
                return
            try:
                result = await broker.publish(
                    event.payload,
                    queue=RabbitQueue(settings.payments_queue, durable=True),
                    persist=True,
                    message_id=str(event.id),
                )
                if result is False:
                    raise RuntimeError("RabbitMQ did not confirm the message")
            except Exception as exc:
                event.attempt_count += 1
                event.last_error = str(exc)[:2000]
                event.next_attempt_at = now + timedelta(seconds=retry_delay(event.attempt_count))
                logger.warning("Outbox event %s publish failed: %s", event.id, exc)
            else:
                event.published_at = now
                event.last_error = None


async def run_outbox_dispatcher(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        broker = RabbitBroker(settings.rabbitmq_url, reconnect_interval=2)
        try:
            await broker.start()
            await broker.declare_queue(RabbitQueue(settings.payments_queue, durable=True))
        except Exception:
            logger.exception("Could not connect to RabbitMQ")
            with suppress(Exception):
                await broker.stop()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=settings.outbox_poll_interval)
            except TimeoutError:
                pass
            continue

        try:
            while not stop_event.is_set():
                try:
                    await publish_pending_events(broker)
                except Exception:
                    logger.exception("Outbox dispatcher failed")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=settings.outbox_poll_interval)
                except TimeoutError:
                    pass
        finally:
            with suppress(Exception):
                await broker.stop()
