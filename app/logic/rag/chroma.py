"""RAG / ChromaDB client module.

Provides ChromaDBClient for interacting with the Chroma vector database.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

log = logging.getLogger("app.logic.rag.chroma")


class ChromaDBClient:
    """
    Client for interacting with ChromaDB vector database.
    """

    def __init__(self, collection_name: str = "capabilities", persist_path: str | None = None) -> None:
        self.collection_name = collection_name
        self.persist_path = persist_path
        self._client: Any | None = None
        self._collection: Any | None = None

    def connect(self) -> bool:
        """Connect to the ChromaDB instance."""
        try:
            import chromadb as _chromadb
            self._client = (
                _chromadb.PersistentClient(path=self.persist_path)  # type: ignore[no-any-return]
                if self.persist_path
                else _chromadb.Client()
            )
            self._collection = self._client.get_or_create_collection(self.collection_name)
            return True
        except Exception as e:
            log.warning(f"ChromaDB connection failed: {e}")
            return False

    def add_documents(
        self,
        documents: list[str],
        ids: list[str] | None = None,
        metadatas: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Add documents to the collection."""
        if self._collection is None:
            if not self.connect():
                return False

        if ids is None:
            ids = [f"doc_{i}" for i in range(len(documents))]

        try:
            assert self._collection is not None
            self._collection.add(
                documents=documents,
                ids=ids,
                metadatas=metadatas,
            )
            return True
        except Exception as e:
            log.error(f"Failed to add documents: {e}")
            return False

    def search(
        self,
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Search the collection for similar documents."""
        if self._collection is None:
            if not self.connect():
                return []

        try:
            assert self._collection is not None
            results = self._collection.query(
                query_texts=[query],
                n_results=top_k,
                where=filters,
            )
            ids_data = results.get("ids", [[]])[0]
            docs_data = results.get("documents", [[]])[0]
            metas_data = results.get("metadatas", [[]])[0]
            dists_data = results.get("distances", [[]])[0]
            return [
                {
                    "id": doc_id,
                    "document": doc,
                    "metadata": meta or {},
                    "distance": dist,
                }
                for doc_id, doc, meta, dist in zip(ids_data, docs_data, metas_data, dists_data)
            ]
        except Exception as e:
            log.error(f"Search failed: {e}")
            return []

    def count(self) -> int:
        """Return the number of documents in the collection."""
        if self._collection is None:
            if not self.connect():
                return 0
        try:
            assert self._collection is not None
            return self._collection.count()
        except Exception:
            return 0
