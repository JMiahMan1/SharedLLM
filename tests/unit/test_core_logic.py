import pytest
from cryptography.fernet import Fernet


@pytest.mark.unit
class TestCryptoFunctions:
    def test_encrypt_decrypt_roundtrip(self):
        from services.identity.crypto import decrypt, encrypt

        plaintext = "secret_token_value"

        encrypted = encrypt(plaintext)
        assert encrypted != plaintext
        assert encrypted is not None

        decrypted = decrypt(encrypted)
        assert decrypted == plaintext

    def test_encrypt_produces_different_output_each_time(self):
        from services.identity.crypto import encrypt

        plaintext = "same_secret"

        encrypted1 = encrypt(plaintext)
        encrypted2 = encrypt(plaintext)

        assert encrypted1 != encrypted2

    def test_decrypt_with_invalid_token_returns_none(self):
        from services.identity.crypto import decrypt

        result = decrypt("invalid_token_value")
        assert result is None

    def test_encrypt_none_returns_none(self):
        from services.identity.crypto import encrypt

        assert encrypt(None) is None
        assert encrypt("") is None

    def test_decrypt_none_returns_none(self):
        from services.identity.crypto import decrypt

        assert decrypt(None) is None
        assert decrypt("") is None

    def test_digest_secret_produces_consistent_hash(self):
        from services.identity.crypto import digest_secret

        secret = "my_api_key"
        hash1 = digest_secret(secret)
        hash2 = digest_secret(secret)

        assert hash1 == hash2
        assert hash1 != secret

    def test_digest_secret_different_inputs(self):
        from services.identity.crypto import digest_secret

        hash1 = digest_secret("key1")
        hash2 = digest_secret("key2")

        assert hash1 != hash2


@pytest.mark.unit
class TestIntentEngine:
    def test_fast_path_light_on(self):
        from services.gateway.intent_engine import IntentEngine

        engine = IntentEngine()
        result = engine.classify("Turn on the piano lamp")
        assert result is not None

    def test_fast_path_light_off(self):
        from services.gateway.intent_engine import IntentEngine

        engine = IntentEngine()
        result = engine.classify("Turn off the kitchen light")
        assert result is not None

    def test_fast_path_media_play(self):
        from services.gateway.intent_engine import IntentEngine

        engine = IntentEngine()
        result = engine.classify("Play music")
        assert result is not None

    def test_fast_path_media_pause(self):
        from services.gateway.intent_engine import IntentEngine

        engine = IntentEngine()
        result = engine.classify("Pause the music")
        assert result is not None

    def test_fast_path_media_stop(self):
        from services.gateway.intent_engine import IntentEngine

        engine = IntentEngine()
        result = engine.classify("Stop playing")
        assert result is not None

    def test_fast_path_volume_up(self):
        from services.gateway.intent_engine import IntentEngine

        engine = IntentEngine()
        result = engine.classify("Volume up")
        assert result is not None

    def test_fast_path_volume_down(self):
        from services.gateway.intent_engine import IntentEngine

        engine = IntentEngine()
        result = engine.classify("Volume down")
        assert result is not None

    def test_fast_path_timer(self):
        from services.gateway.intent_engine import IntentEngine

        engine = IntentEngine()
        result = engine.classify("Set a timer for 5 minutes")
        assert result is not None

    def test_fast_path_calendar(self):
        from services.gateway.intent_engine import IntentEngine

        engine = IntentEngine()
        result = engine.classify("What's on my calendar today?")
        assert result is not None

    def test_fast_path_note(self):
        from services.gateway.intent_engine import IntentEngine

        engine = IntentEngine()
        result = engine.classify("Create a note about testing")
        assert result is not None


@pytest.mark.unit
class TestResolver:
    def test_resolver_imports(self):
        from services.gateway.resolver import resolve_hostname_with_fallback
        assert resolve_hostname_with_fallback is not None

    def test_resolver_returns_ip_for_localhost(self):
        import asyncio

        from services.gateway.resolver import resolve_hostname_with_fallback

        result = asyncio.run(resolve_hostname_with_fallback("localhost", port=0))
        assert result is not None
        assert result == "127.0.0.1"


@pytest.mark.unit
class TestConfigModule:
    def test_config_imports(self):
        from services.config import INTERNAL_SECRET
        assert INTERNAL_SECRET is not None

    def test_config_fernet_key_from_env(self):
        import os
        os.environ["FERNET_KEY"] = Fernet.generate_key().decode()

        from importlib import reload

        import services.config
        reload(services.config)

        from services.config import FERNET_KEY
        assert FERNET_KEY is not None


@pytest.mark.unit
class TestSanitization:
    def test_bearer_token_redaction(self):
        from services.logging.main import sanitize_log_payload

        result = sanitize_log_payload("Authorization: Bearer abc123xyz")
        assert "[REDACTED]" in result
        assert "abc123xyz" not in result

    def test_github_pat_redaction(self):
        from services.logging.main import sanitize_log_payload

        result = sanitize_log_payload("token: github_pat_1234567890abcdef")
        assert "[REDACTED]" in result
        assert "github_pat_1234567890abcdef" not in result

    def test_github_classic_token_redaction(self):
        from services.logging.main import sanitize_log_payload

        result = sanitize_log_payload("token: ghp_abc123def456")
        assert "[REDACTED]" in result

    def test_gitlab_token_redaction(self):
        from services.logging.main import sanitize_log_payload

        result = sanitize_log_payload("token: glpat-abc123def456")
        assert "[REDACTED]" in result

    def test_api_key_field_redaction(self):
        from services.logging.main import sanitize_log_payload

        result = sanitize_log_payload({"api_key": "secret_value"})
        assert result["api_key"] == "[REDACTED]"

    def test_password_field_redaction(self):
        from services.logging.main import sanitize_log_payload

        result = sanitize_log_payload({"password": "my_password"})
        assert result["password"] == "[REDACTED]"

    def test_long_message_truncation(self):
        from services.logging.main import sanitize_log_payload

        long_msg = "x" * 5000
        result = sanitize_log_payload(long_msg)
        assert len(result) <= 4014
        assert "...[TRUNCATED]" in result

    def test_safe_message_unchanged(self):
        from services.logging.main import sanitize_log_payload

        safe_msg = "This is a normal log message"
        result = sanitize_log_payload(safe_msg)
        assert result == safe_msg
