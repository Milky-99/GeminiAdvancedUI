# -*- coding: utf-8 -*-
import os
import re
import random
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set, Any
import json
from utils.constants import WILDCARDS_DIR, WILDCARD_REGEX
from utils.logger import log_error, log_warning, log_debug, log_info

class WildcardResolver:
    """Handles resolving wildcards like [wildcard] and {wildcard} in prompts."""

    MAX_RECURSION_DEPTH = 10 # Prevent infinite loops

    def __init__(self, wildcards_base_dir: Path = WILDCARDS_DIR):
        self.base_dir = wildcards_base_dir
        self._wildcard_cache: Dict[str, List[str]] = {} # Cache loaded file contents
        self._numbered_wildcards: Dict[int, Dict[str, str]] = {} # Cache for [1:wildcard] style
        self._last_resolved_map: Dict[str, str] = {} # Stores {wildcard} -> resolved value for last run
        self._last_resolved_map = {}

    def _load_wildcard_file(self, wildcard_name: str) -> List[Dict[str, Any]]:
        """Loads wildcard data from a JSON file, caching the result."""
        if wildcard_name in self._wildcard_cache:
            return self._wildcard_cache[wildcard_name]

        # Look for .json file now
        file_path = self.base_dir / f"{wildcard_name}.json"
        if not file_path.is_file():
            log_warning(f"Wildcard JSON file not found: {file_path}")
            self._wildcard_cache[wildcard_name] = [] # Cache empty list if not found
            return []

        try:
            log_debug(f"Loading wildcard JSON file: {file_path}")
            with file_path.open('r', encoding='utf-8') as f:
                # Check if file is empty
                if file_path.stat().st_size == 0:
                    log_warning(f"Wildcard JSON file is empty: {file_path}")
                    data = []
                else:
                    data = json.load(f)

            if not isinstance(data, list):
                log_error(f"Wildcard JSON file root is not a list: {file_path}")
                data = [] # Treat as empty on error

            # Validate entries and add default scores if missing
            valid_entries = []
            for i, entry in enumerate(data):
                if isinstance(entry, dict) and "value" in entry:
                    # Ensure score keys exist, default to 0
                    entry.setdefault("success", 0)
                    entry.setdefault("blocked", 0)
                    # Calculate average on load
                    entry["average"] = entry["success"] - entry["blocked"]
                    valid_entries.append(entry)
                else:
                    log_warning(f"Invalid entry structure at index {i} in {file_path}. Skipping: {entry}")

            if not valid_entries and len(data) > 0:
                 log_warning(f"Wildcard file contained data, but no valid entries found: {file_path}")

            self._wildcard_cache[wildcard_name] = valid_entries
            log_debug(f"Loaded {len(valid_entries)} valid entries from wildcard file: {file_path}")
            return valid_entries

        except json.JSONDecodeError as e:
            log_error(f"Error decoding JSON wildcard file {file_path}: {e}")
            self._wildcard_cache[wildcard_name] = []
            return []
        except OSError as e:
            log_error(f"Error reading wildcard file {file_path}: {e}")
            self._wildcard_cache[wildcard_name] = []
            return []
        except Exception as e: # Catch other potential errors
            log_error(f"Unexpected error loading wildcard file {file_path}: {e}", exc_info=True)
            self._wildcard_cache[wildcard_name] = []
            return []


    def resolve_specific_wildcard(self, prompt_text: str, index: int) -> Optional[str]:
        """
        Finds the Nth wildcard in the prompt and returns its resolved value.
        This method does NOT modify the main resolved values map used for {wc_resolved:name}.

        Args:
            prompt_text: The original, unresolved prompt text.
            index: The 1-based index of the wildcard to resolve (e.g., 2 for the second).

        Returns:
            The resolved string for the specified wildcard, or None if not found or error.
        """
        if index <= 0:
            log_error("Wildcard index must be 1 or greater.")
            return None
        if not prompt_text:
            return None

        matches = list(re.finditer(WILDCARD_REGEX, prompt_text))

        if index > len(matches):
            log_warning(f"Requested wildcard index {index}, but only found {len(matches)} wildcards in prompt.")
            return None # Not enough wildcards found

        target_match = matches[index - 1] # Get the match at the 0-based index

        # Backup and clear numbered wildcard cache specific to this call
        # No need to backup/restore self._resolved_values_by_name here anymore
        temp_numbered_cache_backup = self._numbered_wildcards.copy()
        self._numbered_wildcards.clear()

        resolved_value = None
        try:
            log_debug(f"Resolving specific wildcard (Index: {index}, Match: '{target_match.group(0)}')")
            # Call _resolve_single_wildcard WITHOUT passing a target_map.
            # This prevents it from modifying the instance's map.
            resolved_value = self._resolve_single_wildcard(
                target_match,
                current_depth=0,
                visited_in_chain=set(),
                target_map=None  # <-- Explicitly None
            )
            log_debug(f"Resolved specific wildcard value: '{resolved_value}'")
        except Exception as e:
            log_error(f"Error resolving specific wildcard at index {index}: {e}", exc_info=True)
            resolved_value = None
        finally:
            # Restore only the numbered cache
            self._numbered_wildcards = temp_numbered_cache_backup

        # Return the resolved value, or the original match text if resolution failed but match existed
        return resolved_value if resolved_value is not None else target_match.group(0)

    def _resolve_single_wildcard(
        self,
        wildcard_match: re.Match,
        current_depth: int,
        visited_in_chain: Optional[Set[str]] = None,
        target_map: Optional[Dict[str, List[str]]] = None
    ) -> str:
        """
        Resolves a single matched wildcard, handling recursion and OR logic.
        Populates the target_map if provided.
        """
        log_debug(f"--- _resolve_single_wildcard called (Depth: {current_depth}) ---")
        if current_depth > self.MAX_RECURSION_DEPTH:
            log_warning(f"Max recursion depth ({self.MAX_RECURSION_DEPTH}) reached for wildcard. Returning original match.")
            return wildcard_match.group(0)

        visited_in_chain = visited_in_chain or set()
        original_match_text = wildcard_match.group(0)
        log_debug(f"Original match text: '{original_match_text}'")

        curly_wildcard_name = wildcard_match.group(1)
        numbered_prefix = wildcard_match.group(2)
        bracket_wildcard_name = wildcard_match.group(3)
        count_suffix = wildcard_match.group(4)

        resolved_value = ""
        wildcard_base_name = curly_wildcard_name or bracket_wildcard_name
        log_debug(f"Initial wildcard_base_name: '{wildcard_base_name}'")

        # --- NEW "OR" LOGIC ---
        # Check for the OR operator '|' in bracket wildcards.
        # This must be done BEFORE checking for recursion or loading files.
        if bracket_wildcard_name and "|" in bracket_wildcard_name:
            possible_options = [name.strip() for name in bracket_wildcard_name.split('|') if name.strip()]
            if possible_options:
                chosen_wildcard_name = random.choice(possible_options)
                log_debug(f"OR Wildcard detected in '{bracket_wildcard_name}'. Randomly chose '{chosen_wildcard_name}'.")
                wildcard_base_name = chosen_wildcard_name  # This name will now be used for file loading etc.
            else:
                log_warning(f"Wildcard with OR operator was empty or invalid: '[{bracket_wildcard_name}]'")
                # Fallback to keep the original text, which will likely fail to load a file and return the original text
                wildcard_base_name = bracket_wildcard_name
        # --- END NEW "OR" LOGIC ---

        if not wildcard_base_name:
            log_error(f"Could not determine wildcard base name from match: {original_match_text}")
            return original_match_text

        if curly_wildcard_name:
            log_debug(f"Processing curly wildcard: {{{wildcard_base_name}}}")
            lines = self._load_wildcard_file(wildcard_base_name)
            if not lines:
                resolved_value = original_match_text
            else:
                chosen_entry = random.choice(lines)
                resolved_value = chosen_entry.get("value", original_match_text)
                if target_map is not None:
                    if wildcard_base_name not in target_map:
                        target_map[wildcard_base_name] = []
                    target_map[wildcard_base_name].append(resolved_value)
                    log_debug(f"Added '{resolved_value}' to target_map for key '{wildcard_base_name}'")

        elif bracket_wildcard_name:
            log_debug(f"Processing bracket wildcard. Final base name: '{wildcard_base_name}'")
            number_id = None
            if numbered_prefix:
                try:
                    number_id = int(numbered_prefix[:-1])
                except ValueError:
                    log_warning(f"Invalid number prefix in wildcard: {original_match_text}. Treating as non-numbered.")

            count = 1
            if count_suffix:
                try:
                    count = max(1, int(count_suffix))
                except ValueError:
                    log_warning(f"Invalid count suffix in wildcard: {original_match_text}. Using count=1.")

            resolved_parts = []
            for i in range(count):
                current_part_value = ""
                if number_id is not None and number_id in self._numbered_wildcards and wildcard_base_name in self._numbered_wildcards[number_id]:
                    current_part_value = self._numbered_wildcards[number_id][wildcard_base_name]
                    log_debug(f"Using cached value for numbered wildcard [{number_id}:{wildcard_base_name}] -> '{current_part_value}'")
                    if target_map is not None and i == 0:
                        if wildcard_base_name not in target_map:
                            target_map[wildcard_base_name] = []
                        target_map[wildcard_base_name].append(current_part_value)
                        log_debug(f"Added cached value to target_map for key '{wildcard_base_name}'")
                else:
                    lines = self._load_wildcard_file(wildcard_base_name)
                    if lines:
                        chosen_entry = random.choice(lines)
                        chosen_line = chosen_entry.get("value", f"[{wildcard_base_name}]")
                        current_part_value = chosen_line

                        if number_id is not None:
                            if number_id not in self._numbered_wildcards:
                                self._numbered_wildcards[number_id] = {}
                            self._numbered_wildcards[number_id][wildcard_base_name] = chosen_line
                            log_debug(f"Stored value for numbered wildcard [{number_id}:{wildcard_base_name}] -> '{chosen_line}'")

                        if target_map is not None:
                            if wildcard_base_name not in target_map:
                                target_map[wildcard_base_name] = []
                            target_map[wildcard_base_name].append(chosen_line)
                            log_debug(f"Added resolved value '{chosen_line}' to target_map for key '{wildcard_base_name}'")
                    else:
                        current_part_value = f"[{wildcard_base_name}]"

                resolved_parts.append(current_part_value)

            resolved_value = " ".join(resolved_parts)

        else:
            log_debug("Match was not a curly or bracket wildcard. Returning original.")
            return original_match_text

        if re.search(WILDCARD_REGEX, resolved_value):
            recursion_key = wildcard_base_name
            if recursion_key in visited_in_chain:
                log_warning(f"Detected direct self-recursion for '{recursion_key}'. Stopping resolution for this part.")
                return resolved_value

            visited_in_chain.add(recursion_key)
            log_debug(f"Recursively resolving wildcards in: '{resolved_value}' (Depth: {current_depth + 1})")
            resolved_value = self._resolve_recursive(resolved_value, current_depth + 1, visited_in_chain, target_map)
            visited_in_chain.remove(recursion_key)

        log_debug(f"--- _resolve_single_wildcard finished. Returning: '{resolved_value}' ---")
        return resolved_value


    def _resolve_recursive(
        self,
        text: str,
        current_depth: int,
        visited_in_chain: Set[str],
        target_map: Optional[Dict[str, List[str]]] = None # <-- ADDED ARGUMENT
    ) -> str:
        """
        Internal recursive resolution function.
        Resolves all wildcards in the current 'text' string in one pass,
        then recurses if any changes were made, up to MAX_RECURSION_DEPTH.
        Passes the target_map down the chain.
        """
        if current_depth > self.MAX_RECURSION_DEPTH:
            log_warning(f"Max recursion depth ({self.MAX_RECURSION_DEPTH}) reached during wildcard resolution. Returning potentially unresolved text.")
            # Return the text as is without further resolution
            return text

        # Use a flag to check if any resolution happened in this pass
        changed_in_pass = False

        # Define the replacement function which calls _resolve_single_wildcard
        def replace_match(match_obj):
            # Pass visited_in_chain AND target_map to the single wildcard resolver
            resolved_part = self._resolve_single_wildcard(match_obj, current_depth, visited_in_chain, target_map)

            # Check if the match actually resulted in a change
            if resolved_part != match_obj.group(0):
                nonlocal changed_in_pass # Modify the flag in the outer scope
                changed_in_pass = True

            return resolved_part

        # Perform one full pass of substitution using re.sub
        resolved_text_this_pass = re.sub(WILDCARD_REGEX, replace_match, text)

        # If any wildcard was resolved in this pass and the string changed, recursively call again
        if changed_in_pass and resolved_text_this_pass != text:
             log_debug(f"Recursion needed for: '{resolved_text_this_pass[:100]}{'...' if len(resolved_text_this_pass) > 100 else ''}' (Depth: {current_depth + 1})")
             # Recurse on the new string, incrementing depth, passing the target_map
             return self._resolve_recursive(resolved_text_this_pass, current_depth + 1, visited_in_chain, target_map)
        else:
             # No changes were made in this pass, or max depth reached (handled at start)
             return resolved_text_this_pass


    def resolve(self, prompt_text: str) -> Tuple[str, str, Dict[str, List[str]]]:
        """
        Resolves all wildcards in the given prompt text.

        Returns:
            Tuple[str, str, Dict[str, List[str]]]:
            (resolved_prompt, original_prompt, resolved_values_by_name)
            resolved_values_by_name maps wildcard names to a list of chosen values.
        """
        if not prompt_text:
            return "", "", {}

        # Clear numbered wildcard cache (needed for [N:name] consistency within one resolve call)
        self._numbered_wildcards.clear()

        # Create a LOCAL dictionary to store resolved values for THIS call
        local_resolved_values_by_name: Dict[str, List[str]] = {}

        # Start the recursive resolution, passing the LOCAL map as the target
        resolved_prompt = self._resolve_recursive(prompt_text, 0, set(), local_resolved_values_by_name)

        log_debug(f"Original prompt: '{prompt_text}'")
        log_debug(f"Resolved prompt: '{resolved_prompt}'")
        log_debug(f"Resolved values by name map (local): {local_resolved_values_by_name}")

        # --- ADD THIS LOG ---
        log_info(f"[WildcardResolver.resolve END] Returning map: {local_resolved_values_by_name}")
        # --- END ADD ---

        # Return original, resolved, and the LOCAL map of resolved values
        return resolved_prompt, prompt_text, local_resolved_values_by_name.copy()


    def clear_cache(self):
        """Clears the file content cache."""
        self._wildcard_cache.clear()
        log_info("Wildcard file cache cleared.")
        
        
    def _save_wildcard_file(self, wildcard_name: str, data: List[Dict[str, Any]]) -> bool:
        """Saves the updated wildcard data back to its JSON file."""
        file_path = self.base_dir / f"{wildcard_name}.json"
        try:
            # Ensure parent directory exists
            self.base_dir.mkdir(parents=True, exist_ok=True)
            with file_path.open('w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False) # Use indent=2 for smaller files
            log_debug(f"Successfully saved updated wildcard file: {file_path.name}")
            return True
        except OSError as e:
            log_error(f"Error writing wildcard file {file_path}: {e}")
            return False
        except Exception as e:
            log_error(f"Unexpected error saving wildcard file {file_path}: {e}", exc_info=True)
            return False



    def update_scores(self, chosen_wildcards: Dict[str, str], outcome: str):
        """
        Updates the success/blocked scores for the chosen wildcard values and saves the files.

        Args:
            chosen_wildcards: A dict mapping the ORIGINAL wildcard text (e.g., '[colors]')
                              to the specific VALUE string chosen during resolution.
            outcome: Either "success" or "blocked".
        """
        if not chosen_wildcards:
            log_debug("No chosen wildcards provided for score update.")
            return

        log_info(f"Updating wildcard scores based on outcome: '{outcome}'")
        log_debug(f"Chosen wildcards map for score update: {chosen_wildcards}") # Log the input map
        updated_files: Set[str] = set() # Track which files were modified

        for original_wildcard_text, chosen_value in chosen_wildcards.items():
            # Extract wildcard_name from the original text (e.g., 'colors' from '[colors]' or '{colors}')
            match = re.match(WILDCARD_REGEX, original_wildcard_text) # Match against original text
            if not match:
                log_warning(f"Could not parse wildcard name from original text: {original_wildcard_text}")
                continue

            # Get the base name (group 1 for {} or group 3 for [])
            wildcard_name = match.group(1) or match.group(3)
            if not wildcard_name:
                log_warning(f"Could not determine wildcard name for: {original_wildcard_text}")
                continue

            log_debug(f"Attempting score update for wildcard '{wildcard_name}' (from '{original_wildcard_text}'), chosen value: '{chosen_value}'")

            # Load the current data for this wildcard (use cache if available)
            wildcard_data = self._load_wildcard_file(wildcard_name)
            if not wildcard_data:
                log_warning(f"No data found for wildcard '{wildcard_name}', cannot update score for value '{chosen_value}'.")
                continue

            # Find the entry matching the chosen value
            entry_updated = False
            for entry in wildcard_data:
                # Ensure comparison handles potential type differences if necessary, though values should be strings
                if str(entry.get("value", object())) == str(chosen_value): # Compare chosen value string
                    if outcome == "success":
                        entry["success"] = entry.get("success", 0) + 1
                        log_debug(f"  Incremented success score for '{chosen_value}' in '{wildcard_name}'. New score: {entry['success']}")
                    elif outcome == "blocked":
                        entry["blocked"] = entry.get("blocked", 0) + 1
                        log_debug(f"  Incremented blocked score for '{chosen_value}' in '{wildcard_name}'. New score: {entry['blocked']}")
                    else:
                        log_warning(f"Unknown outcome '{outcome}' received for score update.")
                        continue # Don't update scores for unknown outcome

                    # Recalculate average
                    entry["average"] = entry.get("success", 0) - entry.get("blocked", 0)
                    entry_updated = True
                    updated_files.add(wildcard_name) # Mark file for saving
                    break # Found and updated the entry

            if not entry_updated:
                log_warning(f"Could not find entry with value '{chosen_value}' in wildcard file '{wildcard_name}.json' to update score.")

        # Save all modified files
        if not updated_files:
            log_debug("No wildcard files needed saving after score update attempt.")
            return

        log_info(f"Saving updated wildcard files: {', '.join(updated_files)}")
        save_success = True
        for name in updated_files:
            if name in self._wildcard_cache: # Ensure data is cached
                 # Pass the cached (modified) data to the save function
                if not self._save_wildcard_file(name, self._wildcard_cache[name]):
                    save_success = False # Track if any save failed
            else:
                 log_error(f"Cannot save '{name}.json': Data not found in cache after update.")
                 save_success = False

        if save_success:
            log_info("Wildcard score updates saved successfully.")
        else:
            log_error("One or more wildcard files failed to save after score updates.")
          
            
    def clear_specific_cache(self, wildcard_name: str):
        """Clears the cache for a specific wildcard file."""
        if wildcard_name in self._wildcard_cache:
            del self._wildcard_cache[wildcard_name]
            log_info(f"Cache cleared for specific wildcard: {wildcard_name}")
        else:
            log_debug(f"Attempted to clear cache for non-cached wildcard: {wildcard_name}")