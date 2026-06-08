"""Runtime configuration and production safety validation.

Secrets are intentionally loaded from the environment instead of being committed
as defaults.  ``APP_ENV=production`` turns on strict validation so unsafe local
development settings cannot accidentally boot in a production deployment.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    postgres_user: str = "pdfextract"
    postgres_password: str | None = None
    postgres_db: str = "pdfextract_dev"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    redis_host: str = "redis"
    redis_port: int = 6379
    
    minio_host: str = "minio"
    minio_port: int = 9000
    minio_access_key: str | None = None
    minio_secret_key: str | None = None
    minio_bucket: str = "documents"
    llm_backend: str = "ollama"
    llm_base_url: str = "http://host.docker.internal:11434/v1"
    llm_model: str = "qwen2.5vl:7b"
    jwt_secret: str | None = None
    jwt_key_id: str = "v1"
    jwt_previous_secrets: str = ""
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    seed_dev_users: bool = True
    allow_query_token_auth: bool = True
    max_pdf_bytes: int = 25 * 1024 * 1024

    # Column-level PII encryption: 64-char hex string (32 bytes / AES-256 key)
    column_encryption_key: str | None = None

    # MinIO server-side encryption
    minio_sse_enabled: bool = True

    # ClamAV malware scanning
    clamav_host: str = "clamav"
    clamav_port: int = 3310
    clamav_enabled: bool = True
    clamav_timeout: float = 10.0

    # Evidence export signing — HMAC-SHA256 key for manifest signatures.
    # Falls back to jwt_secret so no extra config is needed in development.
    audit_export_key: str | None = None

    # Structured logging format: "text" (default) or "json"
    # Set LOG_FORMAT=json in production to emit machine-readable JSON lines
    # that can be shipped by a log aggregator (Promtail, Filebeat, etc.).
    log_format: str = "text"

    ocr_fallback_enabled: bool = False
    ensemble_confidence_enabled: bool = True
    few_shot_enabled: bool = False
    few_shot_k: int = 3
    few_shot_token_budget: int = 12000

    @property
    def minio_endpoint(self) -> str:
        return f"http://{self.minio_host}:{self.minio_port}"

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"prod", "production"}

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def jwt_secret_versions(self) -> dict[str, str]:
        """Return active and previous JWT signing secrets keyed by ``kid``."""
        versions: dict[str, str] = {}
        if self.jwt_secret:
            versions[self.jwt_key_id] = self.jwt_secret
        for entry in self.jwt_previous_secrets.split(","):
            if not entry.strip():
                continue
            if ":" not in entry:
                continue
            kid, secret = entry.split(":", 1)
            if kid.strip() and secret.strip():
                versions[kid.strip()] = secret.strip()
        return versions

    def validate_production_safety(self):
        """Fail startup when production config is missing required controls."""
        if not self.is_production:
            return
        problems = []
        weak_values = {"devpassword", "minioadmin", "dev-secret-change-me", "changeme", "password", "secret"}

        required_secrets = {
            "JWT_SECRET": self.jwt_secret,
            "POSTGRES_PASSWORD": self.postgres_password,
            "MINIO_ACCESS_KEY": self.minio_access_key,
            "MINIO_SECRET_KEY": self.minio_secret_key,
        }
        for name, value in required_secrets.items():
            if not value:
                problems.append(f"{name} must be set in production")
            elif value.lower() in weak_values or len(value) < 16:
                problems.append(f"{name} must not use a known development default or weak value")

        if self.jwt_secret and len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET must be at least 32 characters in production")
        if self.seed_dev_users:
            problems.append("SEED_DEV_USERS must be false in production")
        if self.allow_query_token_auth:
            problems.append("ALLOW_QUERY_TOKEN_AUTH must be false in production")
        if "*" in self.cors_origin_list:
            problems.append("CORS_ORIGINS must not include * in production")
        if problems:
            raise RuntimeError("; ".join(problems))

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    @property
    def redis_result_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/1"


settings = Settings()
