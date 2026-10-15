# Асинхронный сервис процессинга платежей

Микросервис принимает платежи, обрабатывает их асинхронно через эмуляцию платёжного шлюза и уведомляет клиента через webhook.

## Реализовано

- Модели `payments` и `outbox`, миграции Alembic.
- `POST /api/v1/payments` — создание платежа с `202 Accepted`.
- `GET /api/v1/payments/{payment_id}` — получение детальной информации о платеже.
- Все API-эндпоинты защищены статическим ключом в `X-API-Key`.
- `Idempotency-Key` предотвращает создание повторного платежа; повтор ключа с другим телом возвращает `409 Conflict`.
- RabbitMQ/FastStream: очередь `payments.new` и один consumer. Эмуляция обработки занимает 2–5 секунд, вероятность `succeeded` — 90%, `failed` — 10%.
- Outbox pattern для надёжной публикации событий.
- Три попытки обработки/отправки webhook с экспоненциальной задержкой. После трёх неудачных попыток событие попадает в `payments.dlq`.
- Docker Compose для PostgreSQL, RabbitMQ, API, consumer и миграции; GitHub Actions для тестов и интеграционной проверки.

## Запуск

Нужны Docker и Docker Compose.

```powershell
Copy-Item .env.example .env
```

Замени `API_KEY` в `.env` перед использованием и выполни:

```powershell
docker compose up --build
```

API доступен на `http://localhost:8000`.

## Создание платежа

Укажи URL обработчика webhook, который доступен из контейнера и отвечает кодом `2xx`. В запросе ниже используется стандартный ключ; если ты изменил `API_KEY` в `.env`, подставь его значение.

```powershell
curl.exe -X POST http://localhost:8000/api/v1/payments -H "Content-Type: application/json" -H "X-API-Key: change-me-to-a-long-random-value" -H "Idempotency-Key: order-123-payment-1" -d '{"amount":"1250.50","currency":"RUB","description":"Оплата заказа 123","metadata":{"order_id":123},"webhook_url":"https://httpbin.org/post"}'
```

## Тесты

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
```