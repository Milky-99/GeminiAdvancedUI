# --- START OF FILE core/filename_generator.py ---

import re
import time
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional

from utils.logger import log_debug, log_error, log_warning, log_info
from utils import constants # For default pattern maybe

# Characters invalid in filenames across common OS (Windows, Linux, macOS)
INVALID_FILENAME_CHARS = r'[<>:"/\\|?*\x00-\x1f]' # Control characters added
MAX_FILENAME_LENGTH = 200 # A reasonable limit to prevent issues

# Placeholder Regex (simple version, can be expanded)
# Matches {placeholder_name} or {placeholder_name:argument}
PLACEHOLDER_REGEX = re.compile(r"\{([a-zA-Z0-9_]+)(?::([^}]+))?\}")

class FilenameGeneratorService:
    """Generates unique filenames based on user-defined patterns and context data."""

    def __init__(self, settings_service):
        # Settings service might be needed later for global defaults, but pattern comes from caller for now
        self.settings_service = settings_service

    def _sanitize_part(self, part: str) -> str:
        """Removes invalid characters from a filename part."""
        # Replace invalid characters with an underscore
        sanitized = re.sub(INVALID_FILENAME_CHARS, '_', str(part))
        # Replace multiple consecutive underscores with a single one
        sanitized = re.sub(r'_+', '_', sanitized)
        # Remove leading/trailing underscores/spaces
        sanitized = sanitized.strip('_ ')
        return sanitized




    def _get_placeholder_value(self, placeholder: str, argument: Optional[str], data: Dict[str, Any]) -> str:
        """Retrieves the value for a given placeholder."""
        log_debug(f"Resolving placeholder: '{placeholder}', Argument: '{argument}'")
        placeholder_lower = placeholder.lower()
        timestamp = data.get('timestamp', time.time())
        dt = time.localtime(timestamp)

        if placeholder_lower == 'date':
            return time.strftime("%Y%m%d", dt)
        elif placeholder_lower == 'time':
            return time.strftime("%H%M%S", dt)
        elif placeholder_lower == 'datetime':
            return time.strftime("%Y%m%d_%H%M%S", dt)
        elif placeholder_lower == 'model':
            return data.get('model_name', 'unknown_model')
        elif placeholder_lower == 'key_name':
            return data.get('api_key_name', 'unknown_key')
        elif placeholder_lower == 'instance_id':
            return str(data.get('instance_id', 'NA'))
        elif placeholder_lower == 'prompt_hash':
            prompt = data.get('resolved_prompt', '')
            return hashlib.md5(prompt.encode()).hexdigest()[:8]
        elif placeholder_lower == 'unresolved_prompt_hash':
            prompt = data.get('unresolved_prompt', '')
            return hashlib.md5(prompt.encode()).hexdigest()[:8]
        elif placeholder_lower == 'prompt_start':
            prompt = data.get('resolved_prompt', '')
            length = 50
            if argument:
                try: length = int(argument)
                except ValueError: pass
            return prompt[:length]
        elif placeholder_lower == 'prompt_end':
            prompt = data.get('resolved_prompt', '')
            length = 50
            if argument:
                try: length = int(argument)
                except ValueError: pass
            return prompt[-length:]
            
        # --- NEW GENERIC WILDCARD PLACEHOLDER ---
        elif placeholder_lower == 'wc':
            wildcard_name = argument
            if not wildcard_name:
                log_warning("Missing wildcard name for {wc:name} placeholder.")
                return "" # Return empty string

            resolved_map = data.get("resolved_wildcards_by_name", {})
            resolved_values = resolved_map.get(wildcard_name)

            if isinstance(resolved_values, list) and resolved_values:
                # Join multiple values with an underscore
                combined_value = "_".join(map(str, resolved_values))
                log_debug(f"Resolved {{wc:{wildcard_name}}} to '{combined_value}'")
                return combined_value
            else:
                # If the wildcard wasn't used, return an empty string.
                # This is key for the conditional block logic.
                log_debug(f"No resolved value for {{wc:{wildcard_name}}}, returning empty string.")
                return ""
        
        # Fallback for unknown placeholders
        log_warning(f"Unknown filename placeholder: {{{placeholder}}}")
        return f"{{{placeholder}}}"





    def generate_filename(self,
                          pattern: str,
                          data: Dict[str, Any],
                          output_dir: Path,
                          extension: str) -> Path:
        """
        Generates a unique filepath based on the pattern and data, supporting conditional blocks.
        """
        log_info("--- FilenameGeneratorService.generate_filename CALLED ---")
        log_debug(f"Pattern: '{pattern}', Extension: '{extension}'")
        log_debug(f"[FilenameGen INPUT DATA] received data['resolved_wildcards_by_name']: {data.get('resolved_wildcards_by_name')}")

        # 1. Resolve all {placeholder} tags first
        base_filename = pattern
        for match in PLACEHOLDER_REGEX.finditer(pattern):
            placeholder = match.group(1)
            argument = match.group(2)
            placeholder_tag = match.group(0)
            
            value = self._get_placeholder_value(placeholder, argument, data)
            # IMPORTANT: Here we just replace. We sanitize AFTER conditional blocks are processed.
            base_filename = base_filename.replace(placeholder_tag, value, 1)
        
        log_debug(f"After placeholder replacement: '{base_filename}'")

        # 2. Resolve conditional [[...]] blocks
        conditional_regex = re.compile(r"\[\[(.*?)\]\]")
        # Loop to handle nested conditional blocks, though not common
        while conditional_regex.search(base_filename):
            base_filename = conditional_regex.sub(
                # Use a lambda to check if the content of the block is empty
                lambda m: m.group(1) if m.group(1).strip() else "",
                base_filename
            )
        
        log_debug(f"After conditional block processing: '{base_filename}'")

        # 3. Sanitize the entire final string
        sanitized_filename = self._sanitize_part(base_filename)
        log_debug(f"Final sanitized base filename: '{sanitized_filename}'")

        # 4. Truncate if too long
        if len(sanitized_filename) > MAX_FILENAME_LENGTH:
            log_warning(f"Base filename exceeds max length ({MAX_FILENAME_LENGTH}), truncating.")
            sanitized_filename = sanitized_filename[:MAX_FILENAME_LENGTH].strip('_ ')
        
        if not sanitized_filename:
            log_warning("Generated base filename was empty, using fallback.")
            sanitized_filename = f"generated_{time.strftime('%Y%m%d_%H%M%S')}"

        # 5. Ensure output directory exists
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log_error(f"Failed to create output directory {output_dir}: {e}")

        # 6. Handle filename collisions with a sequence number
        sequence_num = 0
        final_path = output_dir / f"{sanitized_filename}{extension}"
        log_debug(f"Initial path generated: '{final_path}'")

        while final_path.exists():
            sequence_num += 1
            sequence_str = str(sequence_num).zfill(3)
            max_base_len = MAX_FILENAME_LENGTH - len(sequence_str) - 1
            truncated_base = sanitized_filename[:max_base_len].strip('_ ')
            final_path = output_dir / f"{truncated_base}_{sequence_str}{extension}"
            if sequence_num > 999:
                log_error(f"Could not find unique filename for base '{sanitized_filename}' after 999 attempts.")
                ms_timestamp = str(int(time.time() * 1000))
                final_path = output_dir / f"{sanitized_filename}_{ms_timestamp}{extension}"
                log_warning(f"Using fallback timestamp filename: '{final_path}'")
                break
                
        log_info(f"Generated unique filepath: '{final_path}'")
        return final_path