import base64
import json
import os
import time
import urllib.error
import urllib.request
from uuid import uuid4

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "http://localhost:9000")
RABBITMQ_MANAGEMENT_URL = os.getenv("RABBITMQ_MANAGEMENT_URL", "http://localhost:15672")
API_KEY = os.getenv("API_KEY", "ci-test-key-not-for-production")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "payments")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "payments")


def request_json(
    url: str,
    method: str = "GET",
    payload: dict | None = None,
    api_key: str | None = None,
    idempotency_key: str | None = None,
    basic_auth: tuple[str, str] | None = None,
) -> tuple[int, dict[str, str], object | None]:
    headers: dict[str, str] = {}
    if api_key is not None:
        headers["X-API-Key"] = api_key
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    if basic_auth is not None:
        value = base64.b64encode(f"{basic_auth[0]}:{basic_auth[1]}".encode()).decode()
        headers["Authorization"] = f"Basic {value}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read()
            parsed = json.loads(body) if body else None
            return response.status, {key.lower(): value for key, value in response.headers.items()}, parsed
    except urllib.error.HTTPError as error:
        body = error.read()
        parsed = json.loads(body) if body else None
        return error.code, {key.lower(): value for key, value in error.headers.items()}, parsed


def wait_for_api() -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            status, _, body = request_json(f"{API_BASE_URL}/health", api_key=API_KEY)
            if status == 200 and body == {"status": "ok"}:
                return
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(2)
    raise RuntimeError("API did not become healthy within 90 seconds")


def create_payment(payload: dict, key: str) -> dict:
    status, _, body = request_json(
        f"{API_BASE_URL}/api/v1/payments",
        method="POST",
        payload=payload,
        api_key=API_KEY,
        idempotency_key=key,
    )
    assert status == 202 and isinstance(body, dict), f"Payment creation failed: {status} {body}"
    return body


def wait_for_payment(payment_id: str) -> dict:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status, _, body = request_json(f"{API_BASE_URL}/api/v1/payments/{payment_id}", api_key=API_KEY)
        if status != 200 or not isinstance(body, dict):
            raise AssertionError(f"Payment lookup failed: {status} {body}")
        if body.get("status") in {"succeeded", "failed"}:
            return body
        time.sleep(1)
    raise RuntimeError("Payment was not processed within 60 seconds")


def wait_for_webhook(payment_id: str, expected_status: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status, _, body = request_json(f"{WEBHOOK_BASE_URL}/received")
        if status == 200 and isinstance(body, list):
            for event in body:
                if event.get("payment_id") == payment_id and event.get("status") == expected_status:
                    return
        time.sleep(1)
    raise RuntimeError(f"Webhook for payment {payment_id} was not delivered")


def read_dlq_messages() -> list[dict]:
    url = f"{RABBITMQ_MANAGEMENT_URL}/api/queues/%2F/payments.dlq/get"
    status, _, body = request_json(
        url,
        method="POST",
        payload={"count": 100, "ackmode": "ack_requeue_true", "encoding": "auto", "truncate": 50000},
        basic_auth=(RABBITMQ_USER, RABBITMQ_PASSWORD),
    )
    if status != 200 or not isinstance(body, list):
        return []
    payloads = []
    for item in body:
        payload = item.get("payload") if isinstance(item, dict) else None
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def wait_for_dlq(payment_id: str) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            for message in read_dlq_messages():
                if message.get("payment_id") == payment_id:
                    assert message.get("attempts") == 3, f"Expected 3 attempts before DLQ, got {message}"
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(1)
    raise RuntimeError(f"Failed webhook payment {payment_id} did not reach the DLQ after 3 attempts")


def main() -> None:
    wait_for_api()

    status, _, _ = request_json(f"{API_BASE_URL}/health")
    assert status == 401, f"Expected unauthenticated request to return 401, got {status}"

    status, _, body = request_json(f"{API_BASE_URL}/health", api_key=API_KEY)
    assert status == 200 and body == {"status": "ok"}, f"Unexpected health response: {status} {body}"

    key = f"ci-smoke-{uuid4()}"
    payload = {
        "amount": "1250.50",
        "currency": "RUB",
        "description": "CI smoke payment",
        "metadata": {"source": "github-actions"},
        "webhook_url": "http://webhook-test:9000/webhook",
    }
    body = create_payment(payload, key)
    payment_id = body["payment_id"]
    assert body["status"] == "pending", f"Unexpected initial status: {body}"

    replay_status, replay_headers, replay_body = request_json(
        f"{API_BASE_URL}/api/v1/payments",
        method="POST",
        payload=payload,
        api_key=API_KEY,
        idempotency_key=key,
    )
    assert replay_status == 202 and isinstance(replay_body, dict), f"Idempotent replay failed: {replay_status} {replay_body}"
    assert replay_body["payment_id"] == payment_id, "Idempotency key created a duplicate payment"
    assert replay_headers.get("idempotency-replayed") == "true", "Idempotency replay header is missing"

    altered_payload = {**payload, "description": "A different request"}
    conflict_status, _, _ = request_json(
        f"{API_BASE_URL}/api/v1/payments",
        method="POST",
        payload=altered_payload,
        api_key=API_KEY,
        idempotency_key=key,
    )
    assert conflict_status == 409, f"Expected 409 for a reused key with a different payload, got {conflict_status}"

    payment = wait_for_payment(payment_id)
    assert payment["amount"] == "1250.50", f"Unexpected payment amount: {payment}"
    assert payment["processed_at"] is not None, f"Payment has no processing timestamp: {payment}"
    wait_for_webhook(payment_id, payment["status"])

    failing_payload = {
        **payload,
        "description": "CI payment with a failing webhook",
        "webhook_url": "http://webhook-test:9000/fail",
    }
    failing_created = create_payment(failing_payload, f"ci-dlq-{uuid4()}")
    failing_payment_id = failing_created["payment_id"]
    wait_for_payment(failing_payment_id)
    wait_for_dlq(failing_payment_id)
    print(f"Smoke test passed: payment {payment_id}; failed webhook reached DLQ for {failing_payment_id}")


if __name__ == "__main__":
    main()
