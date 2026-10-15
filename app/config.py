from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://payments:payments@localhost:5432/payments"
    rabbitmq_url: str = "amqp://payments:payments@localhost:5672/"
    api_key: str = Field(default="change-me-to-a-long-random-value", min_length=1)
    payments_queue: str = "payments.new"
    payments_dlq: str = "payments.dlq"
    outbox_poll_interval: float = Field(default=1.0, gt=0)
    webhook_timeout: float = Field(default=5.0, gt=0)


settings = Settings()
