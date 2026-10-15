from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.idempotency import request_fingerprint
from app.models import Base
from app.retries import retry_delay
from app.schemas import Currency, PaymentCreate


def test_payment_payload_is_valid() -> None:
    payload = PaymentCreate(
        amount=Decimal("1250.50"),
        currency=Currency.RUB,
        description="Order 123",
        metadata={"order_id": 123},
        webhook_url="https://example.com/webhook",
    )
    assert payload.amount == Decimal("1250.50")
    assert payload.metadata == {"order_id": 123}


def test_payment_amount_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        PaymentCreate(
            amount=Decimal("0"),
            currency=Currency.RUB,
            description="Order 123",
            webhook_url="https://example.com/webhook",
        )


def test_payment_amount_allows_at_most_two_decimal_places() -> None:
    with pytest.raises(ValidationError):
        PaymentCreate(
            amount=Decimal("1.234"),
            currency=Currency.RUB,
            description="Order 123",
            webhook_url="https://example.com/webhook",
        )


def test_request_fingerprint_is_stable() -> None:
    payload = PaymentCreate(
        amount=Decimal("10.00"),
        currency=Currency.RUB,
        description="Test",
        metadata={"b": 2, "a": 1},
        webhook_url="https://example.com/hook",
    )
    assert request_fingerprint(payload) == request_fingerprint(payload)


def test_request_fingerprint_changes_when_request_changes() -> None:
    first = PaymentCreate(
        amount=Decimal("10.00"),
        currency=Currency.RUB,
        description="First",
        metadata={"order_id": 1},
        webhook_url="https://example.com/hook",
    )
    second = first.model_copy(update={"description": "Second"})
    assert request_fingerprint(first) != request_fingerprint(second)


def test_request_fingerprint_is_independent_of_metadata_key_order() -> None:
    first = PaymentCreate(
        amount=Decimal("10.00"),
        currency=Currency.RUB,
        description="Test",
        metadata={"b": 2, "a": 1},
        webhook_url="https://example.com/hook",
    )
    second = PaymentCreate(
        amount=Decimal("10.00"),
        currency=Currency.RUB,
        description="Test",
        metadata={"a": 1, "b": 2},
        webhook_url="https://example.com/hook",
    )
    assert request_fingerprint(first) == request_fingerprint(second)


def test_request_fingerprint_normalizes_amount_scale() -> None:
    first = PaymentCreate(
        amount=Decimal("10"),
        currency=Currency.RUB,
        description="Test",
        metadata={},
        webhook_url="https://example.com/hook",
    )
    second = PaymentCreate(
        amount=Decimal("10.00"),
        currency=Currency.RUB,
        description="Test",
        metadata={},
        webhook_url="https://example.com/hook",
    )
    assert request_fingerprint(first) == request_fingerprint(second)


def test_internal_http_webhook_url_is_valid() -> None:
    payload = PaymentCreate(
        amount=Decimal("5.00"),
        currency=Currency.RUB,
        description="Internal webhook test",
        webhook_url="http://webhook-test:9000/webhook",
    )
    assert str(payload.webhook_url) == "http://webhook-test:9000/webhook"


def test_required_database_tables_are_defined() -> None:
    assert {"payments", "outbox"}.issubset(Base.metadata.tables)


@pytest.mark.parametrize(
    ("retry_number", "expected_delay"),
    [(1, 1), (2, 2), (3, 4), (4, 8), (6, 32), (7, 60)],
)
def test_retry_delay_is_exponential_and_capped(retry_number: int, expected_delay: int) -> None:
    assert retry_delay(retry_number) == expected_delay


def test_retry_delay_rejects_invalid_attempt_number() -> None:
    with pytest.raises(ValueError):
        retry_delay(0)
