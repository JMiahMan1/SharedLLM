# services/gateway/config_validator.py
"""Critical configuration validation for gateway startup and runtime."""
import logging
from typing import Dict, List

log = logging.getLogger("gateway.config_validator")

# Priority levels for configuration keys
CRITICAL = "CRITICAL"   # Service cannot function without this
REQUIRED = "REQUIRED"   # Major functionality degraded without this
OPTIONAL = "OPTIONAL"   # Nice to have, graceful degradation possible

# Schema: key -> (priority, description, impact_if_missing)
GATEWAY_CONFIG_SCHEMA = {
    # Core infrastructure — service is non-functional without these
    "identity_svc_url": (CRITICAL, "Identity service URL", "Cannot resolve credentials or settings"),
    "redis_url": (CRITICAL, "Redis connection URL", "Job queue and history broken"),

    # LLM models — chat endpoint broken without at least one
    "assistant_model": (REQUIRED, "Assistant model", "Chat endpoint returns 503"),
    "coding_model": (REQUIRED, "Coding model", "Coding tasks fall back to assistant model"),
    "librarian_model": (REQUIRED, "Librarian model", "Librarian queries fall back to assistant model"),
    "active_llm_provider": (REQUIRED, "Active LLM provider", "Cannot route inference requests"),
    "llm_local_url": (REQUIRED, "Local LLM service URL", "Ollama inference unreachable"),

    # Service URLs — degraded but partially functional without these
    "execution_svc_url": (REQUIRED, "Execution service URL", "Tool execution broken"),
    "rag_svc_url": (OPTIONAL, "RAG service URL", "Context injection disabled"),
    "storage_svc_url": (OPTIONAL, "Storage service URL", "File storage operations broken"),
    "logging_svc_url": (OPTIONAL, "Logging service URL", "Audit logging disabled"),
    "workspace_runtime_svc_url": (OPTIONAL, "Workspace runtime URL", "Workspace operations broken"),
    "control_plane_url": (OPTIONAL, "Control plane URL", "Container management broken"),

    # Cloud provider (only needed if active_llm_provider=openrouter)
    "llm_cloud_api_key": (OPTIONAL, "Cloud LLM API key", "OpenRouter provider broken"),
}


class ConfigValidationResult:
    def __init__(self):
        self.critical_failures: List[str] = []
        self.required_failures: List[str] = []
        self.optional_failures: List[str] = []
        self.warnings: List[str] = []
        self.ok: List[str] = []

    @property
    def is_functional(self) -> bool:
        return len(self.critical_failures) == 0

    @property
    def is_degraded(self) -> bool:
        return len(self.required_failures) > 0

    def summary(self) -> str:
        parts = []
        if self.critical_failures:
            parts.append(f"CRITICAL: {len(self.critical_failures)} failures")
        if self.required_failures:
            parts.append(f"REQUIRED: {len(self.required_failures)} missing")
        if self.optional_failures:
            parts.append(f"OPTIONAL: {len(self.optional_failures)} missing")
        if not parts:
            return "All configuration validated successfully"
        return "; ".join(parts)


def validate_config(settings: Dict[str, str]) -> ConfigValidationResult:
    """Validate configuration against the gateway schema."""
    result = ConfigValidationResult()
    active_provider = settings.get("active_llm_provider", "ollama")

    for key, (priority, _description, impact) in GATEWAY_CONFIG_SCHEMA.items():
        # Skip cloud keys if not using cloud provider
        if key.startswith("llm_cloud_"):
            if active_provider != "openrouter":
                continue

        # Skip local/ollama keys if using cloud provider
        if key == "llm_local_url":
            if active_provider == "openrouter":
                continue

        value = settings.get(key, "")
        if not value or value in ("auto", "none", "null"):
            if priority == CRITICAL:
                result.critical_failures.append(f"{key}: {impact}")
                log.critical(f"[ConfigValidation] CRITICAL: {key} missing — {impact}")
            elif priority == REQUIRED:
                result.required_failures.append(f"{key}: {impact}")
                log.error(f"[ConfigValidation] REQUIRED: {key} missing — {impact}")
            else:
                result.optional_failures.append(f"{key}: {impact}")
                log.warning(f"[ConfigValidation] OPTIONAL: {key} missing — {impact}")
        else:
            result.ok.append(key)

    # Cross-validation: if active_llm_provider is missing/invalid
    if active_provider not in ("ollama", "openrouter"):
        result.critical_failures.append(
            f"active_llm_provider='{active_provider}': must be 'ollama' or 'openrouter'"
        )
        log.critical(f"[ConfigValidation] CRITICAL: Invalid active_llm_provider='{active_provider}'")

    return result
