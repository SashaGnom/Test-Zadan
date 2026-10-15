import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import engine, get_session
from app.idempotency import request_fingerprint
from app.models import OutboxEvent, Payment
from app.outbox import run_outbox_dispatcher
from app.schemas import PaymentAccepted, PaymentCreate, PaymentDetails, PaymentStatus
from app.security import require_api_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = asyncio.Event()
    dispatcher_task = asyncio.create_task(run_outbox_dispatcher(stop_event))
    yield
    stop_event.set()
    with suppress(Exception):
        await dispatcher_task
    await engine.dispose()


app = FastAPI(
    title="Payment Processing Service",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.get("/health", dependencies=[Depends(require_api_key)])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/v1/payments",
    response_model=PaymentAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_api_key)],
)
async def create_payment(
    payload: PaymentCreate,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PaymentAccepted:
    key = idempotency_key.strip()
    if not key:
        raise HTTPException(status_code=422, detail="Idempotency-Key must not be empty")

    fingerprint = request_fingerprint(payload)
    existing = await session.scalar(select(Payment).where(Payment.idempotency_key == key))
    if existing is not None:
        if existing.request_hash != fingerprint:
            raise HTTPException(status_code=409, detail="Idempotency-Key was already used with a different request")
        response.headers["Idempotency-Replayed"] = "true"
        return PaymentAccepted(payment_id=existing.id, status=PaymentStatus(existing.status), created_at=existing.created_at)

    now = datetime.now(timezone.utc)
    payment_id = uuid4()
    payment = Payment(
        id=payment_id,
        amount=payload.amount,
        currency=payload.currency.value,
        description=payload.description,
        metadata_json=payload.metadata,
        status=PaymentStatus.PENDING.value,
        idempotency_key=key,
        request_hash=fingerprint,
        webhook_url=str(payload.webhook_url),
        created_at=now,
    )
    event = OutboxEvent(
        id=uuid4(),
        aggregate_id=payment_id,
        event_type="payment.created",
        payload={"payment_id": str(payment_id), "event_type": "payment.created"},
        next_attempt_at=now,
        created_at=now,
    )

    session.add(payment)
    try:
        await session.flush()
        session.add(event)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(select(Payment).where(Payment.idempotency_key == key))
        if existing is not None:
            if existing.request_hash != fingerprint:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was already used with a different request",
                )
            response.headers["Idempotency-Replayed"] = "true"
            return PaymentAccepted(payment_id=existing.id, status=PaymentStatus(existing.status), created_at=existing.created_at)
        raise

    return PaymentAccepted(payment_id=payment.id, status=PaymentStatus(payment.status), created_at=payment.created_at)


@app.get(
    "/api/v1/payments/{payment_id}",
    response_model=PaymentDetails,
    dependencies=[Depends(require_api_key)],
)
async def get_payment(
    payment_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PaymentDetails:
    try:
        parsed_id = UUID(payment_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid payment ID") from exc

    payment = await session.get(Payment, parsed_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")

    return PaymentDetails(
        payment_id=payment.id,
        status=PaymentStatus(payment.status),
        created_at=payment.created_at,
        amount=payment.amount,
        currency=payment.currency,
        description=payment.description,
        metadata=payment.metadata_json,
        idempotency_key=payment.idempotency_key,
        webhook_url=payment.webhook_url,
        processed_at=payment.processed_at,
    )
