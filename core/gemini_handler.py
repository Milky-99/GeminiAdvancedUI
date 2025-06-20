# -*- coding: utf-8 -*-

import sys
import time
import mimetypes
import traceback
from io import BytesIO
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Union

from PIL import Image

# --- Google GenAI SDK Imports ---
try:
    from google import genai
    from google.genai import types
    from google.genai import errors as google_errors # Alias to avoid name clash
    from google.api_core import exceptions as google_api_core_exceptions
    SDK_AVAILABLE = True
except ImportError as e:
    print(f"FATAL ERROR: Failed to import google.genai: {e}. Please install it: pip install google-genai", file=sys.stderr)
    SDK_AVAILABLE = False
    # Define dummy classes/exceptions if SDK is not available to prevent import errors later
    class DummyGoogleErrors:
        GoogleAPIError = Exception
        InvalidArgumentError = ValueError
        ResourceExhaustedError = ConnectionError
        PermissionDeniedError = PermissionError
        DeadlineExceededError = TimeoutError # Add DeadlineExceededError
        # Add others as needed
    google_errors = DummyGoogleErrors()
    class DummyTypes:
        HarmCategory = None
        HarmBlockThreshold = None
        SafetySetting = None
        GenerateContentConfig = None
        FinishReason = None
        # Add others as needed
    types = DummyTypes()


from .wildcard_resolver import WildcardResolver
from .image_processor import ImageProcessor
from utils.constants import (
    DEFAULT_MODEL_NAME, DEFAULT_TEMPERATURE, DEFAULT_TOP_P, DEFAULT_MAX_OUTPUT_TOKENS
)
from utils.logger import log_info, log_error, log_warning, log_debug, log_critical
from PyQt6.QtCore import QObject, pyqtSignal, QThread, QTimer

class GeminiHandler(QObject):
    """
    Handles interactions with the Google Gemini API, managing multiple clients
    keyed by API key names for concurrent use.
    """
    models_updated = pyqtSignal(str, list)
    def __init__(self):
        super().__init__() 
        if not SDK_AVAILABLE:
            log_critical("Google GenAI SDK is not available. Cannot initialize GeminiHandler.")
            raise ImportError("google-genai library not found.")

        self.clients: Dict[str, genai.Client] = {}
        self.available_models_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._keys_currently_fetching_models: set[str] = set()

    def get_or_initialize_client(self, api_key_name: str, api_key_value: str) -> Optional[genai.Client]:
        """
        Retrieves an existing client for the given key name or initializes,
        validates, and stores a new one.

        Args:
            api_key_name: The user-defined name for the API key.
            api_key_value: The actual API key string.

        Returns:
            A validated genai.Client instance or None if initialization/validation fails.
        """
        if not api_key_name or not api_key_value or "YOUR_API_KEY" in api_key_value:
            log_error(f"Invalid API key name ('{api_key_name}') or value provided for client retrieval/initialization.")
            return None

        # 1. Check if a validated client already exists for this name
        if api_key_name in self.clients:
             log_debug(f"Returning existing client for key name: {api_key_name}")
             return self.clients[api_key_name]

        # 2. Attempt to initialize and validate a new client
        log_info(f"Initializing and validating new GenAI Client for key name: {api_key_name}...")
        try:
            # Explicitly create a new client instance
            new_client = genai.Client(api_key=api_key_value)

            # Perform a simple test call (e.g., list models) *using this specific client*
            # to verify the key and permissions before storing it.
            # Note: We don't store the result here, just check for errors.
            # It's okay if listing returns empty, but it shouldn't raise permission errors.
            _ = list(new_client.models.list(config={'page_size': 1})) # Validation call

            log_info(f"Client for '{api_key_name}' initialized and validated successfully.")
            self.clients[api_key_name] = new_client
            return new_client

        except google_api_core_exceptions.PermissionDenied as perm_err:
             log_error(f"API Key Error during validation for '{api_key_name}': Permission Denied. Check key validity and Gemini API permissions. Details: {perm_err}")
             # Don't store the invalid client
             return None
        except (google_errors.APIError, google_api_core_exceptions.GoogleAPIError, Exception) as e: # Catch correct error types
            log_error(f"Failed during client initialization or validation for '{api_key_name}': {e}", exc_info=True)
            return None

    def is_client_available(self, api_key_name: str) -> bool:
        """Checks if a validated client exists for the given API key name."""
        return api_key_name in self.clients

    def shutdown_client(self, api_key_name: str):
        """Removes a specific client and its associated cache."""
        if api_key_name in self.clients:
            log_info(f"Shutting down client and clearing cache for key: {api_key_name}")
            del self.clients[api_key_name]
            if api_key_name in self.available_models_cache:
                del self.available_models_cache[api_key_name]
        else:
            log_debug(f"No active client found to shut down for key: {api_key_name}")

    def shutdown_all_clients(self):
         """Removes all stored clients and clears caches."""
         log_info("Shutting down all Gemini clients and clearing caches.")
         self.clients.clear()
         self.available_models_cache.clear()



    def list_models(self, api_key_name: str, api_key_value: str, force_refresh: bool = False) -> List[Dict[str, Any]]:
        client = self.get_or_initialize_client(api_key_name, api_key_value)
        if not client:
            log_error(f"Cannot list models for '{api_key_name}': Client not available or failed initialization.")
            return []

        # Check cache first IF NOT forcing refresh
        if not force_refresh and api_key_name in self.available_models_cache:
             log_info(f"Returning cached list of models for key: {api_key_name}")
             cached_list = self.available_models_cache[api_key_name]
             # Emit signal even when returning cached data, so UI can update if needed
             # Wrap emit in try-except in case signal connection is problematic
             try:
                 self.models_updated.emit(api_key_name, cached_list)
             except Exception as emit_err:
                 log_error(f"Error emitting models_updated signal for cached data (key: {api_key_name}): {emit_err}")
             return cached_list

        # Prevent Concurrent Fetches
        if api_key_name in self._keys_currently_fetching_models:
            log_warning(f"Model fetch already in progress for key '{api_key_name}'. Returning empty list for now.")
            return []

        self._keys_currently_fetching_models.add(api_key_name)

        log_info(f"Fetching available models from API for key: {api_key_name}...")
        models_list = []
        fetched_models = [] # Store the result before updating cache/emitting signal
        error_occurred = False
        try:
            pager = client.models.list()
            for model in pager:
                supported_actions = getattr(model, 'supported_actions', [])
                # --- MODIFICATION: Removed the filter for supported_actions ---
                # Now, we process every model returned by the API
                likely_image_support = (
                    "image" in model.name.lower() or "vision" in model.name.lower() or
                    "flash" in model.name.lower() or "pro" in model.name.lower() and "vision" not in model.name.lower() or
                    "pixel" in model.name.lower() or
                    (hasattr(model, 'description') and model.description and "image" in model.description.lower()) or
                    ("generateImages" in supported_actions) or "imagen" in model.name.lower()
                )
                models_list.append({
                     "display_name": model.display_name, "name": model.name,
                     "description": getattr(model, 'description', 'N/A'),
                     "supported_actions": supported_actions, # Still store this info
                     "input_token_limit": getattr(model, 'input_token_limit', 'N/A'),
                     "output_token_limit": getattr(model, 'output_token_limit', 'N/A'),
                     "version": getattr(model, 'version', 'N/A'),
                     "likely_image_support": likely_image_support
                })
            # --- END MODIFICATION ---

            log_info(f"Found {len(models_list)} models (unfiltered) for key: {api_key_name}.")
            sorted_models = sorted(models_list, key=lambda x: x['display_name'])
            fetched_models = sorted_models

        except google_errors.PermissionDeniedError as api_err:
             log_error(f"API Key Error listing models for '{api_key_name}': {api_err}")
             self.shutdown_client(api_key_name)
             error_occurred = True
        except (google_errors.APIError, google_api_core_exceptions.GoogleAPIError) as api_err:
             log_error(f"API Error listing models for '{api_key_name}': {api_err}")
             error_occurred = True
        except Exception as e:
            log_error(f"Unexpected error listing models for '{api_key_name}': {e}", exc_info=True)
            error_occurred = True
        finally:
            self._keys_currently_fetching_models.discard(api_key_name)
            log_debug(f"Model fetch finished for key '{api_key_name}'. Lock released.")

        if not error_occurred:
            self.available_models_cache[api_key_name] = fetched_models
            try:
                self.models_updated.emit(api_key_name, fetched_models)
            except Exception as emit_err:
                 log_error(f"Error emitting models_updated signal for newly fetched data (key: {api_key_name}): {emit_err}")
        else:
            fetched_models = []

        return fetched_models




    def get_model_details(self, api_key_name: str, api_key_value: str, model_name: str) -> Optional[Dict[str, Any]]:
        """Gets detailed information about a specific model using the specified API key."""
        client = self.get_or_initialize_client(api_key_name, api_key_value)
        if not client:
            log_error(f"Cannot get model details for '{model_name}' with key '{api_key_name}': Client not available.")
            return None
        if not model_name:
            log_error("Model name cannot be empty.")
            return None

        log_info(f"Fetching details for model '{model_name}' using key '{api_key_name}'...")
        try:
            # Use the specific client instance
            model_details = client.models.get(model=model_name)
            details_dict = {}
            if hasattr(model_details, 'model_dump'):
                details_dict = model_details.model_dump(exclude_none=True)
            else:
                 for attr in dir(model_details):
                      if not attr.startswith('_') and not callable(getattr(model_details, attr)):
                           details_dict[attr] = getattr(model_details, attr)

            # Add likely_image_support heuristic if missing
            if 'likely_image_support' not in details_dict:
                supported_actions = details_dict.get('supported_actions', [])
                details_dict['likely_image_support'] = (
                        "image" in details_dict.get('name','').lower() or
                        "vision" in details_dict.get('name','').lower() or
                        "flash" in details_dict.get('name','').lower() or
                        "pro" in details_dict.get('name','').lower() and "vision" not in details_dict.get('name','').lower() or
                        "pixel" in details_dict.get('name','').lower() or
                        ("description" in details_dict and "image" in details_dict["description"].lower()) or
                        ("generateImages" in supported_actions) or
                        "imagen" in details_dict.get('name','').lower()
                    )

            log_info(f"Details fetched successfully for {model_name} using key '{api_key_name}'.")
            return details_dict

        except google_errors.PermissionDeniedError as api_err:
             log_error(f"API Key Error getting model details for '{model_name}' using '{api_key_name}': {api_err}")
             # Remove the potentially invalid client
             self.shutdown_client(api_key_name)
             return None
        except (google_errors.GoogleAPIError, google_api_core_exceptions.GoogleAPIError) as api_err:
            log_error(f"API Error getting model details for '{model_name}' using '{api_key_name}': {api_err}")
            return None
        except Exception as e:
            log_error(f"Unexpected error getting model details for '{model_name}' using '{api_key_name}': {e}", exc_info=True)
            return None
    
    def shutdown_all_clients(self):
        """Removes all stored clients and clears caches."""
        log_info("Shutting down all Gemini clients and clearing caches.")
        self.clients.clear()
        self.available_models_cache.clear()
    





    def generate(
        self,
        api_key_name: str,              # Name of the key to use
        api_key_value: str,             # Value of the key to use
        model_name: str,
        # --- MODIFIED: Expect RESOLVED prompt text ---
        prompt_text: str, # Expect ALREADY RESOLVED prompt
        # --- REMOVED wildcard_resolver parameter ---
        image_paths: Optional[List[Path]] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        safety_settings_dict: Optional[Dict[Any, Any]] = None, # Expects Dict[HarmCategory, HarmBlockThreshold] or None
        request_timeout: Optional[int] = None # NOTE: Timeout not directly supported by generate_content config
    ) -> Dict[str, Any]:
        """
        Generates content using the specified API key, model, and parameters,
        expecting an already resolved prompt text. Strictly follows google-genai SDK patterns.
        """

        # 1. Get Client & Validate Inputs
        client = self.get_or_initialize_client(api_key_name, api_key_value)
        if not client:
            # No resolved prompt to return here as validation failed early
            return {"status": "error", "error_message": f"Client for API key '{api_key_name}' not available."}

        if not model_name:
            # No resolved prompt to return here as validation failed early
            return {"status": "error", "error_message": "Model name not provided."}
        # --- MODIFIED: Check resolved prompt ---
        if not prompt_text:
            # Input prompt_text is required (it should be the resolved one)
            return {"status": "error", "error_message": "Resolved prompt text cannot be empty."}
        # --- END MODIFICATION ---

        start_time = time.time()
        log_info(f"Starting generation request for model '{model_name}' using key '{api_key_name}'.")
        # Store the received (resolved) prompt directly for the result dictionary
        resolved_prompt_for_result = prompt_text

        # 2. Prepare Contents (Uses the already resolved prompt_text)
        api_contents: Union[str, List[Union[str, Image.Image]]]
        pil_images: List[Image.Image] = [] # Keep track to close them later
        has_image_input = bool(image_paths)

        if has_image_input:
            log_debug(f"Preparing image content from paths: {image_paths}")
            # Start the contents list with the already resolved prompt text
            api_contents = [resolved_prompt_for_result]
            for img_path in image_paths:
                # Load image using Pillow (required by google-genai SDK for image input)
                pil_image = ImageProcessor.load_image_for_api(img_path)
                if pil_image:
                    pil_images.append(pil_image)
                    # Append the PIL Image object directly to the contents list
                    api_contents.append(pil_image)
                else:
                    # Handle image loading failure
                    error_msg = f"Failed to load image: {img_path.name}"
                    log_error(error_msg)
                    for img in pil_images: img.close() # Clean up already loaded images
                    # Return the resolved prompt that was being processed
                    return {"status": "error", "error_message": error_msg, "resolved_prompt": resolved_prompt_for_result}
            # Ensure at least one image was successfully loaded if paths were provided
            if not pil_images:
                    error_msg = "Image paths provided, but failed to load any images."
                    log_error(error_msg)
                    # Return the resolved prompt that was being processed
                    return {"status": "error", "error_message": error_msg, "resolved_prompt": resolved_prompt_for_result}
            log_info(f"Prepared content with resolved text and {len(pil_images)} image(s).")
        else:
            # If no images, the contents is just the resolved prompt string
            api_contents = resolved_prompt_for_result
            log_info("Prepared content with resolved text only.")

        # 3. Prepare GenerationConfig (No changes needed in this section)
        # This section correctly builds the config object based on SDK types
        generation_config_args = {}
        generation_config_obj = None
        if temperature is not None: generation_config_args['temperature'] = temperature
        if top_p is not None: generation_config_args['top_p'] = top_p
        if max_output_tokens is not None: generation_config_args['max_output_tokens'] = max_output_tokens
        safety_settings_list = []
        if safety_settings_dict and SDK_AVAILABLE:
            log_debug(f"Processing safety settings dict: {safety_settings_dict}")
            for category_enum, threshold_enum in safety_settings_dict.items():
                if isinstance(category_enum, types.HarmCategory) and isinstance(threshold_enum, types.HarmBlockThreshold):
                    if threshold_enum != types.HarmBlockThreshold.HARM_BLOCK_THRESHOLD_UNSPECIFIED:
                        safety_settings_list.append(types.SafetySetting(category=category_enum, threshold=threshold_enum))
                        log_debug(f"  Adding SafetySetting: Category={category_enum.name}, Threshold={threshold_enum.name}")
                    else: log_debug(f"  Skipping UNSPECIFIED threshold for category {category_enum.name}")
                else: log_warning(f"Invalid type found in safety_settings_dict. Skipping this entry.")
            if safety_settings_list: generation_config_args['safety_settings'] = safety_settings_list; log_debug(f"Adding 'safety_settings' to config_args: {safety_settings_list}")
            else: log_debug("No specific (non-default) safety settings were provided or valid.")
        elif safety_settings_dict is None: log_debug("No safety settings dict provided. Using API defaults.");
        elif not safety_settings_dict: log_debug("Empty safety settings dict provided. Using API defaults.")
        elif safety_settings_dict and not SDK_AVAILABLE: log_warning("Safety settings provided but SDK unavailable.")

        model_info = next((m for m in self.available_models_cache.get(api_key_name, []) if m['name'] == model_name), None)
        if model_info: likely_image_support = model_info.get('likely_image_support', False); log_debug(f"Image support from cache for {model_name}: {likely_image_support}")
        else: likely_image_support = ("image" in model_name.lower() or "vision" in model_name.lower() or "flash" in model_name.lower() or "pixel" in model_name.lower() or "imagen" in model_name.lower()); log_warning(f"Model info cache miss for '{model_name}'. Inferred image support: {likely_image_support}")

        # Decide response modalities based on input type and inferred model capability
        if has_image_input or likely_image_support:
            generation_config_args['response_modalities'] = ['TEXT', 'IMAGE']
            log_debug("Requesting TEXT and IMAGE modalities.")
        else:
            generation_config_args['response_modalities'] = ['TEXT']
            log_debug("Requesting only TEXT modality.")

        # Create the config object if needed
        if generation_config_args:
            if SDK_AVAILABLE and hasattr(types, 'GenerateContentConfig'):
                try:
                    generation_config_obj = types.GenerateContentConfig(**generation_config_args)
                    log_debug(f"Created GenerateContentConfig object: {generation_config_obj}")
                except Exception as config_err:
                    log_error(f"Error creating GenerateContentConfig: {config_err}", exc_info=True);
                    for img in pil_images: img.close()
                    # --- Return resolved prompt in error dict ---
                    return {"status": "error", "error_message": f"Failed to create GenerationConfig: {config_err}", "resolved_prompt": resolved_prompt_for_result}
            else:
                log_warning("Cannot create GenerateContentConfig: SDK or type missing."); generation_config_obj = None
        else:
            log_debug("No arguments for GenerateContentConfig, using API defaults."); generation_config_obj = None

        # 4. Make API Call using the client.models.generate_content method
        response = None
        try:
            log_info(f"Sending request to model '{model_name}' using client for key '{api_key_name}'...")
            # Pass the prepared api_contents (string or list with PIL Images) and config object
            response = client.models.generate_content(
                model=model_name,
                contents=api_contents, # Pass the resolved content
                config=generation_config_obj # Pass the config object (or None)
            )
            log_info(f"API response received for key '{api_key_name}'.")

        # 5. Error Handling (Revised Order and Types, includes resolved prompt)
        except google_errors.APIError as api_err:
            error_msg = f"Google GenAI API Error for key '{api_key_name}': {api_err}"
            status_code = getattr(api_err, 'code', None)
            if status_code == 429: # Rate Limit
                error_msg = f"Resource Exhausted (Rate Limit/Quota) for key '{api_key_name}'. Wait and retry."; log_error(error_msg, exc_info=False)
                return {"status": "rate_limited", "error_code": "RATE_LIMIT", "error_message": error_msg, "api_key_name": api_key_name, "resolved_prompt": resolved_prompt_for_result}
            elif status_code == 403: # Permission Denied
                error_msg = f"Permission Denied for key '{api_key_name}': {api_err}. Check key."; log_error(error_msg, exc_info=False)
                self.shutdown_client(api_key_name)
                return {"status": "error", "error_code": "AUTH_ERROR", "error_message": error_msg, "api_key_name": api_key_name, "resolved_prompt": resolved_prompt_for_result}
            else: # Generic API error
                log_error(error_msg, exc_info=True)
                return {"status": "error", "error_message": error_msg, "api_key_name": api_key_name, "resolved_prompt": resolved_prompt_for_result}
        except google_api_core_exceptions.DeadlineExceeded as timeout_err:
             error_msg = f"Request Timeout for key '{api_key_name}': {timeout_err}."; log_error(error_msg, exc_info=False)
             return {"status": "error", "error_code": "TIMEOUT", "error_message": error_msg, "api_key_name": api_key_name, "resolved_prompt": resolved_prompt_for_result}
        except google_api_core_exceptions.GoogleAPIError as core_api_err:
            error_msg = f"Google API Core Error for key '{api_key_name}': {core_api_err}"; log_error(error_msg, exc_info=True)
            return {"status": "error", "error_message": error_msg, "api_key_name": api_key_name, "resolved_prompt": resolved_prompt_for_result}
        except Exception as e:
            error_msg = f"Unexpected Error during API call for key '{api_key_name}': {e}"; log_error(error_msg, exc_info=True)
            # Fallback rate limit check
            if "RESOURCE_EXHAUSTED" in str(e).upper() or "429" in str(e):
                log_warning("Caught RESOURCE_EXHAUSTED via generic exception string match.")
                return {"status": "rate_limited", "error_code": "RATE_LIMIT_FALLBACK", "error_message": error_msg, "api_key_name": api_key_name, "resolved_prompt": resolved_prompt_for_result}
            else:
                return {"status": "error", "error_message": error_msg, "api_key_name": api_key_name, "resolved_prompt": resolved_prompt_for_result}
        finally:
            # Ensure PIL images are closed
            for img in pil_images:
                try: img.close()
                except Exception as close_err: log_warning(f"Error closing PIL image: {close_err}")

        # 6. Process Response (Includes resolved prompt in result)
        if not response:
            error_msg = "API call succeeded but returned no response object."; log_error(error_msg)
            return {"status": "error", "error_message": error_msg, "resolved_prompt": resolved_prompt_for_result}

        # Initialize result dictionary, including the resolved prompt used
        result_data = {
            "status": "success", "text_result": None, "image_bytes": None, "image_mime": None,
            "usage_metadata": None, "finish_reason": None, "prompt_feedback": None,
            "candidate_feedback": [], "error_message": None, "block_reason": None,
            "resolved_prompt": resolved_prompt_for_result, # Include the input resolved prompt
            "api_key_name": api_key_name,
            # Note: unresolved_prompt is NOT available here, must be added by the caller if needed
        }
        try:
            # Extract usage metadata
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                result_data["usage_metadata"] = {
                    "prompt_token_count": getattr(response.usage_metadata, 'prompt_token_count', 'N/A'),
                    "candidates_token_count": getattr(response.usage_metadata, 'candidates_token_count', 'N/A'),
                    "total_token_count": getattr(response.usage_metadata, 'total_token_count', 'N/A'),
                }
                if hasattr(response.usage_metadata, 'model_dump'):
                    try: result_data["usage_metadata"] = response.usage_metadata.model_dump(exclude_none=True)
                    except Exception: pass
                log_debug(f"Usage Metadata: {result_data['usage_metadata']}")

            # Check prompt feedback for immediate blocking
            prompt_blocked = False; prompt_block_reason_obj = None; prompt_safety_ratings = []
            if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                prompt_block_reason_obj = getattr(response.prompt_feedback, 'block_reason', None)
                prompt_safety_ratings = getattr(response.prompt_feedback, 'safety_ratings', [])
                unspecified_block_reason = getattr(types.BlockedReason, 'BLOCKED_REASON_UNSPECIFIED', None) if SDK_AVAILABLE else None
                if prompt_block_reason_obj and prompt_block_reason_obj != unspecified_block_reason:
                    result_data["block_reason"] = getattr(prompt_block_reason_obj, 'name', str(prompt_block_reason_obj))
                    result_data["status"] = "blocked"
                    result_data["error_message"] = f"Prompt blocked due to {result_data['block_reason']}."
                    log_warning(result_data["error_message"])
                    prompt_blocked = True
                    if prompt_safety_ratings: log_warning(f"Prompt Safety Ratings (Blocked): {prompt_safety_ratings}")
                result_data["prompt_feedback"] = {'block_reason': result_data.get("block_reason"), 'safety_ratings': prompt_safety_ratings}
            # Return early if prompt was blocked
            if prompt_blocked: return result_data

            # Process candidates if prompt wasn't blocked
            if not response.candidates:
                error_msg = "API response received, but contains no candidates."; log_warning(error_msg)
                if result_data["prompt_feedback"]: error_msg += f" Prompt feedback available: {result_data['prompt_feedback']}"
                result_data["status"] = "error"; result_data["error_message"] = error_msg; return result_data

            # Process the first candidate
            candidate = response.candidates[0]
            finish_reason_obj = getattr(candidate, 'finish_reason', None)
            if finish_reason_obj: result_data["finish_reason"] = getattr(finish_reason_obj, 'name', str(finish_reason_obj)); log_info(f"Candidate finish reason: {result_data['finish_reason']}")

            # Check candidate safety ratings for blocking
            candidate_blocked = False; safety_block_reason = None; candidate_safety_ratings = []
            if hasattr(candidate, 'safety_ratings') and candidate.safety_ratings:
                candidate_safety_ratings = candidate.safety_ratings; result_data["candidate_feedback"] = candidate_safety_ratings; log_debug(f"Candidate Safety Ratings: {candidate_safety_ratings}")
                for rating in candidate_safety_ratings:
                    if getattr(rating, 'blocked', False): category_obj = getattr(rating, 'category', None); safety_block_reason = getattr(category_obj, 'name', 'UNKNOWN_CATEGORY') if category_obj else 'UNKNOWN_CATEGORY'; candidate_blocked = True; break

            # Check finish reason for safety/block related reasons
            is_safety_finish = False
            safety_finish_reasons = {'SAFETY', 'BLOCKLIST', 'PROHIBITED_CONTENT'} # Use strings for comparison if SDK missing
            if SDK_AVAILABLE:
                if finish_reason_obj is not None: safety_finish_enums = [getattr(types.FinishReason, r, None) for r in safety_finish_reasons if hasattr(types.FinishReason, r)]; is_safety_finish = finish_reason_obj in filter(None, safety_finish_enums)
                elif result_data["finish_reason"] in safety_finish_reasons: is_safety_finish = True
            elif result_data["finish_reason"] in safety_finish_reasons: is_safety_finish = True

            # Set status to blocked if candidate safety or finish reason indicates it
            if candidate_blocked or is_safety_finish:
                result_data["status"] = "blocked"; block_description = safety_block_reason if candidate_blocked else result_data['finish_reason']; result_data["block_reason"] = block_description; result_data["error_message"] = f"Content generation stopped/blocked ({block_description})."; log_warning(result_data["error_message"])
                if candidate_safety_ratings: log_warning(f"Candidate Safety Ratings (Blocked): {candidate_safety_ratings}")
                # Score updates are handled by the caller now

            # Extract text and image parts
            text_parts = []; image_part_found = False
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if hasattr(part, 'text') and part.text: text_parts.append(part.text)
                    elif (hasattr(part, 'inline_data') and part.inline_data and hasattr(part.inline_data, 'mime_type') and part.inline_data.mime_type and part.inline_data.mime_type.startswith("image/") and hasattr(part.inline_data, 'data') and part.inline_data.data and not image_part_found):
                        result_data["image_bytes"] = part.inline_data.data; result_data["image_mime"] = part.inline_data.mime_type; image_part_found = True; log_info(f"Image part found (MIME: {result_data['image_mime']}, Size: {len(result_data['image_bytes'])} bytes).")

            if text_parts: result_data["text_result"] = "\n".join(text_parts).strip(); log_info("Text part(s) found."); log_debug(f"Text Result:\n{result_data['text_result'][:500]}...")

            # Handle cases where generation stopped but produced no content
            if result_data["status"] == "success":
                if not result_data["text_result"] and not result_data["image_bytes"]:
                    max_tokens_reason = getattr(types.FinishReason, 'MAX_TOKENS', 'MAX_TOKENS').name if SDK_AVAILABLE else 'MAX_TOKENS'; stop_reason = getattr(types.FinishReason, 'STOP', 'STOP').name if SDK_AVAILABLE else 'STOP'
                    if result_data["finish_reason"] == max_tokens_reason: result_data["status"] = "error"; result_data["error_message"] = "Finished due to MAX_TOKENS but no output."; log_warning(result_data["error_message"])
                    elif result_data["finish_reason"] == stop_reason: result_data["status"] = "success"; log_info("Stopped normally but empty content.")
                    else: result_data["status"] = "error"; result_data["error_message"] = f"No content generated (Finish: {result_data['finish_reason']})."; log_warning(result_data["error_message"])

        except AttributeError as ae:
            error_msg = f"Error processing response structure (AttributeError): {ae}"
            log_error(error_msg, exc_info=True)
            result_data["status"] = "error"
            result_data["error_message"] = error_msg
            # Attempt to log the raw response, but don't fail if that also errors
            try:
                log_error(f"Raw response object structure issue: {response}")
            except Exception:
                log_error("Could not even log the raw response object.")
            # Fall through to return result_data with error status set
        except Exception as e:
                    error_msg = f"Unexpected error processing response: {e}"
                    log_error(error_msg, exc_info=True)
                    result_data["status"] = "error"
                    result_data["error_message"] = error_msg
                    # Attempt to log the raw response, but don't fail if that also errors
                    try:
                        log_error(f"Raw response object issue: {response}")
                    except Exception:
                        log_error("Could not even log the raw response object during general exception.")
                    # Fall through to return result_data with error status set

        end_time = time.time()
        log_info(f"Generation request for key '{api_key_name}' finished in {end_time - start_time:.2f} seconds. Final Status: {result_data['status']}")
        # Score updates are handled by the caller now
        return result_data