# -*- coding: utf-8 -*-
import secrets
import base64
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# --- Project Imports ---
# ***** ADD THIS LINE *****
from utils.logger import log_info, log_warning, log_error
# *************************
from utils.constants import API_KEYS_FILE, DEFAULT_API_KEY_PLACEHOLDER
from utils.helpers import load_json_file, save_json_file

# IMPORTANT: This is a basic encryption example... (rest of comments)
try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    ENCRYPTION_AVAILABLE = True
    # Now this call is valid because log_info is imported above
    log_info("Cryptography library found. Using Fernet for API key encryption.")
except ImportError:
    ENCRYPTION_AVAILABLE = False
    # Now this call is valid because log_warning is imported above
    log_warning("Cryptography library not found. API keys will be stored obfuscated (XOR) NOT securely encrypted. Install with: pip install cryptography")


# --- Simple XOR Obfuscation (Fallback if cryptography is not installed) ---
_OBFUSCATION_KEY = b"a_simple_key_for_xor_obfuscation" # Keep this consistent

def _xor_cipher(data: bytes, key: bytes) -> bytes:
    """Simple XOR cipher for basic obfuscation."""
    key_len = len(key)
    return bytes(data[i] ^ key[i % key_len] for i in range(len(data)))

def _obfuscate(text: str) -> str:
    """Obfuscates text using XOR and encodes to base64."""
    if not text:
        return ""
    obfuscated_bytes = _xor_cipher(text.encode('utf-8'), _OBFUSCATION_KEY)
    return base64.urlsafe_b64encode(obfuscated_bytes).decode('utf-8')

def _deobfuscate(obfuscated_text: str) -> str:
    """Deobfuscates text previously obfuscated with _obfuscate."""
    if not obfuscated_text:
        return ""
    try:
        obfuscated_bytes = base64.urlsafe_b64decode(obfuscated_text.encode('utf-8'))
        deobfuscated_bytes = _xor_cipher(obfuscated_bytes, _OBFUSCATION_KEY)
        return deobfuscated_bytes.decode('utf-8')
    except Exception as e:
        # log_error is now imported
        log_error(f"Failed to deobfuscate key: {e}")
        return "" # Return empty string on failure

# --- Fernet Encryption (Preferred if cryptography is installed) ---
_SALT_SIZE = 16
_PASSWORD = b"app-specific-password-change-me" # Ideally get this from user or secure storage

def _derive_key(salt: bytes) -> bytes:
    """Derives a Fernet key from the password and salt."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480000, # NIST recommended minimum iterations
    )
    return base64.urlsafe_b64encode(kdf.derive(_PASSWORD))

def _encrypt(text: str) -> Optional[str]:
    """Encrypts text using Fernet if available."""
    if not ENCRYPTION_AVAILABLE or not text:
        return _obfuscate(text) # Fallback to obfuscation

    try:
        salt = secrets.token_bytes(_SALT_SIZE)
        key = _derive_key(salt)
        f = Fernet(key)
        encrypted_data = f.encrypt(text.encode('utf-8'))
        # Prepend salt to the encrypted data for storage
        return base64.urlsafe_b64encode(salt + encrypted_data).decode('utf-8')
    except Exception as e:
        # log_error is now imported
        log_error(f"Encryption failed: {e}")
        return None

def _decrypt(encrypted_text: str) -> str:
    """Decrypts text using Fernet if available."""
    if not encrypted_text:
        return ""

    try:
        # Attempt decryption first
        if ENCRYPTION_AVAILABLE:
            encrypted_data_with_salt = base64.urlsafe_b64decode(encrypted_text.encode('utf-8'))
            salt = encrypted_data_with_salt[:_SALT_SIZE]
            encrypted_data = encrypted_data_with_salt[_SALT_SIZE:]
            key = _derive_key(salt)
            f = Fernet(key)
            decrypted_bytes = f.decrypt(encrypted_data)
            return decrypted_bytes.decode('utf-8')
        else:
            # If decryption failed or not available, try deobfuscation
            return _deobfuscate(encrypted_text)
    except InvalidToken:
        # log_warning is now imported
        log_warning(f"Invalid token during decryption, attempting deobfuscation for potential legacy key.")
        return _deobfuscate(encrypted_text) # Fallback to deobfuscation
    except Exception as e:
        # log_error is now imported
        log_error(f"Decryption/Deobfuscation failed: {e}")
        return ""


class ApiKeyService:
    """Manages storage and retrieval of API keys."""

    def __init__(self, filepath: Path = API_KEYS_FILE):
        self.filepath = filepath
        self._keys: Dict[str, str] = self._load_keys() # Stores NAME -> ENCRYPTED_KEY

    def _load_keys(self) -> Dict[str, str]:
        """Loads encrypted keys from the JSON file."""
        data = load_json_file(self.filepath, default={})
        if isinstance(data, dict):
            return data
        # log_error is now imported
        log_error("API keys file is corrupt or not a dictionary. Returning empty.")
        return {}

    def _save_keys(self) -> bool:
        """Saves the current state of encrypted keys to the JSON file."""
        return save_json_file(self.filepath, self._keys)

    def add_or_update_key(self, name: str, key_value: str) -> bool:
        """Adds a new key or updates an existing one."""
        if not name or not key_value:
            # log_error is now imported
            log_error("API key name and value cannot be empty.")
            return False
        if name == DEFAULT_API_KEY_PLACEHOLDER:
             # log_error is now imported
             log_error(f"Cannot use the placeholder name '{DEFAULT_API_KEY_PLACEHOLDER}'.")
             return False

        encrypted_value = _encrypt(key_value)
        if encrypted_value is None:
            # log_error is now imported
            log_error(f"Failed to encrypt API key '{name}'.")
            return False

        self._keys[name] = encrypted_value
        # log_info is now imported
        log_info(f"API key '{name}' added/updated.")
        return self._save_keys()

    def get_key_value(self, name: str) -> Optional[str]:
        """Retrieves and decrypts the value of a specific API key."""
        encrypted_value = self._keys.get(name)
        if encrypted_value:
            decrypted = _decrypt(encrypted_value)
            if decrypted:
                return decrypted
            else:
                # log_error is now imported
                log_error(f"Failed to decrypt key '{name}'. It might be corrupted or use an old format/password.")
                return None # Indicate decryption failure
        return None

    def remove_key(self, name: str) -> bool:
        """Removes an API key by name."""
        if name in self._keys:
            del self._keys[name]
            # log_info is now imported
            log_info(f"API key '{name}' removed.")
            return self._save_keys()
        # log_warning is now imported
        log_warning(f"Attempted to remove non-existent API key '{name}'.")
        return False

    def get_key_names(self) -> List[str]:
        """Returns a list of all stored API key names."""
        return sorted(list(self._keys.keys()))

    def get_all_decrypted_keys(self) -> Dict[str, str]:
         """Returns a dictionary of all decrypted keys. Use with caution!"""
         decrypted_keys = {}
         for name in self.get_key_names():
             value = self.get_key_value(name)
             if value is not None: # Only include successfully decrypted keys
                 decrypted_keys[name] = value
             else:
                 decrypted_keys[name] = "**DECRYPTION FAILED**" # Placeholder for UI
         return decrypted_keys