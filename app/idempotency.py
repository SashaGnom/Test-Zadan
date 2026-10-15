import hashlib
import json
from decimal import Decimal

from app.schemas import PaymentCreate


def request_fingerprint(payload: PaymentCreate) -> str:
    normalized = {
        "amount": format(payload.amount.quantize(Decimal("0.01")), "f"),
        "currency": payload.currency.value,
        "description": payload.description,
        "metadata": payload.metadata,
        "webhook_url": str(payload.webhook_url),
    }
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
