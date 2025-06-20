# -*- coding: utf-8 -*-
from pathlib import Path
from typing import Dict, Any, Optional
from utils import constants
# Need to import SDK types here for safety setting serialization/deserialization
try:
    from google.genai import types as google_types
    SDK_TYPES_AVAILABLE = True

    # Helper mapping for JSON serialization/deserialization
    # Store enum names as strings in JSON
    HARM_CATEGORY_TO_STR = {v: k for k, v in google_types.HarmCategory.__members__.items()}
    STR_TO_HARM_CATEGORY = {k: v for k, v in google_types.HarmCategory.__members__.items()}
    HARM_THRESHOLD_TO_STR = {v: k for k, v in google_types.HarmBlockThreshold.__members__.items()}
    STR_TO_HARM_THRESHOLD = {k: v for k, v in google_types.HarmBlockThreshold.__members__.items()}

except ImportError:
    SDK_TYPES_AVAILABLE = False
    # Define dummy mappings if SDK fails
    HARM_CATEGORY_TO_STR = {}
    STR_TO_HARM_CATEGORY = {}
    HARM_THRESHOLD_TO_STR = {}
    STR_TO_HARM_THRESHOLD = {}
    # Define dummy types needed
    class DummyGoogleTypes:
         HarmCategory = type('HarmCategory', (), {})
         HarmBlockThreshold = type('HarmBlockThreshold', (), {})
    google_types = DummyGoogleTypes()


from utils.constants import (
    SETTINGS_FILE, DEFAULT_THEME, DEFAULT_LOGGING_ENABLED,
    DEFAULT_AUTO_SAVE_ENABLED, DEFAULT_RETRY_COUNT, DEFAULT_RETRY_DELAY,
    DEFAULT_REQUEST_DELAY, DEFAULT_FILENAME_PATTERN, DEFAULT_FILENAME_PATTERN_NAME, SAVED_FILENAME_PATTERNS_KEY, ACTIVE_FILENAME_PATTERN_NAME_KEY
)
from utils.helpers import load_json_file, save_json_file
from utils.logger import log_info, log_error, log_warning, set_logging_enabled, is_logging_enabled


class SettingsService:
    """Manages application settings."""

    def __init__(self, filepath: Path = SETTINGS_FILE):
        self.filepath = filepath
        self.settings: Dict[str, Any] = self._load_settings()
        # Apply loaded logging setting immediately
        set_logging_enabled(self.get_setting("logging_enabled", DEFAULT_LOGGING_ENABLED))




    def _serialize_safety_settings(self, settings_dict: Optional[Dict[google_types.HarmCategory, google_types.HarmBlockThreshold]]) -> Optional[Dict[str, str]]:
        """Converts safety setting enums to strings for JSON. Handles None and empty dict."""
        if settings_dict is None:
            return None
        if not settings_dict: # Handle empty dict explicitly
             return {}
        if not SDK_TYPES_AVAILABLE:
            log_warning("SDK types not available during safety setting serialization.")
            return {} # Return empty dict if SDK missing but input wasn't None

        serialized = {}
        for category_enum, threshold_enum in settings_dict.items():
            cat_str = HARM_CATEGORY_TO_STR.get(category_enum)
            thresh_str = HARM_THRESHOLD_TO_STR.get(threshold_enum)
            if cat_str and thresh_str:
                serialized[cat_str] = thresh_str
            else:
                log_warning(f"Could not serialize safety setting: {category_enum}, {threshold_enum}")
        # Return the populated dict (could be empty if serialization failed for all items)
        return serialized



    def _deserialize_safety_settings(self, serialized_dict: Optional[Dict[str, str]]) -> Optional[Dict[google_types.HarmCategory, google_types.HarmBlockThreshold]]:
        """Converts safety setting strings from JSON back to enums. Handles None and empty dict."""
        if serialized_dict is None:
            return None
        if not serialized_dict: # Handle empty dict explicitly
             return {}
        if not SDK_TYPES_AVAILABLE:
             log_warning("SDK types not available during safety setting deserialization.")
             return {} # Return empty dict if SDK missing but input wasn't None

        settings_dict = {}
        for cat_str, thresh_str in serialized_dict.items():
            category_enum = STR_TO_HARM_CATEGORY.get(cat_str)
            threshold_enum = STR_TO_HARM_THRESHOLD.get(thresh_str)
            if category_enum and threshold_enum:
                settings_dict[category_enum] = threshold_enum
            else:
                log_warning(f"Could not deserialize safety setting: {cat_str}, {thresh_str}")
        # Return the populated dict (could be empty if deserialization failed for all items)
        return settings_dict





    def _load_settings(self) -> Dict[str, Any]:
        """Loads settings from the JSON file."""
        # Define default values for all settings
        defaults = {
            "theme": constants.DEFAULT_THEME,
            "logging_enabled": constants.DEFAULT_LOGGING_ENABLED,
            "auto_save_enabled": constants.DEFAULT_AUTO_SAVE_ENABLED,
            "retry_count": constants.DEFAULT_RETRY_COUNT,
            "retry_delay": constants.DEFAULT_RETRY_DELAY,
            "request_delay": constants.DEFAULT_REQUEST_DELAY,
            "last_used_api_key_name": None,
            "last_mode": "Single",
            # 'filename_pattern' key will store the *active* pattern string after validation
            "filename_pattern": constants.DEFAULT_FILENAME_PATTERN,
            # Store the dictionary of saved patterns: {"Name": "{pattern_string}"}
            constants.SAVED_FILENAME_PATTERNS_KEY: {
                constants.DEFAULT_FILENAME_PATTERN_NAME: constants.DEFAULT_FILENAME_PATTERN
            },
            # Store the *name* of the active pattern
            constants.ACTIVE_FILENAME_PATTERN_NAME_KEY: constants.DEFAULT_FILENAME_PATTERN_NAME,
            "default_temperature": constants.DEFAULT_TEMPERATURE,
            "default_top_p": constants.DEFAULT_TOP_P,
            "default_max_tokens": constants.DEFAULT_MAX_OUTPUT_TOKENS,
            "last_image_dir": str(Path.home()),
            # Store safety settings serialized (will be deserialized later)
            "single_mode_safety_settings": None,
            "multi_mode_safety_settings": None,
            "last_thumbnail_browse_dir": str(Path.home()),
            constants.SAVE_TEXT_FILE_ENABLED: constants.DEFAULT_SAVE_TEXT_FILE_ENABLED,
            constants.EMBED_METADATA_ENABLED: constants.DEFAULT_EMBED_METADATA_ENABLED,
            # Add other settings keys with defaults here if needed
            
        }

        # Load raw settings from file, using an empty dict if file missing/corrupt
        loaded_settings_raw = load_json_file(self.filepath, default={})
        if not isinstance(loaded_settings_raw, dict):
            log_error("Settings file is corrupt or not a dictionary. Using defaults.")
            loaded_settings_raw = {} # Ensure it's a dict for update

        # Merge defaults with loaded settings (loaded values overwrite defaults)
        merged_settings = defaults.copy()
        merged_settings.update(loaded_settings_raw)

        # --- Validate and Sanitize Filename Pattern Settings ---
        saved_patterns = merged_settings.get(constants.SAVED_FILENAME_PATTERNS_KEY, {})

        # Ensure saved_patterns is a dictionary
        if not isinstance(saved_patterns, dict):
            log_warning(f"'{constants.SAVED_FILENAME_PATTERNS_KEY}' key is not a dictionary in settings file, resetting to default.")
            saved_patterns = {constants.DEFAULT_FILENAME_PATTERN_NAME: constants.DEFAULT_FILENAME_PATTERN}
            merged_settings[constants.SAVED_FILENAME_PATTERNS_KEY] = saved_patterns # Fix in merged dict

        # Ensure the default pattern exists within the saved patterns
        if constants.DEFAULT_FILENAME_PATTERN_NAME not in saved_patterns:
            log_warning(f"Default pattern '{constants.DEFAULT_FILENAME_PATTERN_NAME}' missing from saved patterns. Adding it.")
            saved_patterns[constants.DEFAULT_FILENAME_PATTERN_NAME] = constants.DEFAULT_FILENAME_PATTERN
            # Update the merged_settings directly as saved_patterns is potentially a new dict now
            merged_settings[constants.SAVED_FILENAME_PATTERNS_KEY] = saved_patterns

        # Ensure the active pattern name is valid and exists in saved_patterns
        active_pattern_name = merged_settings.get(constants.ACTIVE_FILENAME_PATTERN_NAME_KEY)
        if active_pattern_name not in saved_patterns:
            original_invalid_name = active_pattern_name # Keep for logging
            active_pattern_name = constants.DEFAULT_FILENAME_PATTERN_NAME # Reset to default name
            log_warning(f"Active pattern name '{original_invalid_name}' not found in saved patterns. Resetting to default ('{active_pattern_name}').")
            merged_settings[constants.ACTIVE_FILENAME_PATTERN_NAME_KEY] = active_pattern_name # Fix in merged dict

        # Crucially, ensure the 'filename_pattern' key holds the actual pattern string
        # corresponding to the (potentially corrected) active_pattern_name
        merged_settings["filename_pattern"] = saved_patterns[active_pattern_name]
        # --- End Filename Pattern Validation ---

        # --- Deserialize Safety Settings ---
        # Do this *after* merging defaults and loaded values
        single_mode_safety_serialized = merged_settings.get("single_mode_safety_settings")
        multi_mode_safety_serialized = merged_settings.get("multi_mode_safety_settings")

        merged_settings["single_mode_safety_settings"] = self._deserialize_safety_settings(
            single_mode_safety_serialized
        ) if isinstance(single_mode_safety_serialized, dict) else None # Ensure it's a dict before passing

        merged_settings["multi_mode_safety_settings"] = self._deserialize_safety_settings(
            multi_mode_safety_serialized
        ) if isinstance(multi_mode_safety_serialized, dict) else None # Ensure it's a dict before passing
        # --- End Safety Settings Deserialization ---

        log_info(f"Settings loaded successfully from {self.filepath}")
        return merged_settings



    def get_saved_filename_patterns(self) -> Dict[str, str]:
        """Returns a copy of the saved filename patterns dictionary."""
        # Ensure the key exists and is a dict, return default if not
        patterns = self.settings.get(SAVED_FILENAME_PATTERNS_KEY, {})
        if not isinstance(patterns, dict):
            log_warning(f"'{SAVED_FILENAME_PATTERNS_KEY}' is not a dictionary in settings, returning default.")
            return {DEFAULT_FILENAME_PATTERN_NAME: DEFAULT_FILENAME_PATTERN}.copy()
        return patterns.copy()

    def add_or_update_saved_filename_pattern(self, name: str, pattern: str) -> bool:
        """Adds or updates a pattern in the saved patterns list."""
        if not name or not pattern:
            log_error("Pattern name and pattern string cannot be empty.")
            return False
        # Optional: Prevent modifying the default pattern's name directly?
        # if name == DEFAULT_FILENAME_PATTERN_NAME and self.settings[SAVED_FILENAME_PATTERNS_KEY].get(name) != pattern:
        #    log_warning("Modifying the default pattern string. Consider creating a new pattern.")

        current_patterns = self.settings.get(SAVED_FILENAME_PATTERNS_KEY, {})
        if not isinstance(current_patterns, dict): # Ensure it's a dict
             current_patterns = {DEFAULT_FILENAME_PATTERN_NAME: DEFAULT_FILENAME_PATTERN}

        current_patterns[name] = pattern
        self.settings[SAVED_FILENAME_PATTERNS_KEY] = current_patterns # Update the main settings dict
        log_info(f"Saved filename pattern '{name}' updated.")
        return self._save_settings()

    def remove_saved_filename_pattern(self, name: str) -> bool:
        """Removes a pattern from the saved list."""
        if name == DEFAULT_FILENAME_PATTERN_NAME:
            log_error("Cannot remove the default filename pattern.")
            return False

        current_patterns = self.settings.get(SAVED_FILENAME_PATTERNS_KEY, {})
        if not isinstance(current_patterns, dict):
             log_error("Cannot remove pattern, saved patterns structure is invalid.")
             return False

        if name in current_patterns:
            del current_patterns[name]
            # If the removed pattern was the active one, reset active to default
            if self.settings.get(ACTIVE_FILENAME_PATTERN_NAME_KEY) == name:
                log_warning(f"Removed pattern '{name}' was active. Resetting active pattern to default.")
                self.set_setting(ACTIVE_FILENAME_PATTERN_NAME_KEY, DEFAULT_FILENAME_PATTERN_NAME, save=False) # Update active name and pattern string, don't save yet

            self.settings[SAVED_FILENAME_PATTERNS_KEY] = current_patterns
            log_info(f"Saved filename pattern '{name}' removed.")
            return self._save_settings() # Now save all changes
        else:
            log_warning(f"Attempted to remove non-existent saved pattern '{name}'.")
            return False

    def _save_settings(self) -> bool:
        """Saves the current settings to the JSON file."""
        # Create a copy to serialize safety settings before saving
        settings_to_save = self.settings.copy()

        # Serialize safety settings
        settings_to_save["single_mode_safety_settings"] = self._serialize_safety_settings(
            settings_to_save.get("single_mode_safety_settings")
        )
        settings_to_save["multi_mode_safety_settings"] = self._serialize_safety_settings(
            settings_to_save.get("multi_mode_safety_settings")
        )

        log_info(f"Saving settings to {self.filepath}")
        return save_json_file(self.filepath, settings_to_save)

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Retrieves a setting value by key, deserializing safety settings if needed."""
        raw_value = self.settings.get(key, default)

        # Check if the key represents safety settings that need deserialization
        is_single_safety = (key == "single_mode_safety_settings")
        is_multi_safety = (key == "multi_mode_safety_settings")
        is_instance_safety = key.startswith("instance_") and key.endswith("_safety_settings")

        if (is_single_safety or is_multi_safety or is_instance_safety) and isinstance(raw_value, dict):
            # Attempt to deserialize only if it looks like a serialized dict
            deserialized_value = self._deserialize_safety_settings(raw_value)
            if deserialized_value is not None:
                # log_debug(f"Deserialized safety setting for key '{key}'") # Optional debug log
                return deserialized_value
            else:
                # Deserialization failed or resulted in None, return original raw_value or default
                log_warning(f"Failed to deserialize safety setting for key '{key}', returning raw value or default.")
                return raw_value
        elif (is_single_safety or is_multi_safety or is_instance_safety) and raw_value is not None and not isinstance(raw_value, dict):
             # Log if the value exists but isn't the expected dictionary format
             log_warning(f"Safety setting key '{key}' exists but is not a dictionary (Type: {type(raw_value)}). Returning raw value.")
             return raw_value

        # For all other keys, or if safety setting is None/not a dict, return the raw value
        return raw_value

    def set_setting(self, key: str, value: Any, save: bool = True):
        """Sets a setting value by key."""
        # Handle specific actions when certain settings change
        if key == "logging_enabled" and self.settings.get(key) != value:
            set_logging_enabled(bool(value))

        # Store the value (it should already be in the correct type, e.g., deserialized dict for safety)
        self.settings[key] = value
        if key == ACTIVE_FILENAME_PATTERN_NAME_KEY:
            saved_patterns = self.get_saved_filename_patterns()
            if value in saved_patterns:
                self.settings[key] = value # Store the name
                # Update the actual pattern string in the 'filename_pattern' key
                self.settings["filename_pattern"] = saved_patterns[value]
                log_info(f"Active filename pattern set to: '{value}'")
            else:
                log_error(f"Cannot set active pattern: Name '{value}' not found in saved patterns.")
                if save: self._save_settings() # Save other potential changes even if this one fails
                return # Don't save if the name is invalid
        else:
            # Store other settings normally
            self.settings[key] = value
        if save:
            self._save_settings()

    def get_all_settings(self) -> Dict[str, Any]:
        """Returns a copy of all current settings."""
        # Return a copy to prevent external modification
        return self.settings.copy()