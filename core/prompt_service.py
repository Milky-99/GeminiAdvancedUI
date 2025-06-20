# -*- coding: utf-8 -*-
import secrets
import base64
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import traceback
from io import BytesIO
from PyQt6.QtCore import pyqtSignal, QObject
from utils.constants import PROMPTS_FILE, MAX_PROMPT_SLOTS, PROMPTS_ASSETS_DIR
from utils.helpers import load_json_file, save_json_file
from utils.logger import log_info, log_warning, log_error, log_debug
from PIL import Image
from core.image_processor import ImageProcessor
from utils.logger import log_info, log_error, log_warning, log_debug

class PromptService(QObject):
    prompts_updated = pyqtSignal()
    """Manages storage and retrieval of user prompts, including thumbnail paths."""

    def __init__(self, filepath: Path = PROMPTS_FILE, max_slots: int = MAX_PROMPT_SLOTS):
        super().__init__()
        self.filepath = filepath
        self.max_slots = max_slots
        # Structure: {"slot_1": {"name": "...", "text": "...", "thumbnail_path": "..."}, ...}
        self._prompts: Dict[str, Dict[str, str]] = self._load_prompts()

    def _load_prompts(self) -> Dict[str, Dict[str, str]]:
        """Loads prompts from the JSON file."""
        data = load_json_file(self.filepath, default={})
        if isinstance(data, dict):
            valid_data = {}
            for slot, content in data.items():
                if (isinstance(content, dict) and
                        "name" in content and
                        "text" in content):
                    # Ensure thumbnail_path exists, default to None if missing
                    content.setdefault("thumbnail_path", None)
                    valid_data[slot] = content
                else:
                    log_warning(f"Invalid prompt structure found for slot '{slot}' in {self.filepath}. Skipping.")
            log_info(f"{len(valid_data)} prompts loaded from {self.filepath}")
            return valid_data
        log_error("Prompts file is corrupt or not a dictionary. Returning empty.")
        return {}

    # --- RENAMED FROM _save_prompts ---
    def save_all_prompts(self) -> bool:
        """Saves the current state of prompts to the JSON file."""
        log_debug(f"Saving {len(self._prompts)} prompts to {self.filepath}")
        success = save_json_file(self.filepath, self._prompts)
        if success:
            log_info("Prompt file saved successfully. Emitting prompts_updated signal.")
            self.prompts_updated.emit()
        return success
    # --- END RENAMED METHOD ---


    def _get_next_available_slot(self) -> Optional[str]:
        """Finds the lowest numbered available slot."""
        for i in range(1, self.max_slots + 1):
            slot_key = f"slot_{i}"
            if slot_key not in self._prompts:
                return slot_key
        return None

    def _create_and_save_thumbnail_file(self, slot_key: str, image_bytes: bytes) -> Optional[str]:
        """
        Creates a thumbnail from image bytes, saves it to the assets directory
        with the filename `slot_key.png`, and returns the relative filename.
        Returns None on failure.
        """
        if not image_bytes:
            log_warning(f"No image bytes provided to create thumbnail for slot {slot_key}.")
            return None

        thumbnail_filename = f"{slot_key}.png"
        thumbnail_full_path = PROMPTS_ASSETS_DIR / thumbnail_filename
        log_info(f"Attempting to create and save thumbnail {thumbnail_filename} for slot {slot_key}.")

        try:
            # Ensure assets directory exists
            PROMPTS_ASSETS_DIR.mkdir(parents=True, exist_ok=True)

            # Generate thumbnail (use desired larger size)
            thumb_bytes = ImageProcessor.create_thumbnail_bytes(image_bytes, size=(256, 256))

            if thumb_bytes:
                thumbnail_full_path.write_bytes(thumb_bytes)
                log_info(f"Successfully saved new thumbnail file: {thumbnail_full_path}")
                return thumbnail_filename # Return the relative filename
            else:
                log_error(f"Failed to create thumbnail bytes for slot {slot_key}.")
                return None

        except Exception as e:
            log_error(f"Error creating/saving thumbnail file for slot {slot_key}: {e}", exc_info=True)
            return None

    def _delete_thumbnail_file(self, relative_thumb_filename: Optional[str]):
        """Deletes a thumbnail file from the assets directory if it exists."""
        if relative_thumb_filename:
            thumb_file_path = PROMPTS_ASSETS_DIR / relative_thumb_filename
            if thumb_file_path.is_file():
                try:
                    thumb_file_path.unlink()
                    log_info(f"Deleted thumbnail file: {thumb_file_path}")
                except OSError as e:
                    log_error(f"Error deleting thumbnail file {thumb_file_path}: {e}")
            else:
                log_debug(f"Thumbnail file path found in data ('{relative_thumb_filename}'), but file doesn't exist at {thumb_file_path}, skipping deletion.")

    def add_prompt_to_memory(self, name: str, text: str) -> Optional[str]:
        """Adds a new prompt to the next available slot in memory. Returns slot_key."""
        if not name:
            log_error("Prompt name cannot be empty.")
            return None

        slot_key = self._get_next_available_slot()
        if slot_key is None:
            log_error(f"Maximum prompt slots ({self.max_slots}) reached.")
            return None

        # Initialize with thumbnail_path as None
        self._prompts[slot_key] = {"name": name, "text": text, "thumbnail_path": None}
        log_info(f"Prompt '{name}' added to {slot_key} (in memory).")
        return slot_key

    def update_prompt_data_in_memory(self, slot_key: str, name: str, text: str, thumbnail_path: Optional[str]) -> bool:
        """Updates an existing prompt's data in memory. Does NOT save immediately."""
        if slot_key not in self._prompts:
            log_error(f"Attempted to update non-existent prompt slot '{slot_key}'.")
            return False
        if not name: # Allow empty text, but not empty name
            log_error("Prompt name cannot be empty for update.")
            return False

        self._prompts[slot_key] = {
            "name": name,
            "text": text,
            "thumbnail_path": thumbnail_path
        }
        log_debug(f"Prompt data for {slot_key} updated in memory.")
        return True


    # --- NEW Public Methods ---

    def save_prompt_to_slot_with_thumbnail(self, slot_key: str, new_image_bytes: bytes) -> bool:
        """
        Saves the prompt text (unchanged) and updates the thumbnail for an existing slot.
        Deletes the old thumbnail file and creates a new one. Saves the main prompts file.
        Returns True on success, False on failure.
        """
        if slot_key not in self._prompts:
            log_error(f"Attempted to save to non-existent slot '{slot_key}'.")
            return False
        if not new_image_bytes:
            log_error(f"No image bytes provided to save thumbnail for slot '{slot_key}'.")
            return False

        current_prompt_data = self._prompts[slot_key]
        old_thumbnail_filename = current_prompt_data.get("thumbnail_path")
        prompt_name = current_prompt_data.get("name", "Unnamed") # Keep existing name
        prompt_text = current_prompt_data.get("text", "") # Keep existing text

        log_info(f"Saving new thumbnail for existing prompt '{prompt_name}' ({slot_key}).")

        # 1. Delete the old thumbnail file
        self._delete_thumbnail_file(old_thumbnail_filename)

        # 2. Create and save the new thumbnail file
        new_thumbnail_filename = self._create_and_save_thumbnail_file(slot_key, new_image_bytes)

        # 3. Update the prompt data in memory with the new thumbnail path
        # Use update_prompt_data_in_memory to modify the _prompts dictionary
        self.update_prompt_data_in_memory(slot_key, prompt_name, prompt_text, new_thumbnail_filename)

        # 4. Save the main prompts file
        return self.save_all_prompts() # Use the public save method


    def add_new_prompt_with_thumbnail(self, name: str, text: str, image_bytes: bytes) -> Optional[str]:
        """
        Adds a new prompt entry (memory + thumbnail file) and saves the main prompts file.
        Returns the new slot_key if successful, else None.
        """
        if not name:
            log_error("Prompt name cannot be empty for a new prompt.")
            return None
        if not image_bytes:
            log_error("No image bytes provided to add new prompt with thumbnail.")
            return None # Cannot add thumbnail-based prompt without image

        # 1. Add the base prompt to memory
        slot_key = self.add_prompt_to_memory(name, text)
        if not slot_key:
            return None # Failed to add base prompt to memory

        # 2. Create and save the new thumbnail file
        thumbnail_filename = self._create_and_save_thumbnail_file(slot_key, image_bytes)

        # 3. Update the prompt data in memory with the thumbnail path
        # Use update_prompt_data_in_memory to modify the _prompts dictionary
        # Pass the same name/text just in case
        self.update_prompt_data_in_memory(slot_key, name, text, thumbnail_filename)

        # 4. Save the main prompts file
        # Save even if thumbnail creation failed, so the new slot is recorded
        if not self.save_all_prompts(): # Use the public save method
            log_error(f"Failed to save prompt file after adding new prompt {slot_key}.")
            # Consider removing from memory if save failed? Risky.
            # If thumbnail creation failed but save succeeded, the slot exists but has thumbnail_path=None
            # If thumbnail creation succeeded but save failed, the thumbnail file exists but isn't linked in the JSON
            return None # Indicate overall failure

        return slot_key

    # --- End NEW Public Methods ---


    def get_prompt(self, slot_key: str) -> Optional[Dict[str, str]]:
        """Retrieves the full data dictionary of a prompt by slot key."""
        # Return a copy to prevent external modification of internal state
        return self._prompts.get(slot_key, {}).copy()


    def get_prompt_text(self, slot_key: str) -> Optional[str]:
        """Retrieves only the text of a prompt by slot key."""
        prompt_data = self._prompts.get(slot_key)
        return prompt_data.get("text") if prompt_data else None

    def remove_prompt(self, slot_key: str) -> bool:
        """Removes a prompt by slot key from memory. Does NOT save immediately."""
        if slot_key in self._prompts:
            removed_name = self._prompts[slot_key].get("name", "Unknown")
            del self._prompts[slot_key]
            log_info(f"Prompt '{removed_name}' ({slot_key}) removed from memory.")
            # Note: Deleting the thumbnail file on remove is handled in the PromptManagerDialog
            return True
        log_warning(f"Attempted to remove non-existent prompt slot '{slot_key}'.")
        return False

    def get_all_prompts_summary(self) -> List[Tuple[str, str]]:
        """Returns a list of (slot_key, prompt_name) for UI lists, sorted by slot number."""
        summaries = []
        for slot_key, data in self._prompts.items():
            summaries.append((slot_key, data.get("name", "Unnamed Prompt")))

        summaries.sort(key=lambda item: int(item[0].split('_')[-1]) if item[0].startswith("slot_") else float('inf'))
        return summaries

    def get_all_prompts_full(self) -> Dict[str, Dict[str, str]]:
        """Returns a copy of the full prompts dictionary."""
        return self._prompts.copy()

    def has_slot(self, slot_key: str) -> bool:
        """Checks if a prompt slot exists."""
        return slot_key in self._prompts