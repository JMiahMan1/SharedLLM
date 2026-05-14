import requests

RAG_URL = "http://localhost:8004"
SECRET = os.getenv("INTERNAL_SECRET")

def test_rag():
    print("--- 2. Testing RAG Capabilities ---")
    headers = {"X-Internal-Secret": SECRET}
    payload = {
        "collection_name": "system_capabilities",
        "query": "Execute the StorageIndexRequest tool for the path /Notes",
        "user_id": "default",
        "k": 5
    }
    
    try:
        resp = requests.post(f"{RAG_URL}/rag/search", json=payload, headers=headers, timeout=5)
        if resp.status_code == 200:
            hits = resp.json().get("results", [])
            print(f"RAG returned {len(hits)} hits.")
            for i, hit in enumerate(hits):
                print(f"[{i}] {hit['content'][:150]}...")
        else:
            print(f"RAG Error: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"Failed to connect to RAG: {e}")

test_rag()
