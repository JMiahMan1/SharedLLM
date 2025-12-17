
import asyncio
import os
import sys

# Add project root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

# Use settings which typically init the collection
from app.settings import GlobalResources

async def check_office_tv_integration():
    try:
        # We need to manually init if settings doesn't do it automatically in script context
        # But GlobalResources.ha_collection should be lazy loaded or we can init it.
        # Let's try to just access it, if it's None, we init.
        
        if not GlobalResources.ha_collection:
             from langchain_chroma import Chroma
             from langchain_huggingface import HuggingFaceEmbeddings
             
             persist_dir = "data/chroma_db"
             embedding_func = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
             GlobalResources.ha_collection = Chroma(
                collection_name="home_assistant_entities",
                embedding_function=embedding_func,
                persist_directory=persist_dir
             )

        coll = GlobalResources.ha_collection
        # The underlying client might be accessible or we just use similarity_search to check logic
        # But we want direct metadata.
        # langchain Chroma wrapper has .get() usually?
        
        # Try direct get
        results = coll.get(
            where={"entity_id": "media_player.office_tv"},
            include=["metadatas"]
        )
        
        print("\n--- ChromaDB Metadata for media_player.office_tv ---")
        if results and results["metadatas"]:
            print(results["metadatas"][0])
        else:
            print("No metadata found.")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(check_office_tv_integration())
