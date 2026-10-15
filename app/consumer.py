import asyncio
import logging
import random
from datetime import datetime, timezone
from uuid import UUID

import httpx
from faststream import FastStream
from faststream.rabbit import RabbitBroker, RabbitQueue
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import Payment
from app.retries import retry_delay
from app.schemas import PaymentStatus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

broker = RabbitBroker(settings.rabbitmq_url, reconnect_interval=2)
app = FastStream(broker)
payments_queue = RabbitQueue(settings.payments_queue, durable=True)
dead_letter_queue = RabbitQueue(settings.payments_dlq, durable=True)


@app.on_startup
async def declare_dead_letter_queue() -> None:
    await broker.connect()
    await broker.declare_queue(dead_letter_queue)


async def process_payment(payment_id: UUID) -> None:
    async with SessionLocal() as session:
        payment = await session.get(Payment, payment_id)
        if payment is None:
            raise ValueError(f"Payment {payment_id} does not exist")
        should_process = payment.status == PaymentStatus.PENDING.value

    if should_process:
        await asyncio.sleep(random.uniform(2, 5))

    async with SessionLocal() as session:
        async with session.begin():
            payment = await session.scalar(
                select(Payment).where(Payment.id == payment_id).with_for_update()
            )
            if payment is None:
                raise ValueError(f"Payment {payment_id} does not exist")
            if payment.status == PaymentStatus.PENDING.value:
                payment.status = (
                    PaymentStatus.SUCCEEDED.value if random.random() < 0.9 else PaymentStatus.FAILED.value
                )
                payment.processed_at = datetime.now(timezone.utc)
            body = {
                "event": "payment.processed",
                "payment_id": str(payment.id),
                "status": payment.status,
                "amount": str(payment.amount),
                "currency": payment.currency,
                "description": payment.description,
                "metadata": payment.metadata_json,
                "created_at": payment.created_at.isoformat(),
                "processed_at": payment.processed_at.isoformat() if payment.processed_at else None,
            }
            webhook_url = payment.webhook_url

    async with httpx.AsyncClient(timeout=settings.webhook_timeout) as client:
        response = await client.post(
            webhook_url,
            json=body,
            headers={"X-Webhook-Event": "payment.processed", "Idempotency-Key": str(payment_id)},
        )
        response.raise_for_status()


async def send_to_dlq(message: dict, error: str, payment_id: UUID | None = None) -> None:
    payload = {
        "payment_id": str(payment_id) if payment_id else message.get("payment_id"),
        "event_type": message.get("event_type", "payment.created"),
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "attempts": 3,
        "error": error[:2000],
        "original_message": message,
    }
    await broker.publish(
        payload,
        queue=dead_letter_queue,
        persist=True,
        message_id=f"{payment_id or 'invalid'}-dead-letter",
    )


@broker.subscriber(payments_queue)
async def handle_payment(message: dict) -> None:
    try:
        payment_id = UUID(str(message["payment_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        await send_to_dlq(message, f"Invalid payment message: {exc}")
        return

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            await process_payment(payment_id)
            logger.info("Payment %s processed", payment_id)
            return
        except Exception as exc:
            last_error = exc
            logger.warning("Payment %s attempt %s failed: %s", payment_id, attempt + 1, exc)
            if attempt < 2:
                await asyncio.sleep(retry_delay(attempt + 1))

    await send_to_dlq(message, str(last_error or "Unknown error"), payment_id)
    logger.error("Payment %s moved to %s after 3 attempts", payment_id, settings.payments_dlq)
