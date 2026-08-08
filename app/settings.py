from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="dev", validation_alias="APP_ENV")
    database_url: str = Field(
        default="sqlite:///./quote_agent.db", validation_alias="DATABASE_URL"
    )
    cors_origins: str = Field(default="*", validation_alias="CORS_ORIGINS")

    llm_provider: str = Field(default="featherless", validation_alias="LLM_PROVIDER")
    max_tokens: int = Field(default=1024, ge=64, validation_alias="LLM_MAX_TOKENS")

    featherless_api_key: SecretStr | None = Field(default=None, validation_alias="FEATHERLESS_API_KEY")
    featherless_base_url: str = Field(
        default="https://api.featherless.ai/v1", validation_alias="FEATHERLESS_BASE_URL"
    )
    featherless_model: str = Field(
        default="Qwen/Qwen2.5-7B-Instruct", validation_alias="FEATHERLESS_MODEL"
    )
    featherless_temperature: float = Field(default=0, ge=0, le=2, validation_alias="FEATHERLESS_TEMPERATURE")

    wa_service_url: str | None = Field(default=None, validation_alias="WA_SERVICE_URL")
    wa_shared_token: SecretStr | None = Field(default=None, validation_alias="WA_SHARED_TOKEN")

    langfuse_public_key: str | None = Field(default=None, validation_alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: SecretStr | None = Field(default=None, validation_alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="https://us.cloud.langfuse.com", validation_alias="LANGFUSE_HOST")

    @model_validator(mode="before")
    @classmethod
    def _drop_empty_strings(cls, values: dict) -> dict:
        # env vars declaradas mas vazias não devem sobrescrever defaults
        if isinstance(values, dict):
            return {k: v for k, v in values.items() if v != ""}
        return values

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def wa_configured(self) -> bool:
        return bool(self.wa_service_url and self.wa_shared_token)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def active_model(self) -> str:
        return self.featherless_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
