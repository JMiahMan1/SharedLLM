import sys
import os
import logging
import asyncio
import importlib.util
import types

# Configure logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

def load_module_from_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

async def run_debug(device_name, intent="play_media"):
    print(f"--- DIRECT DEBUG: {device_name} ---")
    
    # 1. Setup Environment
    if "/" not in sys.path:
        sys.path.append("/")
    
    # 2. Mock 'app' package structure to support relative imports and bypass circulars
    
    # Create 'app'
    if "app" not in sys.modules:
        app = types.ModuleType("app")
        app.__path__ = ["/app"]
        sys.modules["app"] = app
    else:
        app = sys.modules["app"]
        
    # Create 'app.logic' (Mocked to be empty/safe)
    app_logic = types.ModuleType("app.logic")
    app_logic.__path__ = ["/app/logic"]
    sys.modules["app.logic"] = app_logic
    app.logic = app_logic
    
    # Create 'app.domains'
    app_domains = types.ModuleType("app.domains")
    app_domains.__path__ = ["/app/domains"]
    sys.modules["app.domains"] = app_domains
    app.domains = app_domains
    
    # Create 'app.domains.media'
    app_domains_media = types.ModuleType("app.domains.media")
    app_domains_media.__path__ = ["/app/domains/media"]
    sys.modules["app.domains.media"] = app_domains_media
    app_domains.media = app_domains_media

    # 3. Manually load dependencies
    print("Loading dependencies...")
    try:
        # Load settings
        settings = load_module_from_file("app.settings", "/app/settings.py")
        app.settings = settings
        
        # Load logic/pattern_matching
        pat_match = load_module_from_file("app.logic.pattern_matching", "/app/logic/pattern_matching.py")
        app_logic.pattern_matching = pat_match
        
        # Load integrations (dependency of devices)
        print("Loading integrations.py...")
        integrations = load_module_from_file("app.domains.media.integrations", "/app/domains/media/integrations.py")
        app_domains_media.integrations = integrations

        # Load devices.py
        print("Loading devices.py...")
        devices = load_module_from_file("app.domains.media.devices", "/app/domains/media/devices.py")
        app_domains_media.devices = devices
        
        smart_resolve_entity = devices.smart_resolve_entity
        get_device_capabilities = devices.get_device_capabilities
        
    except Exception as e:
        print(f"Import Error: {e}")
        import traceback
        traceback.print_exc()
        return

    # 4. Execute Logic
    print("Executing Smart Resolve...")
    try:
        # Need to init GlobalResources if possible?
        # settings.GlobalResources might need connection
        # The script assumes existing Chroma connection or lazily inits
        # We might need to manually trigger init if it's not autosetup
        
        # The container usually runs main.py which inits these.
        # We are bypassing main.
        # Let's see if we can manually init.
        
        from langchain_chroma import Chroma
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        
        # Attempt minimal init of GlobalResources
        # Assuming environment variables are set in container
        if not hasattr(settings.GlobalResources, "ha_collection") or not settings.GlobalResources.ha_collection:
             print("Initializing ChromaDB connection...")
             embeddings = GoogleGenerativeAIEmbeddings(model="models/embedding-001")
             settings.GlobalResources.ha_collection = Chroma(
                collection_name="home_assistant_entities",
                embedding_function=embeddings,
                persist_directory="./data/chroma_db" 
             )

        resolved = await smart_resolve_entity(
            device_name, 
            intent, 
            settings.GlobalResources.ha_collection, 
            is_music=True,
            is_video=False,
            allow_multiple=True
        )
        
        print(f"Resolved: {resolved}")
        
        if resolved:
            entities = resolved if isinstance(resolved, list) else ([resolved] if isinstance(resolved, tuple) else [(resolved, "unknown")])
            for eid, integ in entities:
                print(f"  Entity: {eid} | Integration: {integ}")
                
                # Check MASS Swap logic manually
                if integ != "music_assistant":
                    search_name = device_name # clean this
                    print(f"  Simulating Swap Search for '{search_name}'...")
                    
                    ma_docs = settings.GlobalResources.ha_collection.similarity_search(f"{search_name} music assistant", k=3)
                    for d in ma_docs:
                         print(f"    Found: {d.metadata.get('entity_id')} ({d.metadata.get('integration')})")

    except Exception as e:
        print(f"Execution Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    import sys
    name = sys.argv[1] if len(sys.argv) > 1 else "Office TV"
    asyncio.run(run_debug(name))
