# -*- coding: utf-8 -*-
import os
import sys
from pathlib import Path
# --- Base Directory ---
# Determine if running as a bundled executable (PyInstaller) or script
if getattr(sys, 'frozen', False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).parent.parent # Assuming constants.py is in utils/

# --- Data Directories ---
DATA_DIR = APP_DIR / "data"
API_KEYS_DIR = DATA_DIR / "api_keys"
PROMPTS_DIR = DATA_DIR / "prompts"
PROMPTS_ASSETS_DIR = PROMPTS_DIR / "assets"
SETTINGS_DIR = DATA_DIR / "settings"
LOGS_DIR = DATA_DIR / "logs"
WILDCARDS_DIR = DATA_DIR / "wildcards"
THEMES_DIR = APP_DIR / "themes"
# --- File Names ---
API_KEYS_FILE = API_KEYS_DIR / "keys.json"
PROMPTS_FILE = PROMPTS_DIR / "prompts.json"
SETTINGS_FILE = SETTINGS_DIR / "app_settings.json"
LOG_FILE = LOGS_DIR / "app.log"
SAVE_TEXT_FILE_ENABLED = "save_text_file_enabled"
EMBED_METADATA_ENABLED = "embed_metadata_enabled"
# --- Default Values ---
DEFAULT_FILENAME = 'generated_image.png'
DEFAULT_API_KEY_PLACEHOLDER = "YOUR_API_KEY_NAME"
DEFAULT_MODEL_NAME = "models/gemini-2.0-flash-exp-image-generation" # Or another suitable default
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TOP_P = 1.0
DEFAULT_MAX_OUTPUT_TOKENS = 2048 # Adjust as needed
DEFAULT_RETRY_COUNT = 3
DEFAULT_RETRY_DELAY = 5 # seconds
DEFAULT_REQUEST_DELAY = 1 # seconds
DEFAULT_TEMPERATURE = 1.0
DEFAULT_TOP_P = 0.95
DEFAULT_MAX_OUTPUT_TOKENS = 8192
DEFAULT_LOGGING_ENABLED = True
DEFAULT_AUTO_SAVE_ENABLED = True
DEFAULT_SAVE_TEXT_FILE_ENABLED = True # Save text file by default
DEFAULT_EMBED_METADATA_ENABLED = True # Embed metadata by default
DEFAULT_THEME = "Auto" # Auto, Light, Dark
DEFAULT_FILENAME_PATTERN = "{date}_{time}_{model}_{prompt_hash}" # Example: 20231027_153000_gemini-pro_a1b2c3d4.png
DEFAULT_FILENAME_PATTERN_NAME = "Default"
SAVED_FILENAME_PATTERNS_KEY = "saved_filename_patterns"
ACTIVE_FILENAME_PATTERN_NAME_KEY = "active_filename_pattern_name"

# --- Help Text ---
FILENAME_HELP_TEXT = """
**Filename Pattern Help**

Define how generated files are named using placeholders and conditional blocks.
Invalid filename characters (< > : " / \\ | ? * and control chars) will be replaced with underscores.

**Available Placeholders:**

*   `{date}`: Current date (YYYYMMDD) - e.g., 20231027
*   `{time}`: Current time (HHMMSS) - e.g., 154500
*   `{datetime}`: Date and Time (YYYYMMDD_HHMMSS) - e.g., 20231027_154500
*   `{model}`: Name of the model used (sanitized).
*   `{key_name}`: Name of the API Key used.
*   `{instance_id}`: Instance ID in Multi-Mode ('NA' in Single-Mode).
*   `{prompt_hash}`: Short unique hash (8 chars) of the *resolved* prompt.
*   `{unresolved_prompt_hash}`: Short unique hash (8 chars) of the *unresolved* prompt.
*   `{prompt_start:N}`: First N characters of the *resolved* prompt (default N=50).
*   `{prompt_end:N}`: Last N characters of the *resolved* prompt (default N=50).
*   `{wc:name}`: The resolved value(s) for a wildcard. If `[name]` resolved to "Monet", `{wc:name}` becomes `Monet`. If multiple wildcards of the same name are used (e.g., `[colors]`, `[1:colors]`), their values are joined by underscores. **This is the recommended way to include wildcard values.**
*   `{sequence_number}`: Added automatically by the system if a file with the same name already exists (e.g., `_001`). You do not need to add this.

**Conditional Blocks:**

Use double square brackets `[[...]]` to define optional parts of your filename.
If all placeholders inside a conditional block resolve to an empty string, the entire block (including any static text) will be removed from the filename.

**Example Pattern:**
`{date}_{model}_[[by_{wc:artist}_]]({prompt_hash})`

*   **If `[artist]` resolves to "Van Gogh":**
    `{wc:artist}` becomes `Van_Gogh`. The block becomes `by_Van_Gogh_`.
    *Result:* `20231027_gemini-pro_by_Van_Gogh_(a1b2c3d4).png`

*   **If `[artist]` is NOT used in the prompt:**
    `{wc:artist}` is empty. The block `[[by__]]` is considered empty and is removed entirely.
    *Result:* `20231027_gemini-pro_(a1b2c3d4).png`
"""



# --- UI Defaults ---
MAX_PROMPT_SLOTS = 999999 # Example limit for prompt manager

# --- Metadata Keys ---
METADATA_KEY_UNRESOLVED_PROMPT = "UserComment" # Using a common EXIF tag
METADATA_KEY_RESOLVED_PROMPT = "XPComment" # Using another common EXIF tag (Windows)
# Consider using custom PNG chunks for more flexibility if needed

# --- Wildcard Regex ---
WILDCARD_REGEX = r"(\{(?!\d+:)[^}]+\})|\[(\d+:)?([^:\]]+)(?::(\d+))?\]"
# Matches:
# {wildcard} -> group 1
# [wildcard] -> group 3
# [1:wildcard] -> group 2 = "1:", group 3 = "wildcard"
# [wildcard:2] -> group 3 = "wildcard", group 4 = "2"
# [1:wildcard:2] -> group 2 = "1:", group 3 = "wildcard", group 4 = "2"

# --- Ensure Directories Exist ---
def ensure_dirs():
    """Creates necessary data directories if they don't exist."""
    dirs_to_create = [
        DATA_DIR, API_KEYS_DIR, PROMPTS_DIR,
        SETTINGS_DIR, LOGS_DIR, WILDCARDS_DIR,
        PROMPTS_ASSETS_DIR # <<< Added this line
    ]
    for d in dirs_to_create:
        d.mkdir(parents=True, exist_ok=True)
    # Create dummy wildcard file if it doesn't exist
    dummy_wildcard = WILDCARDS_DIR / "example.txt"
    if not dummy_wildcard.exists():
        try:
            dummy_wildcard.write_text("random line 1\nrandom line 2")
        except OSError:
             # Handle potential write permission errors gracefully in a real app
             print(f"Warning: Could not create dummy wildcard file at {dummy_wildcard}")

# Call ensure_dirs on import? Or explicitly call it in main_app.py
# Let's call it explicitly in main_app.py for better control.

# --- Wildcards syntax helper ---
WILDCARD_SYNTAX_HELP_TEXT = """
Wildcard Syntax Reference
=========================

Here are all the ways you can format wildcards in your prompts, from the simplest to the most complex combinations.

1. Basic Random Selection: {wildcard}
-------------------------------------
- Syntax: {wildcard_name}
- Behavior: Finds `wildcard_name.json` and inserts a single, randomly chosen value.
- Key Detail: If you use the same curly-brace wildcard multiple times, a NEW random value is chosen each time.
- Example: A {colors} car and a {colors} house. -> A red car and a blue house.

2. Bracketed Random Selection: [wildcard]
-----------------------------------------
- Syntax: [wildcard_name]
- Behavior: On its own, works identically to the curly-brace version. Its real power is enabling the modifiers below.
- Example: A [colors] car. -> A green car.

3. Numbered Wildcard (Consistency): [N:wildcard]
------------------------------------------------
- Syntax: [number:wildcard_name]
- Behavior: Guarantees that all wildcards with the same number (e.g., all instances of `[1:...]`) will resolve to the *same random choice* within a single prompt generation.
- Key Detail: Different numbers resolve independently. `[1:artists]` and `[2:artists]` will pick two different random artists.
- Example: A painting by [1:artists]. The work of [1:artists] is known for its energetic themes. -> Both instances become "Van Gogh".

4. The "OR" Operator (Choice of File): [wildcard1|wildcard2]
------------------------------------------------------------
- Syntax: [name1|name2|name3|...]
- Behavior: Randomly chooses ONE of the wildcard files listed (e.g., chooses between `Females.json`, `Males.json`, or `Animals.json`), and then picks a random value from that single chosen file.
- Example: A portrait of a [Females|Males|Animals]. -> (picks `Animals.json`) -> A portrait of a Lion.

5. Count Suffix (Repetition): [wildcard:N]
-----------------------------------------
- Syntax: [wildcard_name:number]
- Behavior: Resolves the specified wildcard `N` times, with each resolution being a new random choice, and joins the results with a space.
- Example: A bouquet of [colors:3] flowers. -> A bouquet of red blue yellow flowers.

Combining Modifiers (Order of Operations)
=========================================
The "OR" Operator (`|`) is resolved FIRST. The chosen filename is then used for the Numbered (`N:`) and Count (`:N`) modifiers.

- Consistency with Choice: `[1:positive_adj|negative_adj]`
  (First, it chooses between `positive_adj.json` and `negative_adj.json`, then picks one value and uses it for all `[1:...]` instances).

- Repetition with Choice: `[colors|shapes:3]`
  (First, it chooses between `colors.json` and `shapes.json`, then picks three random values from the chosen file).

- The Ultimate Combination: `[1:adjectives|artists:2]`
  1. "OR" Logic: Chooses between `adjectives.json` and `artists.json`. Let's say it picks `artists`.
  2. The prompt is now treated as `[1:artists:2]`.
  3. "Numbered" Logic: The first resolution picks a random artist (e.g., "Monet") and caches it for number `1`.
  4. "Count" Logic: It needs to resolve twice. Both resolutions will be "Monet" because the value for number `1` is cached.
  5. Final Result: `Monet Monet`
"""

# --- General Application Help ---
GENERAL_APP_HELP_TEXT = """
Gemini Studio UI - General Help
===============================

Welcome to Gemini Studio UI! This guide explains the main features of the application.

--- Main Interface ---
The application has two primary modes, switchable from the "View" menu:

1.  **Single-API Mode:**
    - A straightforward interface for running one generation at a time.
    - Ideal for testing prompts, iterating on ideas, and simple generation tasks.
    - Configure your API Key, Model, and parameters at the top.
    - Use the "Generate" button to start and "Cancel" to stop.

2.  **Multi-API Mode:**
    - A powerful interface for running multiple, parallel generation instances.
    - Each "instance" can have its own prompt, parameters, and API key.
    - Ideal for A/B testing prompts, generating variations, or maximizing throughput.
    - Use the "Add API Instance" button to create new instances.
    - Each instance can be started, stopped, and configured independently.
    - Use the "Start All Ready" and "Stop All Running" buttons for global control.
    - Warning: Running many instances simultaneously can quickly consume API quotas. A warning will appear before adding the 5th instance.

--- Key Features (Tools Menu) ---

-   **API Key Manager:**
    - Securely add, rename, and delete your Google AI Studio API keys.
    - Keys are encrypted on your local disk.
    - In Multi-Mode, each new instance will automatically pick an available, unused key.

-   **Prompt Manager:**
    - Save your favorite or most-used prompts for quick access.
    - Each prompt can have a name, text, and a thumbnail image.
    - From the manager, you can load a saved prompt directly into the active mode (Single Mode or a specific Multi-Mode instance).

-   **Wildcard Manager:**
    - View and edit the content of your wildcard files (located in `data/wildcards/`).
    - Add or remove values from each file.
    - Scores (Success/Blocked) are updated automatically based on generation outcomes to help you track which values perform best.

-   **View Image Metadata:**
    - Load a previously generated image to view the unresolved and resolved prompts that were embedded in its metadata.
    - You can load these prompts back into the application.

-   **Settings:**
    - Configure application-wide settings like theme, logging, default generation parameters, and the filename pattern for auto-saved files.

--- Wildcards ---
For a detailed guide on how to use the powerful wildcard syntax, please see "Help -> Wildcard Syntax".
"""