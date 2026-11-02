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

    # --- Embeddings: proveedor activo ("gemini" | "openai") ---
    # Cambiarlo obliga a reindexar todo el corpus (ver services/embeddings.py).
    embedding_provider: str = "gemini"
    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-3-small"

    # --- Limites de entrada (cada consulta cuesta dinero, ver docs/costos-consultas.md) ---
    max_chars_consulta: int = 1000
    audio_max_segundos: int = 60
    audio_max_bytes: int = 10 * 1024 * 1024
    audio_path: str = "/audio"  # volumen donde se archiva el audio original (evidencia)

    # --- Transcripcion de audio: proveedor activo ("openai" | "gemini") ---
    # Precios en USD por minuto de audio: ESTIMADOS, verificar contra las paginas de
    # precios de cada proveedor antes de confiar en el costo que reporta la API.
    # Gemini usa el modelo Flash de services/generacion.py.
    transcription_provider: str = "openai"
    openai_transcribe_model: str = "gpt-4o-mini-transcribe"
    openai_transcribe_usd_por_min: float = 0.003
    gemini_transcribe_usd_por_min: float = 0.002

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
