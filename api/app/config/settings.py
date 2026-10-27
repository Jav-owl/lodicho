from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Postgres ---
    database_url: str = ""

    # --- Redis ---
    redis_url: str = ""

    # --- Qdrant ---
    qdrant_url: str = ""
    qdrant_api_key: str = ""

    # --- Gemini ---
    gemini_api_key: str = ""

    # --- Ingesta (n8n / GitHub webhook) ---
    github_webhook_secret: str = ""
    ingest_api_token: str = ""

    # --- CORS ---
    allowed_origins: str = ""  # separados por coma, ej: "https://lodicho.zamarrito.com,http://localhost:8000"

    # --- Salvaguarda legal ---
    modo_silencio_electoral: bool = False

    # --- Panel de admin (carga de corpus desde el front) ---
    admin_password: str = ""
    admin_session_secret: str = ""  # HMAC de las sesiones; distinto de admin_password
    admin_session_horas: int = 12
    corpus_path: str = "/corpus"  # mismo mount que usa scripts/ingest_corpus.py
    corpus_git_ssh_key: str = "/run/secrets/corpus_deploy_key"
    corpus_git_remote: str = ""  # ej: git@github.com:Jav-owl/lodicho-corpus.git

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origen.strip() for origen in self.allowed_origins.split(",") if origen.strip()]


settings = Settings()
