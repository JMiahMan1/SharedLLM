"""The private chat's "Unlock with this phone's lock" (DeviceUnlockPlugin).

Source checks, since the APK is built in CI: the plugin is registered before
the bridge starts, the Keystore key only works right after the phone's lock is
passed (every use), a new fingerprint or face destroys it, the plain key is
never stored, and a destroyed key is reported and forgotten.
"""
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "services/ui/android/app"
JAVA = APP / "src/main/java/com/jarvisos/app"


def _plugin() -> str:
    return (JAVA / "DeviceUnlockPlugin.java").read_text()


def test_the_plugin_is_registered_before_the_bridge_starts():
    main = (JAVA / "MainActivity.java").read_text()
    register = main.index("registerPlugin(DeviceUnlockPlugin.class);")
    assert register < main.index("super.onCreate(savedInstanceState);")
    assert '@CapacitorPlugin(name = "JarvisDeviceUnlock")' in _plugin()


def test_the_keystore_key_needs_the_phone_lock_for_every_use():
    code = _plugin()
    assert '"AndroidKeyStore"' in code
    assert ".setUserAuthenticationRequired(true)" in code
    assert re.search(
        r"\.setUserAuthenticationParameters\(\s*0\s*,\s*KeyProperties\.AUTH_BIOMETRIC_STRONG\s*\|\s*KeyProperties\.AUTH_DEVICE_CREDENTIAL\s*\)",
        code,
    )
    assert ".setInvalidatedByBiometricEnrollment(true)" in code
    assert ".setKeySize(256)" in code and "BLOCK_MODE_GCM" in code


def test_the_key_is_used_only_through_the_prompt_and_never_stored_plain():
    code = _plugin()
    assert "new BiometricPrompt.CryptoObject(cipher)" in code
    assert 'setTitle(title)' in code and '"Unlock private chat"' in code
    assert "BIOMETRIC_STRONG | BiometricManager.Authenticators.DEVICE_CREDENTIAL" in code
    assert "isDeviceSecure()" in code
    # Only the ciphertext (IV and ct) goes into preferences, and the plain bytes are zeroed.
    puts = re.findall(r"\.putString\(([^;]+)\);", code)
    assert puts and all("plain" not in p for p in puts)
    assert code.count("Arrays.fill(plain, (byte) 0)") >= 3


def test_a_destroyed_key_is_reported_and_forgotten():
    code = _plugin()
    block = code[code.index("catch (KeyPermanentlyInvalidatedException e)"):]
    block = block[: block.index("return;")]
    assert "forget(token)" in block and "INVALIDATED" in block
    assert "set it up again with the password" in code


def test_androidx_biometric_is_on_the_app_classpath():
    gradle = (APP / "build.gradle").read_text()
    assert 'implementation "androidx.biometric:biometric:' in gradle
