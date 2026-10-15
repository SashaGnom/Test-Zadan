# Асинхронный сервис процессинга платежей

Микросервис для асинхронной обработки платежей с использованием FastAPI, PostgreSQL и RabbitMQ.

## Функциональность

- Создание платежей и получение информации о них.
- Аутентификация через API-ключ `X-API-Key`.
- Защита от дублирования запросов с помощью `Idempotency-Key`.
- Асинхронная обработка платежей через RabbitMQ и FastStream.
- Эмуляция платёжного шлюза: обработка 2–5 секунд, вероятность успеха 90%.
- Outbox pattern для надёжной публикации событий.
- Повторные попытки обработки с экспоненциальной задержкой.
- Webhook-уведомления о результате платежа.
- Dead Letter Queue для сообщений, не обработанных после трёх попыток.
- Миграции базы данных через Alembic.

## Технологии

Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0, PostgreSQL, RabbitMQ, FastStream, Alembic, Docker Compose.

## Запуск

Требуются Docker и Docker Compose.

Создать файл конфигурации:

```powershell
Copy-Item .env.example .env
```

Установить собственный `API_KEY` в `.env` и запустить сервисы:

```powershell
docker compose up --build
```

API доступен по адресу `http://localhost:8000`.

## Создание платежа

```bash
curl -X POST http://localhost:8000/api/v1/payments \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-me-to-a-long-random-value" \
  -H "Idempotency-Key: order-123-payment-1" \
  -d '{
    "amount": "1250.50",
    "currency": "RUB",
    "description": "Оплата заказа 123",
    "metadata": {"order_id": 123},
    "webhook_url": "https://httpbin.org/post"
  }'
```

## Получение информации о платеже

```bash
curl http://localhost:8000/api/v1/payments/PASTE_PAYMENT_ID \
  -H "X-API-Key: change-me-to-a-long-random-value"
```

## Тесты

```bash
python -m pip install -r requirements.txt
python -m pytest -q
```

GitHub Actions автоматически запускает тесты и интеграционные проверки при изменении репозитория.
