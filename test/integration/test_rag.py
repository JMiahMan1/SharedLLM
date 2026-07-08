import httpx
import pytest

# RAG Service URL
RAG_URL = "http://localhost:11438"

@pytest.mark.server_only
@pytest.mark.live
@pytest.mark.asyncio
async def test_hybrid_rag_fusion_precision():
    """
    Test that Hybrid Search correctly prioritizes lexical matches (BM25)
    for specific entity IDs that might have weak semantic embeddings.
    """
    user_id = "test_hybrid_user"
    specific_id = "ERROR_CODE_409X_CRITICAL"
    content = f"The resolution for {specific_id} is to restart the Gateway service and purge the Redis cache."

    # 1. Ingest specific knowledge
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{RAG_URL}/rag/ingest",
            json={
                "user_id": user_id,
                "content": content,
                "collection_name": "system_docs",
                "metadata": {"source": "manual", "code": specific_id}
            }
        )

        # 2. Query with the specific ID
        # Vector search might rank other "error" docs higher,
        # but BM25 should nail this specific string.
        resp = await client.post(
            f"{RAG_URL}/rag/search",
            json={
                "user_id": user_id,
                "query": f"What do I do about {specific_id}?",
                "collection_name": "system_docs",
                "alpha": 0.3, # Favor lexical (BM25)
                "use_rrf": True
            }
        )

        assert resp.status_code == 200
        results = resp.json().get("results", [])

        # 3. Assert top result is the correct one
        assert len(results) > 0
        top_hit = results[0]["content"]
        assert specific_id in top_hit
        assert "restart the Gateway" in top_hit
