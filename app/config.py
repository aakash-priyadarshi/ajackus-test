from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql+psycopg://kindred:kindred@localhost:5432/kindred"
    embed_dim: int = 16
    payment_timeout_trigger_cents: int = 999999
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"


settings = Settings()
