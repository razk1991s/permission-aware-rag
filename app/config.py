"""הגדרות האפליקציה. כל ערך ניתן לדריסה דרך משתני סביבה או קובץ .env."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Meridian Knowledge Platform"
    environment: str = "dev"
    log_level: str = "INFO"

    # --- מסד נתונים ---
    database_url: str = "postgresql+asyncpg://rag:ragpass@localhost:5432/ragdb"
    db_echo: bool = False

    # --- אבטחה ---
    jwt_secret: str = "dev-only-change-me"
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 15
    redact_pii: bool = True
    # מקורות מותרים ל-ng serve. רלוונטי רק כש-ENVIRONMENT=dev.
    cors_origins: str = "http://localhost:4200,http://127.0.0.1:4200"

    # --- ספק מודלים ---
    # ollama = מקומי אמיתי · stub = דטרמיניסטי לבדיקות ול-CI
    llm_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    generation_model: str = "qwen2.5:7b-instruct"
    utility_model: str = "qwen2.5:3b-instruct"
    judge_model: str = "qwen2.5:3b-instruct"
    llm_timeout_seconds: float = 120.0
    llm_max_concurrency: int = 2
    daily_token_quota: int = 200_000

    # --- הטמעות ---
    embedding_provider: str = "sentence-transformers"  # או "stub"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    embedding_batch_size: int = 16
    embeddings_enabled: bool = True

    # --- חיתוך לצ'אנקים ---
    chunk_target_tokens: int = 600
    chunk_overlap_ratio: float = 0.12
    chunk_min_tokens: int = 40
    row_chunk_overlap: int = 1

    # --- שליפה ---
    retrieval_candidates: int = 30       # כמה מועמדים לפני דירוג מחדש
    retrieval_top_k: int = 5             # כמה נשלחים ל-context
    rrf_k: int = 60                      # הקבוע מהמאמר המקורי
    hybrid_enabled: bool = True
    rerank_enabled: bool = True
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    multi_query_enabled: bool = True
    max_expanded_queries: int = 3
    neighbor_expansion: bool = True      # צירוף הצ'אנקים השכנים לתוצאה המובילה

    # --- ספי סירוב וולידציה ---
    min_rerank_score: float = 0.25       # מתחתיו המערכת מסרבת לענות
    min_vector_score: float = 0.20       # סף חלופי כשהרירנקר כבוי
    groundedness_enabled: bool = True
    min_groundedness: float = 0.6
    max_context_tokens: int = 3000
    max_answer_retries: int = 1

    # --- גבולות הסוכן ---
    max_tool_calls: int = 6
    max_same_tool_calls: int = 2
    max_wall_clock_seconds: float = 45.0

    # --- שערי אישור ---
    # תקרה קשיחה: מעל הסכום הזה תמיד נדרש אישור ועדה, בלי קשר למה
    # שהשליפה החזירה מהמסמך. השליפה יכולה רק להחמיר, לא להקל.
    approval_hard_ceiling: float = 15_000.0
    approval_policy_doc: str = "FIN-001"

    # --- נתיבים ---
    corpus_dir: Path = ROOT / "data" / "corpus"
    upload_dir: Path = ROOT / "data" / "uploads"
    reports_dir: Path = ROOT / "reports"

    @property
    def is_dev(self) -> bool:
        return self.environment in {"dev", "test"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
