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

    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-5", validation_alias="ANTHROPIC_MODEL")
    max_tokens: int = Field(default=1024, ge=64, validation_alias="LLM_MAX_TOKENS")

    featherless_api_key: SecretStr | None = Field(default=None, validation_alias="FEATHERLESS_API_KEY")
    featherless_base_url: str = Field(
        default="https://api.featherless.ai/v1", validation_alias="FEATHERLESS_BASE_URL"
    )
    featherless_model: str = Field(
        default="meta-llama/Llama-3.3-70B-Instruct", validation_alias="FEATHERLESS_MODEL"
    )

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
    def llm_configured(self) -> bool:
        return self.featherless_api_key is not None or self.anthropic_api_key is not None

    @property
    def primary_provider(self) -> str | None:
        # Featherless é o primário (perk do evento); Anthropic entra como fallback
        if self.featherless_api_key is not None:
            return "featherless"
        if self.anthropic_api_key is not None:
            return "anthropic"
        return None

    @property
    def primary_model(self) -> str:
        return self.featherless_model if self.primary_provider == "featherless" else self.anthropic_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
