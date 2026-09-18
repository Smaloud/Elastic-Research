from __future__ import annotations

import threading
from functools import lru_cache
from typing import Any

from app.config import Settings, get_settings


class EmbeddingUnavailable(RuntimeError):
    pass


class Embedder:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError


class DisabledEmbedder(Embedder):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingUnavailable("向量模型已禁用；请设置 EMBEDDING_PROVIDER=fastembed")

    def embed_query(self, text: str) -> list[float]:
        raise EmbeddingUnavailable("向量模型已禁用；请设置 EMBEDDING_PROVIDER=fastembed")


class FastEmbedEmbedder(Embedder):
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model: Any = None
        self._lock = threading.Lock()

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                try:
                    from fastembed import TextEmbedding

                    self.settings.model_cache_path.mkdir(parents=True, exist_ok=True)
                    self._model = TextEmbedding(
                        model_name=self.settings.embedding_model,
                        cache_dir=str(self.settings.model_cache_path),
                    )
                except Exception as exc:  # model download/runtime errors vary by platform
                    raise EmbeddingUnavailable(f"本地向量模型初始化失败：{exc}") from exc
        return self._model

    def _validate(self, vectors: list[list[float]]) -> list[list[float]]:
        if vectors and len(vectors[0]) != self.settings.embedding_dimension:
            raise EmbeddingUnavailable(
                "向量维度不匹配："
                f"模型返回 {len(vectors[0])}，数据库配置为 {self.settings.embedding_dimension}"
            )
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            vectors = [vector.tolist() for vector in self._get_model().embed(texts)]
        except EmbeddingUnavailable:
            raise
        except Exception as exc:
            raise EmbeddingUnavailable(f"文档向量生成失败：{exc}") from exc
        return self._validate(vectors)

    def embed_query(self, text: str) -> list[float]:
        vectors = self.embed_documents([text])
        return vectors[0]


@lru_cache
def get_embedder() -> Embedder:
    settings = get_settings()
    provider = settings.embedding_provider.casefold()
    if provider in {"none", "disabled", "off"}:
        return DisabledEmbedder()
    if provider == "fastembed":
        return FastEmbedEmbedder(settings)
    raise EmbeddingUnavailable(f"不支持的 EMBEDDING_PROVIDER：{settings.embedding_provider}")
