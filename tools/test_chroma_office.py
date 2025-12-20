import sys
import os
import asyncio

# Add app to path
sys.path.append(os.getcwd())

from app.settings import GlobalResources, load_resources

async def check_chroma():
    await load_resources()
    
    print("Checking ChromaDB for Office TV...")
    try:
        if GlobalResources.ha_collection:
            # Query by text first to see what search finds
            print("\n--- Search Results for 'Office TV' ---")
            results = await GlobalResources.ha_collection.asimilarity_search_with_score("Office TV", k=5)
            for doc, score in results:
                print(f"ID: {doc.metadata.get('entity_id')}")
                print(f"Integration: {doc.metadata.get('integration')}")
                print(f"Group: {doc.metadata.get('group_id')}")
                print(f"Attributes: {doc.metadata.get('attributes')}")
                print(f"Score: {score}")
                print("-" * 20)

                # If group found, query group
                gid = doc.metadata.get("group_id")
                if gid and gid != "unknown":
                    print(f"\n--- Group Members for {gid} ---")
                    # Access internal collection directly
                    group_res = GlobalResources.ha_collection._collection.get(where={"group_id": gid})
                    if group_res and group_res.get("ids"):
                        for i, eid in enumerate(group_res["ids"]):
                            meta = group_res["metadatas"][i]
                            print(f"Member ID: {eid}")
                            print(f"Integration: {meta.get('integration')}")
                            print(f"Attributes: {meta.get('attributes')}")
                            print("-" * 10)
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(check_chroma())
