from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    postgres_user: str = "pdfextract"
    postgres_password: str = "devpassword"
    postgres_db: str = "pdfextract_dev"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    redis_host: str = "redis"
    redis_port: int = 6379
    
    minio_host: str = "minio"
    minio_port: int = 9000
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "documents"
    llm_backend: str = "ollama"
    llm_base_url: str = "http://host.docker.internal:11434/v1"
    llm_model: str = "qwen2.5vl:7b"
    jwt_secret: str = "dev-secret-change-me"
    access_token_minutes: int = 480
    seed_dev_users: bool = True
    allow_query_token_auth: bool = True
    max_pdf_bytes: int = 25 * 1024 * 1024
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

    def validate_production_safety(self):
        if not self.is_production:
            return
        problems = []
        if self.jwt_secret == "dev-secret-change-me" or len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET must be set to a strong production secret")
        if self.seed_dev_users:
            problems.append("SEED_DEV_USERS must be false in production")
        if self.allow_query_token_auth:
            problems.append("ALLOW_QUERY_TOKEN_AUTH must be false in production")
        if self.postgres_password == "devpassword":
            problems.append("POSTGRES_PASSWORD must not use the development default in production")
        if self.minio_access_key == "minioadmin" or self.minio_secret_key == "minioadmin":
            problems.append("MinIO credentials must not use development defaults in production")
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
