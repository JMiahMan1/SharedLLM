import os

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
IDENTITY_SVC = os.getenv("IDENTITY_SVC_URL", "http://identity:8001")
EXECUTION_SVC = os.getenv("EXECUTION_SVC_URL", "http://execution:8003")
RAG_SVC = os.getenv("RAG_SVC_URL", "http://rag:8004")
STORAGE_SVC = os.getenv("STORAGE_SVC_URL", "http://storage:8005")
LOGGING_SVC = os.getenv("LOGGING_SVC_URL", "http://logging:8006")
WORKSPACE_RUNTIME_SVC = os.getenv("WORKSPACE_RUNTIME_SVC_URL", "http://workspace_runtime:8007")

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
OLLAMA_TIMEOUT = 180.0

# Raven job constraints
RAVEN_MAX_TOTAL_SECONDS = int(os.getenv("RAVEN_MAX_TOTAL_SECONDS", "1800"))  # 30 minutes for 35B
RAVEN_ITERATION_TIMEOUT = int(os.getenv("RAVEN_ITERATION_TIMEOUT", "600"))  # 10 minutes per iteration
RAVEN_HEARTBEAT_INTERVAL = int(os.getenv("RAVEN_HEARTBEAT_INTERVAL", "30"))  # seconds
RAVEN_HUNG_THRESHOLD = int(os.getenv("RAVEN_HUNG_THRESHOLD", "600"))  # seconds

CONFIG = {
    "assistant_model": os.getenv("ASSISTANT_MODEL", ""),
    "librarian_model": os.getenv("LIBRARIAN_MODEL", ""),
    "coding_model": os.getenv("CODING_MODEL", ""),
}
