from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Paperlib"
    database_url: str = "postgresql+psycopg://paperlib:paperlib_dev@localhost:54329/paperlib"
    storage_path: Path = Path("data/files")
    figure_path: Path = Path("data/figures")
    model_cache_path: Path = Path("data/models")
    llm_config_path: Path = Path("data/private/llm.json")
    semantic_scholar_config_path: Path = Path("data/private/semantic_scholar.json")
    zotero_config_path: Path = Path("data/private/zotero.json")

    embedding_provider: str = "fastembed"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dimension: int = Field(default=384, ge=1, le=4096)
    search_candidate_limit: int = Field(default=50, ge=10, le=200)


@lru_cache
def get_settings() -> Settings:
    return Settings()
