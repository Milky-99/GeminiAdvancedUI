# -*- coding: utf-8 -*-
import re
import time
import mimetypes
import traceback
import copy
from pathlib import Path
from typing import Optional, List, Dict, Any
import webbrowser
import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QGroupBox, QComboBox, QDoubleSpinBox,
    QSpinBox, QPlainTextEdit, QCheckBox, QScrollArea, QFileDialog, QHBoxLayout,
    QSpacerItem, QSizePolicy, QProgressBar, QGridLayout, QFrame, QApplication, QDialog, QLineEdit 
)
from PyQt6.QtCore import pyqtSignal, Qt, QThread, QObject, pyqtSlot, QSize, QTimer, QEvent, QCoreApplication, QMetaObject
from PyQt6.QtGui import QPixmap, QImage, QIcon 

# --- Project Imports ---
from utils import constants
from utils.logger import log_info, log_debug, log_error, log_warning
from utils.helpers import show_error_message, show_info_message, show_warning_message, get_themed_icon
from core.settings_service import SettingsService
from core.api_key_service import ApiKeyService
from core.prompt_service import PromptService
from core.wildcard_resolver import WildcardResolver
from core.gemini_handler import GeminiHandler, SDK_AVAILABLE # Import SDK_AVAILABLE
from core.filename_generator import FilenameGeneratorService
try:
    # Keep SDK type imports for safety settings if available
    from google.genai import types as google_types
    SDK_TYPES_AVAILABLE = True
except ImportError:
    google_types = None
    # Use the SDK_AVAILABLE flag from gemini_handler for general SDK presence check
    SDK_TYPES_AVAILABLE = SDK_AVAILABLE

from core.image_processor import ImageProcessor
# Import the Safety Settings Dialog
from .safety_settings_dialog import SafetySettingsDialog


# --- Worker Thread for API Calls ---
class GenerationWorker(QObject):
    """Worker object to handle API calls in a separate thread."""
    finished = pyqtSignal(dict) # Signal emitting the result dictionary
    progress = pyqtSignal(str)  # Signal for status updates



    def __init__(self,
                 gemini_handler: GeminiHandler,
                 api_key_name: str,
                 api_key_value: str,
                 params: dict,
                 resolved_wildcards_map: Dict[str, List[str]], # <<< Existing Explicit Argument
                 image_filename_context: Optional[str] = None):
        super().__init__()
        log_debug(f"--- GenerationWorker __init__ CALLED (Key: {api_key_name}) ---")
        self.gemini_handler = gemini_handler
        self.api_key_name = api_key_name
        self.api_key_value = api_key_value
        self.params = params # Store the whole dict
        self._is_cancelled = False
        self._is_running = False
        self.image_filename_context = image_filename_context

        self.filename_wildcard_values = { k: v for k, v in params.items() if k.startswith("wildcard_value_") }
        log_debug(f"[Worker.__init__] Received filename wildcard values: {self.filename_wildcard_values}")

        self.retry_count = params.get("retry_count", constants.DEFAULT_RETRY_COUNT)
        self.retry_delay = params.get("retry_delay", constants.DEFAULT_RETRY_DELAY)

        # --- Store DEEP COPY of the EXPLICIT map ---
        self._initial_resolved_wildcards_by_name = copy.deepcopy(resolved_wildcards_map)
        log_info(f"[Worker.__init__] Stored DEEP COPY of explicit resolved_wildcards_map: {self._initial_resolved_wildcards_by_name}")

        # --- Store resolved and unresolved prompts passed in params ---
        self.resolved_prompt = params.get("resolved_prompt_text", "") # Get RESOLVED prompt
        self.unresolved_prompt = params.get("unresolved_prompt_text", "") # Get UNRESOLVED prompt

        log_debug(f"[Worker.__init__] Retry settings: Count={self.retry_count}, Delay={self.retry_delay}s")
        log_debug(f"--- GenerationWorker __init__ COMPLETE (Key: {api_key_name}) ---")



    @pyqtSlot()
    def run(self):
        """Executes the generation task. It will make ONE attempt.
        If rate limited, it reports 'rate_limited' status.
        Other errors are reported as 'error'.
        """
        thread_id = QThread.currentThreadId()
        log_debug(f"GenerationWorker started on thread {thread_id} for model: {self.params.get('model_name')} using key: {self.api_key_name}")
        start_time = time.time()
        result = {} # Initialize result dict

        # --- Base result info to include in all outcomes ---
        base_result = {
            "unresolved_prompt": self.unresolved_prompt,
            "resolved_prompt": self.resolved_prompt,
            "image_filename_context": self.image_filename_context,
            "resolved_wildcards_by_name": self._initial_resolved_wildcards_by_name,
            **self.filename_wildcard_values
        }

        if self._is_cancelled:
            log_info(f"GenerationWorker (Thread {thread_id}, Key: {self.api_key_name}) cancelled before start.")
            result = {"status": "cancelled", "error_message": "Operation cancelled."}
            result.update(base_result)
            self.finished.emit(result)
            return

        # --- Perform a single API call attempt ---
        log_debug(f"GenerationWorker: Making single API call attempt...")
        try:
            # Prepare args for the handler generate call
            generate_args = {
                k: v for k, v in self.params.items()
                if k not in [
                    'retry_count', 'retry_delay', # These are no longer used by the worker for rate limits
                    'resolved_wildcards_by_name',
                    'wildcard_resolver',
                    'resolved_prompt_text',
                    'unresolved_prompt_text'
                ] and not k.startswith("wildcard_value_")
            }
            generate_args["prompt_text"] = self.resolved_prompt # Pass the resolved prompt

            handler_result = self.gemini_handler.generate(
                api_key_name=self.api_key_name,
                api_key_value=self.api_key_value,
                **copy.deepcopy(generate_args)
            )
            result = handler_result # This is the result of the single attempt

            # The status ("rate_limited", "error", "success", "blocked")
            # is now directly from the GeminiHandler.

        except Exception as e:
            log_error(f"Exception in GenerationWorker run method (Key: {self.api_key_name}): {e}", exc_info=True)
            tb_str = traceback.format_exc()
            result = {"status": "error", "error_message": f"Worker thread error: {e}\nTraceback:\n{tb_str}"}
        # --- End single API call attempt ---

        # --- Final Result Processing ---
        result.update(base_result) # Add base info to the final result

        if self._is_cancelled and result.get("status") != "cancelled":
            # If cancellation happened during the API call (which is hard to detect from here cleanly without more complex IPC)
            # or if the API call was very short and cancellation was requested almost simultaneously.
            log_info(f"GenerationWorker (Thread {thread_id}, Key: {self.api_key_name}) was cancelled during execution or just before emission.")
            result["status"] = "cancelled"
            result["error_message"] = "Operation cancelled by user."

        log_debug(f"GenerationWorker (Thread {thread_id}, Key: {self.api_key_name}) emitting final result (Status: {result.get('status')}).")
        self.finished.emit(result)

        end_time = time.time()
        log_debug(f"GenerationWorker (Thread {thread_id}, Key: {self.api_key_name}) finished in {end_time - start_time:.2f}s. Final Status: {result.get('status')}")


    @pyqtSlot()
    def cancel(self):
         self._is_cancelled = True
         log_info(f"Cancellation requested for GenerationWorker (Key: {self.api_key_name}).")

# --- Main Widget ---
class SingleModeWidget(QWidget):
    """Widget for handling single API key interactions."""
    status_update = pyqtSignal(str, int) # message, timeout


    def __init__(self,
                 settings_service: SettingsService,
                 api_key_service: ApiKeyService,
                 prompt_service: PromptService,
                 wildcard_resolver: WildcardResolver,
                 gemini_handler: GeminiHandler,
                 parent=None):
        super().__init__(parent)

        if not SDK_AVAILABLE: # Check general SDK availability
            log_error("Google GenAI SDK could not be imported. Some features might be disabled.")
        # Keep SDK_TYPES_AVAILABLE for specific safety dialog features
        if not SDK_TYPES_AVAILABLE:
             log_warning("Google GenAI SDK Types not found. Safety Settings Dialog may be limited or disabled.")


        self.settings_service = settings_service
        self.api_key_service = api_key_service
        self.prompt_service = prompt_service
        self.wildcard_resolver = wildcard_resolver
        self.gemini_handler = gemini_handler
        self.main_window = parent # Reference to MainWindow for calling dialogs

        self._current_api_key_name: Optional[str] = None
        self._current_api_key_value: Optional[str] = None # Store the actual key value when selected
        self._selected_image_paths: List[Path] = []
        self._generation_thread: Optional[QThread] = None
        self._generation_worker: Optional[GenerationWorker] = None
        self._custom_save_path: Optional[Path] = None
        # Store safety settings as the SDK type dict or None
        self._current_safety_settings: Optional[Dict[google_types.HarmCategory, google_types.HarmBlockThreshold]] = None if SDK_TYPES_AVAILABLE else None
        self._is_running = False # Tracks if a worker is currently active
        self._continuous_loop_active = False # Tracks if continuous mode loop is desired

        # --- Persistent Worker Thread for Single Mode ---
        # Create a persistent thread when the widget is initialized.
        # This thread will run the GenerationWorker for each request.
        self._worker_thread = QThread(self)
        self._worker_thread.setObjectName("SingleModeWorkerThread")
        # Start the thread's event loop immediately so it's ready when needed.
        try:
            self._worker_thread.start()
            log_debug("SingleMode: Persistent worker thread started.")
        except Exception as e:
            log_critical(f"SingleMode: Failed to start persistent worker thread: {e}", exc_info=True)
            # Handle this error, maybe disable generation? For now, log and hope for the best.


        # --- Timer for Continuous Loop Scheduling ---
        # Initialize the QTimer here. It will be used to schedule the next
        # generation run after the request_delay in continuous mode.
        self._loop_timer = QTimer(self)
        self._loop_timer.setObjectName("SingleModeLoopTimer")
        self._loop_timer.setSingleShot(True) # Ensure it only fires once per start

        # --- Image/Result Display State ---
        self._full_result_pixmap: Optional[QPixmap] = None # Stores the last generated full image for resizing
        # Initialize _thumbnail_loaded flag. This is needed to differentiate between
        # a displayed result image (not _thumbnail_loaded) and a static thumbnail
        # loaded from an input image (_thumbnail_loaded).
        self._thumbnail_loaded: bool = False


        self._setup_ui()
        self._connect_signals()
        self._load_initial_data()

        # Install event filters for desired widgets to block wheel events
        self.temperature_spin.installEventFilter(self)
        self.top_p_spin.installEventFilter(self)
        self.max_tokens_spin.installEventFilter(self)
        self.api_key_combo.installEventFilter(self)
        self.model_combo.installEventFilter(self)
        self.prompt_combo.installEventFilter(self)
        log_debug("Single Mode: Event filters installed for parameter spin boxes and combo boxes.")

        self.filename_generator = FilenameGeneratorService(self.settings_service)
        self._sequential_mode_enabled: bool = False
        self._sequential_image_queue: List[Path] = []
        self._sequential_current_index: int = -1 # -1 indicates not started or finished
        self._last_resolved_prompt: Optional[str] = None # Stores resolved prompt of LAST successful run
        self._last_image_bytes: Optional[bytes] = None # Stores image bytes of LAST successful run
        self._last_image_mime: Optional[str] = None # Stores image mime of LAST successful run
        self._loaded_prompt_slot_key: Optional[str] = None # Stores the slot key if prompt was loaded from manager


    # Add closeEvent handler for clean thread shutdown
    def closeEvent(self, event):
        log_info("SingleModeWidget received closeEvent. Shutting down worker thread.")
        # Ensure the worker thread is stopped when the widget is closing
        if self._worker_thread and self._worker_thread.isRunning():
            log_debug("SingleModeWidget: Quitting worker thread event loop.")
            self._worker_thread.quit()
            # Wait briefly for the thread to finish
            if not self._worker_thread.wait(1000): # Wait up to 1 second
                 log_warning("SingleModeWidget: Worker thread did not terminate within timeout during close.")
                 # In a critical app shutdown, you might force terminate, but log the issue.
                 # self._worker_thread.terminate()
                 # self._worker_thread.wait(1000)
            else:
                 log_debug("SingleModeWidget: Worker thread terminated cleanly.")
        # Call the base class closeEvent to ensure proper widget destruction
        super().closeEvent(event)


    def _setup_ui(self):
        """Set up the UI elements for Single Mode."""
        self.setObjectName("singleModeWidget") # Name the main widget itself
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setObjectName("mainLayoutSingleMode")
        self.main_layout.setContentsMargins(10, 10, 10, 10)

        # --- Top Controls Group ---
        top_controls_group = QGroupBox("Configuration")
        top_controls_group.setObjectName("configGroupSingleMode")
        top_controls_grid_layout = QGridLayout(top_controls_group)
        top_controls_grid_layout.setObjectName("configGridLayoutSingleMode")

        # Row 0: API Key
        # config_api_key_label = QLabel("API Key:") # Simple labels often don't need names unless highly specific styling is needed
        top_controls_grid_layout.addWidget(QLabel("API Key:"), 0, 0)
        self.api_key_combo = QComboBox()
        self.api_key_combo.setObjectName("apiKeyComboSingleMode")
        self.api_key_combo.setToolTip("Select the API Key to use for this session.")
        top_controls_grid_layout.addWidget(self.api_key_combo, 0, 1, 1, 2) # Span 2 columns
        self.api_key_combo.installEventFilter(self)
        self.manage_keys_button = QPushButton("Manage...")
        self.manage_keys_button.setObjectName("manageKeysButtonSingleMode")
        self.manage_keys_button.setToolTip("Open the API Key Manager.")
        top_controls_grid_layout.addWidget(self.manage_keys_button, 0, 3)

        # Row 1: Model
        # config_model_label = QLabel("Model:")
        top_controls_grid_layout.addWidget(QLabel("Model:"), 1, 0)
        self.model_combo = QComboBox()
        self.model_combo.setObjectName("modelComboSingleMode")
        self.model_combo.setToolTip("Select the Gemini model to use. [IMG] indicates likely image support.")
        self.model_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.model_combo.setEnabled(False) # Initialize as disabled
        top_controls_grid_layout.addWidget(self.model_combo, 1, 1, 1, 2) # Span 2 columns
        self.model_combo.installEventFilter(self)
        self.refresh_models_button = QPushButton("Refresh")
        self.refresh_models_button.setObjectName("refreshModelsButtonSingleMode")
        self.refresh_models_button.setToolTip("Refresh the list of available models (requires valid API key).")
        self.refresh_models_button.setEnabled(False) # Initialize as disabled
        top_controls_grid_layout.addWidget(self.refresh_models_button, 1, 3)

        # Row 2: Model Parameters (Temp, TopP)
        # config_temp_label = QLabel("Temp:")
        top_controls_grid_layout.addWidget(QLabel("Temp:"), 2, 0)
        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setObjectName("temperatureSpinSingleMode")
        self.temperature_spin.setRange(0.0, 2.0)      # Range 0.0 to 2.0 (correct)
        self.temperature_spin.setSingleStep(0.05)
        self.temperature_spin.setDecimals(2)
        self.temperature_spin.setToolTip("Controls randomness (0.0=deterministic, higher=more random).")
        top_controls_grid_layout.addWidget(self.temperature_spin, 2, 1)
        self.temperature_spin.installEventFilter(self)

        # config_top_p_label = QLabel("Top P:")
        top_controls_grid_layout.addWidget(QLabel("Top P:"), 2, 2)
        self.top_p_spin = QDoubleSpinBox()
        self.top_p_spin.setObjectName("topPSpinSingleMode")
        self.top_p_spin.setRange(0.0, 1.0)
        self.top_p_spin.setSingleStep(0.05)
        self.top_p_spin.setDecimals(2)
        self.top_p_spin.setToolTip("Nucleus sampling: Considers tokens until probability sum reaches this value.")
        top_controls_grid_layout.addWidget(self.top_p_spin, 2, 3)
        self.top_p_spin.installEventFilter(self)

        # Row 3: Max Tokens, Reset, Safety
        # config_max_tokens_label = QLabel("Max Tokens:")
        top_controls_grid_layout.addWidget(QLabel("Max Tokens:"), 3, 0)
        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setObjectName("maxTokensSpinSingleMode")
        self.max_tokens_spin.setRange(1, 8192)
        self.max_tokens_spin.setSingleStep(1)
        self.max_tokens_spin.setToolTip("Maximum number of tokens to generate in the response.")
        top_controls_grid_layout.addWidget(self.max_tokens_spin, 3, 1)
        self.max_tokens_spin.installEventFilter(self)

        self.reset_params_button = QPushButton("Reset Params")
        self.reset_params_button.setObjectName("resetParamsButtonSingleMode")
        self.reset_params_button.setToolTip("Reset Temperature, Top P, and Max Tokens to defaults.")
        top_controls_grid_layout.addWidget(self.reset_params_button, 3, 2)

        self.safety_button = QPushButton("Configure Safety...")
        self.safety_button.setObjectName("safetyButtonSingleMode")
        self.safety_button.setToolTip("Configure content safety thresholds (blocks unsafe content).")
        self.safety_button.setEnabled(SDK_TYPES_AVAILABLE)
        if not SDK_TYPES_AVAILABLE:
            self.safety_button.setToolTip("Safety settings unavailable (google-genai library or types missing).")
        top_controls_grid_layout.addWidget(self.safety_button, 3, 3)

        top_controls_grid_layout.setColumnStretch(1, 1)
        top_controls_grid_layout.setColumnStretch(2, 1)
        top_controls_grid_layout.setColumnStretch(3, 0)

        self.main_layout.addWidget(top_controls_group)

        # --- Prompt and Image Area ---
        prompt_image_group = QGroupBox("Input Prompt & Images")
        prompt_image_group.setObjectName("inputGroupSingleMode")
        prompt_image_layout = QVBoxLayout(prompt_image_group)
        prompt_image_layout.setObjectName("inputGroupLayoutSingleMode")

        # Prompt Controls
        prompt_controls_layout = QHBoxLayout()
        prompt_controls_layout.setObjectName("promptControlsLayoutSingleMode")
        # input_load_prompt_label = QLabel("Load Prompt:")
        prompt_controls_layout.addWidget(QLabel("Load Prompt:"))
        self.prompt_combo = QComboBox()
        self.prompt_combo.setObjectName("promptComboSingleMode")
        self.prompt_combo.addItem(" - Enter Prompt Below - ", "")
        self.prompt_combo.setToolTip("Load a saved prompt from the Prompt Manager.")
        self.prompt_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        prompt_controls_layout.addWidget(self.prompt_combo)
        self.prompt_combo.installEventFilter(self)
        self.manage_prompts_button = QPushButton("Manage...")
        self.manage_prompts_button.setObjectName("managePromptsButtonSingleMode")
        self.manage_prompts_button.setToolTip("Open the Prompt Manager.")
        prompt_controls_layout.addWidget(self.manage_prompts_button)
        prompt_image_layout.addLayout(prompt_controls_layout)

        # Prompt Text Input
        self.prompt_text_edit = QPlainTextEdit()
        self.prompt_text_edit.setObjectName("promptTextEditSingleMode")
        self.prompt_text_edit.setPlaceholderText("Enter your prompt here. Use [wildcard] or {wildcard} syntax.")
        self.prompt_text_edit.setMinimumHeight(100)
        prompt_image_layout.addWidget(self.prompt_text_edit, 1)

        # Image Handling
        image_controls_layout = QHBoxLayout()
        image_controls_layout.setObjectName("imageControlsLayoutSingleMode")
        self.add_image_button = QPushButton("Add Image(s)...")
        self.add_image_button.setObjectName("addImageButtonSingleMode")
        self.add_image_button.setToolTip("Select image files to include with the prompt.")
        self.add_image_button.setEnabled(False)
        self.clear_images_button = QPushButton("Clear Images")
        self.clear_images_button.setObjectName("clearImagesButtonSingleMode")
        self.clear_images_button.setToolTip("Remove all selected images.")
        self.clear_images_button.setEnabled(False)
        self.sequential_image_checkbox = QCheckBox("Process Images Sequentially")
        self.sequential_image_checkbox.setObjectName("sequentialImageCheckboxSingleMode")
        self.sequential_image_checkbox.setToolTip("Process selected images one by one with the prompt.")
        self.image_label = QLabel("No image selected (Select model with [IMG] support).")
        self.image_label.setObjectName("imageStatusLabelSingleMode")
        self.image_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        image_controls_layout.addWidget(self.add_image_button)
        image_controls_layout.addWidget(self.clear_images_button)
        image_controls_layout.addWidget(self.sequential_image_checkbox)
        image_controls_layout.addWidget(self.image_label, 1)
        prompt_image_layout.addLayout(image_controls_layout)

        self.main_layout.addWidget(prompt_image_group)

        # --- Action Buttons & Progress ---
        bottom_controls_layout = QVBoxLayout()
        bottom_controls_layout.setObjectName("bottomControlsLayoutSingleMode")

        # --- Row 1: Generate/Cancel/Progress ---
        action_progress_layout = QHBoxLayout()
        action_progress_layout.setObjectName("actionProgressLayoutSingleMode")
        self.generate_button = QPushButton("Generate")
        self.generate_button.setObjectName("generateButtonSingleMode") # Existing name is good
        self.generate_button.setStyleSheet("QPushButton#generateButtonSingleMode { background-color: #aaffaa; font-weight: bold; }") # Example targetting
        self.generate_button.setMinimumHeight(30)
        self.generate_button.setEnabled(False)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("cancelButtonSingleMode")
        self.cancel_button.setMinimumHeight(30)
        self.cancel_button.setEnabled(False)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("progressBarSingleMode")
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0,0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMaximumHeight(15)

        action_progress_layout.addWidget(self.generate_button)
        action_progress_layout.addWidget(self.cancel_button)
        action_progress_layout.addWidget(self.progress_bar, 1)
        bottom_controls_layout.addLayout(action_progress_layout)

        # --- Row 2: Options and Save Path ---
        options_path_layout = QHBoxLayout()
        options_path_layout.setObjectName("optionsPathLayoutSingleMode")

        self.auto_save_checkbox = QCheckBox("Auto-Save")
        self.auto_save_checkbox.setObjectName("autoSaveCheckboxSingleMode")
        self.continuous_checkbox = QCheckBox("Continuous")
        self.continuous_checkbox.setObjectName("continuousCheckboxSingleMode")
        self.continuous_checkbox.setToolTip("Automatically restart generation after completion.")
        self.auto_save_checkbox.setToolTip("Automatically save the generated image and text to the specified path.")
        options_path_layout.addWidget(self.auto_save_checkbox)
        options_path_layout.addWidget(self.continuous_checkbox)
        options_path_layout.addSpacing(20)

        # options_save_path_label = QLabel("Save Path:")
        options_path_layout.addWidget(QLabel("Save Path:"))
        self.save_path_edit = QLineEdit()
        self.save_path_edit.setObjectName("savePathEditSingleMode")
        self.save_path_edit.setPlaceholderText("[Default Output Path]")
        self.save_path_edit.setReadOnly(True)
        self.save_path_edit.setToolTip(f"Custom output directory. Default: {constants.DATA_DIR / 'output'}")
        options_path_layout.addWidget(self.save_path_edit, 1)

        self.browse_save_path_button = QPushButton("...")
        self.browse_save_path_button.setObjectName("browseSavePathButtonSingleMode")
        self.browse_save_path_button.setToolTip("Browse for custom save directory")
        self.browse_save_path_button.setMaximumWidth(30)
        options_path_layout.addWidget(self.browse_save_path_button)

        self.clear_save_path_button = QPushButton("X")
        self.clear_save_path_button.setObjectName("clearSavePathButtonSingleMode")
        self.clear_save_path_button.setToolTip("Clear custom path (use default)")
        self.clear_save_path_button.setMaximumWidth(30)
        self.clear_save_path_button.setEnabled(False)
        options_path_layout.addWidget(self.clear_save_path_button)

        self.open_save_folder_button = QPushButton()
        self.open_save_folder_button.setObjectName("openSaveFolderButtonSingleMode")
        self.open_save_folder_button.setIcon(get_themed_icon("folder_open.png")) # Assuming you have this icon
        self.open_save_folder_button.setIconSize(QSize(16,16))
        self.open_save_folder_button.setToolTip("Open current output folder")
        self.open_save_folder_button.setMaximumWidth(30)
        options_path_layout.addWidget(self.open_save_folder_button)

        bottom_controls_layout.addLayout(options_path_layout)

        self.main_layout.addLayout(bottom_controls_layout)

        # --- Output Area ---
        output_group = QGroupBox("Result")
        output_group.setObjectName("outputGroupSingleMode")
        output_layout = QHBoxLayout(output_group)
        output_layout.setObjectName("outputLayoutSingleMode")

        # Left side: Text result
        self.result_text_edit = QPlainTextEdit()
        self.result_text_edit.setObjectName("resultTextEditSingleMode")
        self.result_text_edit.setReadOnly(True)
        self.result_text_edit.setPlaceholderText("Generated text will appear here.")
        output_layout.addWidget(self.result_text_edit, 2) # Give text more space

        # Right side: Image result and Save button
        image_area_layout = QVBoxLayout()
        image_area_layout.setObjectName(f"imageAreaLayoutSingleMode")
        image_area_layout.setSpacing(5)

        self.result_image_label = QLabel("Generated image\nwill appear here.")
        self.result_image_label.setObjectName("resultImageLabelSingleMode")
        self.result_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_image_label.setMinimumSize(QSize(200, 200)) # Keep minimum size
        self.result_image_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored) # Keep ignored/ignored
        self.result_image_label.setStyleSheet("QLabel#resultImageLabelSingleMode { background-color: #f0f0f0; border: 1px solid grey; color: grey; }")
        self.result_image_label.setScaledContents(False) # Keep false for manual scaling
        image_area_layout.addWidget(self.result_image_label, 1) # Image label takes vertical stretch

        self.save_prompt_button = QPushButton("Save Prompt && Thumb")
        self.save_prompt_button.setObjectName("savePromptButtonSingleMode")
        self.save_prompt_button.setToolTip("Save the last generated prompt and image thumbnail to Prompt Manager.")
        self.save_prompt_button.setVisible(False) # Hidden initially
        # Optional: Add Icon
        # self.save_prompt_button.setIcon(get_themed_icon("save.png"))
        image_area_layout.addWidget(self.save_prompt_button)

        output_layout.addLayout(image_area_layout, 1) # Add image area layout, give less space than text

        self.main_layout.addWidget(output_group, 1) # Give output group vertical stretch



    def _connect_signals(self):
        """Connect internal signals."""
        self.api_key_combo.currentIndexChanged.connect(self._on_api_key_selected)
        self.model_combo.currentIndexChanged.connect(self._on_model_selected)
        self.refresh_models_button.clicked.connect(self._refresh_models)
        self.manage_keys_button.clicked.connect(self._open_api_keys_external)
        self.manage_prompts_button.clicked.connect(self._open_prompts_external)
        if SDK_AVAILABLE:
            self.safety_button.clicked.connect(self._configure_safety)
        self.browse_save_path_button.clicked.connect(self._browse_save_path)
        self.clear_save_path_button.clicked.connect(self._clear_save_path)
        self.prompt_combo.currentIndexChanged.connect(self._load_selected_prompt)
        self.add_image_button.clicked.connect(self._add_images)
        self.clear_images_button.clicked.connect(self._clear_images)
        self.auto_save_checkbox.toggled.connect(self._set_internal_autosave)
        # Connect the continuous checkbox to update the internal flag and button state
        self.continuous_checkbox.toggled.connect(self._set_internal_continuous)
        # Connect the loop timer's timeout signal to the method that handles scheduling the next run
        self._loop_timer.timeout.connect(self._try_start_next_generation)


        self.generate_button.clicked.connect(self._handle_generate_button_click)
        # Connect the cancel button to the cancellation method
        self.cancel_button.clicked.connect(self._cancel_generation)

        self.reset_params_button.clicked.connect(self._reset_parameters)
        self.sequential_image_checkbox.toggled.connect(self._on_sequential_mode_toggled)
        self.save_prompt_button.clicked.connect(self._save_current_prompt)
        self.open_save_folder_button.clicked.connect(self._open_output_folder)
        
    def _load_initial_data(self):
        """Load API keys, prompts, models, and settings."""
        log_debug("SingleMode: Loading initial data...")
        self.update_api_key_list() # This will trigger _on_api_key_selected if a key is selected
        self.update_prompt_list()
        self.auto_save_checkbox.setChecked(self.settings_service.get_setting("auto_save_enabled", constants.DEFAULT_AUTO_SAVE_ENABLED))
        self.temperature_spin.setValue(self.settings_service.get_setting("default_temperature", constants.DEFAULT_TEMPERATURE))
        self.top_p_spin.setValue(self.settings_service.get_setting("default_top_p", constants.DEFAULT_TOP_P))
        self.max_tokens_spin.setValue(self.settings_service.get_setting("default_max_tokens", constants.DEFAULT_MAX_OUTPUT_TOKENS))
        # Load safety settings from settings service (deserialized)
        self._current_safety_settings = self.settings_service.get_setting("single_mode_safety_settings", None)
        log_info(f"Loaded initial safety settings: {self._current_safety_settings}")
        self.continuous_checkbox.setChecked(False)
        # Initial state: disable buttons until a valid key is selected and validated
        self.model_combo.setEnabled(False)
        self.generate_button.setEnabled(False)
        self.refresh_models_button.setEnabled(False)
        self.add_image_button.setEnabled(False)
        self.clear_images_button.setEnabled(False)
 
        
        saved_path_str = self.settings_service.get_setting("single_mode_custom_save_path")
        if saved_path_str:
            saved_path = Path(saved_path_str)
            if saved_path.is_dir(): # Check if the saved path is still valid
                self._custom_save_path = saved_path
                self.save_path_edit.setText(str(saved_path))
                self.clear_save_path_button.setEnabled(True)
                log_debug(f"Single Mode: Loaded custom save path: {saved_path}")
            else:
                log_warning(f"Single Mode: Loaded custom save path '{saved_path_str}' is invalid. Clearing setting.")
                self._custom_save_path = None
                self.settings_service.set_setting("single_mode_custom_save_path", None) # Clear invalid setting
                self.save_path_edit.clear()
                self.save_path_edit.setPlaceholderText("[Default Output Path]")
                self.clear_save_path_button.setEnabled(False)
        else:
            # Ensure UI is in default state if no path was saved
            self._custom_save_path = None
            self.save_path_edit.clear()
            self.save_path_edit.setPlaceholderText("[Default Output Path]")
            self.clear_save_path_button.setEnabled(False)

    @pyqtSlot()
    def _reset_parameters(self):
        """Resets Temperature, Top P, and Max Tokens to their default values."""
        log_info("Resetting generation parameters to defaults.")
        self.temperature_spin.setValue(constants.DEFAULT_TEMPERATURE)
        self.top_p_spin.setValue(constants.DEFAULT_TOP_P)
        self.max_tokens_spin.setValue(constants.DEFAULT_MAX_OUTPUT_TOKENS)
        self.status_update.emit("Parameters reset to defaults.", 3000)    

    def eventFilter(self, source: QObject, event: QEvent) -> bool:
        """Filters out Wheel events over specific child widgets."""
        if event.type() == QEvent.Type.Wheel:
            widgets_to_block = [
                self.temperature_spin,
                self.top_p_spin,
                self.max_tokens_spin,
                self.api_key_combo,
                self.model_combo,
                self.prompt_combo # <<< ADD THIS LINE
            ]
            if source in widgets_to_block:
                log_debug(f"Single Mode: Ignoring wheel event on {source.objectName() or source.__class__.__name__}")
                event.accept()
                return True

        return super().eventFilter(source, event)
        
    
    
    @pyqtSlot()
    def _open_output_folder(self):
        """Opens the current output directory in the system's file explorer."""
        instance_log_prefix = "SingleMode" # For logging consistency
        try:
            # Determine the correct path
            if self._custom_save_path and self._custom_save_path.is_dir():
                path_to_open = self._custom_save_path
                log_info(f"{instance_log_prefix}: Opening custom output folder: {path_to_open}")
            else:
                path_to_open = constants.DATA_DIR / "output"
                log_info(f"{instance_log_prefix}: Opening default output folder: {path_to_open}")
                if self._custom_save_path:
                    log_warning(f"{instance_log_prefix}: Custom path '{self._custom_save_path}' was set but invalid, opening default.")

            # Ensure the directory exists before trying to open it
            path_to_open.mkdir(parents=True, exist_ok=True)
            log_debug(f"{instance_log_prefix}: Ensured directory exists: {path_to_open}")

            # Open the directory using webbrowser for cross-platform compatibility
            # Convert path to URI for webbrowser
            folder_uri = path_to_open.absolute().as_uri()
            log_debug(f"{instance_log_prefix}: Attempting to open URI: {folder_uri}")
            success = webbrowser.open(folder_uri)

            if not success:
                # Fallback for some systems if webbrowser fails
                log_warning(f"{instance_log_prefix}: webbrowser.open failed, attempting system-specific fallback.")
                try:
                    if platform.system() == "Windows":
                        os.startfile(str(path_to_open.absolute()))
                    elif platform.system() == "Darwin": # macOS
                        subprocess.Popen(["open", str(path_to_open.absolute())])
                    else: # Linux/other Unix-like
                        subprocess.Popen(["xdg-open", str(path_to_open.absolute())])
                except Exception as fallback_e:
                     log_error(f"{instance_log_prefix}: Fallback method also failed to open folder {path_to_open}: {fallback_e}", exc_info=True)
                     show_error_message(self, "Error Opening Folder", f"Could not open the folder:\n{path_to_open}\n\nReason: {fallback_e}")
            else:
                 log_info(f"{instance_log_prefix}: Successfully requested to open folder {path_to_open}.")

        except OSError as e:
            log_error(f"{instance_log_prefix}: Error ensuring/creating directory {path_to_open}: {e}", exc_info=True)
            show_error_message(self, "Directory Error", f"Could not create or access the directory:\n{path_to_open}\n\nReason: {e}")
        except Exception as e:
            log_error(f"{instance_log_prefix}: Unexpected error opening output folder: {e}", exc_info=True)
            show_error_message(self, "Error", f"An unexpected error occurred while trying to open the output folder.")    
    
        
    @pyqtSlot(bool)
    def _set_internal_autosave(self, checked):
        """Sets the internal flag for auto-save mode."""

        log_debug(f"Single Mode: Auto-Save set to {checked}")
 
    @pyqtSlot(bool)
    def _set_internal_continuous(self, checked):
        """Sets the internal flag for continuous mode."""
        self._continuous_mode = checked # Assumes you have self._continuous_mode variable
        log_debug(f"Single Mode: Continuous mode set to {checked}")
    
    # --- Public Methods ---
    
    
    
    def shutdown_mode(self, is_closing=False) -> bool:
        """Prepares the widget for mode switching or app closing."""
        log_info(f"SingleMode shutdown requested (is_closing={is_closing}).")
        # Check internal running flag, which is updated by the worker's finished signal
        if self._is_running or self._continuous_loop_active:
            log_warning("Generation is in progress or continuous loop is active. Cannot shutdown/switch mode now.")

            if is_closing:
                log_info("Attempting to cancel generation for application shutdown...")
                self._continuous_loop_active = False # Explicitly stop loop
                self._cancel_generation() # Request worker cancellation

                # Now, wait for the worker thread to finish ONLY if we are closing the application
                if self._generation_thread and self._generation_thread.isRunning():
                    log_debug("Waiting for generation thread to finish...")
                    # Use a timeout to prevent hanging indefinitely
                    if not self._generation_thread.wait(5000): # Wait up to 5 seconds
                        log_error("Generation thread did not stop within timeout during shutdown.")
                        # In a critical shutdown path, we might proceed anyway, but log the issue.
                        # Returning False might prevent close, which is safer if the thread is stuck.
                        show_error_message(self.parent(), "Shutdown Warning",
                                           "Background generation thread did not stop promptly. Application might become unstable.")
                        return False # Prevent closing immediately

                # If we reached here, the thread should be finished or wasn't running
                log_info("Generation thread finished or was not running during shutdown.")
                # Ensure UI state is idle after thread finishes
                self._set_ui_generating(False) # This updates buttons etc.
                self.status_update.emit("Ready.", 3000)
                return True # OK to close

            else:
                 # Just switching modes, cannot force close/cancel without confirmation
                 show_error_message(self.parent(), "Operation Active",
                                    "Generation is currently running. Please cancel it before switching modes.")
                 return False # Prevent switch

        # If not running or looping, it's always safe to switch modes or shutdown
        log_info("SingleMode shutdown: No active generation. State clean.")
        return True    
    
    
    
    def activate_mode(self):
        """Called when this mode becomes active."""
        log_info("SingleMode activated.")
        # Re-check API key status and model list if needed (e.g., if key was deleted while inactive)
        self._revalidate_api_key_and_load_models()
        # Apply current settings that might affect the UI look or behavior
        self.auto_save_checkbox.setChecked(self.settings_service.get_setting("auto_save_enabled", constants.DEFAULT_AUTO_SAVE_ENABLED))
        self._current_safety_settings = self.settings_service.get_setting("single_mode_safety_settings", None)
        self.status_update.emit("Single-API Mode Activated.", 3000)

    def update_api_key_list(self):
        """Refreshes the API key dropdown."""
        log_debug("SingleMode: Updating API key list.")
        current_key_name = self.api_key_combo.currentData()
        last_used_key_name = self.settings_service.get_setting("last_used_api_key_name")

        self.api_key_combo.blockSignals(True)
        self.api_key_combo.clear()
        # Use a more user-friendly placeholder
        self.api_key_combo.addItem("--- Select API Key ---", None)

        key_names = self.api_key_service.get_key_names()
        found_current = False
        found_last_used = False
        current_index_to_select = 0

        for i, name in enumerate(key_names):
            self.api_key_combo.addItem(name, name)
            if name == current_key_name:
                current_index_to_select = i + 1
                found_current = True
            if name == last_used_key_name:
                found_last_used = True

        if found_current:
            log_debug(f"Restoring current API key selection: {current_key_name}")
            self.api_key_combo.setCurrentIndex(current_index_to_select)
        elif found_last_used:
            index = self.api_key_combo.findData(last_used_key_name)
            if index != -1:
                log_debug(f"Restoring last used API key selection: {last_used_key_name}")
                self.api_key_combo.setCurrentIndex(index)
            else: # Last used key was deleted
                 log_debug(f"Last used key '{last_used_key_name}' not found.")
                 self.api_key_combo.setCurrentIndex(0)
        else:
            log_debug("No current or last used key found, selecting placeholder.")
            self.api_key_combo.setCurrentIndex(0)

        self.api_key_combo.blockSignals(False)
        # Manually trigger the handler for the (potentially) newly selected index
        # Use QTimer to ensure it runs after the current event loop processing
        QTimer.singleShot(0, lambda idx=self.api_key_combo.currentIndex(): self._on_api_key_selected(idx))



    @pyqtSlot()
    def _attempt_next_api_key_and_retry(self):
        """
        Attempts to find the next available and *valid* API key and retries generation.
        Validates the new key before scheduling the retry.
        """
        instance_log_prefix = "SingleMode"
        log_info(f"{instance_log_prefix}: Rate limit hit. Attempting to switch API key and retry.")

        current_key_name = self._current_api_key_name
        if not current_key_name:
            log_error(f"{instance_log_prefix}: Cannot switch key: No current key name stored.")
            show_error_message(self, "Key Switch Error", "Cannot determine the current API key to switch from.")
            self._continuous_loop_active = False
            self._set_ui_generating(False)
            self.status_update.emit("Key Error.", 5000)
            return

        all_keys = self.api_key_service.get_key_names()
        potential_next_keys = [k for k in all_keys if k != current_key_name]

        if not potential_next_keys:
            log_warning(f"{instance_log_prefix}: Rate limit hit on '{current_key_name}', but no other API keys available.")
            show_warning_message(self, "Rate Limit", f"API Key '{current_key_name}' hit rate limit. No other keys available.")
            self._continuous_loop_active = False
            self._set_ui_generating(False)
            self.generate_button.setEnabled(False)
            self.generate_button.setToolTip(f"Rate limit hit on key '{current_key_name}'. No other keys.")
            self.status_update.emit(f"Rate limit (No other keys).", 0)
            return

        found_valid_key = False
        for next_key_name in potential_next_keys:
            log_debug(f"{instance_log_prefix}: Trying next potential key: '{next_key_name}'")
            next_key_value = self.api_key_service.get_key_value(next_key_name)

            if not next_key_value:
                log_warning(f"{instance_log_prefix}: Could not retrieve value for key '{next_key_name}'. Skipping.")
                continue

            # --- Explicitly Validate Client for the NEW Key ---
            log_debug(f"{instance_log_prefix}: Validating client for potential key '{next_key_name}'...")
            new_client = self.gemini_handler.get_or_initialize_client(next_key_name, next_key_value)

            if new_client:
                log_info(f"{instance_log_prefix}: Switching from rate-limited key '{current_key_name}' to VALID key '{next_key_name}'.")
                found_valid_key = True

                # --- Update internal state ---
                self._current_api_key_name = next_key_name
                self._current_api_key_value = next_key_value
                self.settings_service.set_setting("last_used_api_key_name", self._current_api_key_name)

                # --- Update UI ComboBox selection ---
                combo_index_to_select = self.api_key_combo.findData(next_key_name)
                if combo_index_to_select != -1:
                    self.api_key_combo.blockSignals(True)
                    self.api_key_combo.setCurrentIndex(combo_index_to_select)
                    self.api_key_combo.blockSignals(False)
                    log_debug(f"{instance_log_prefix}: UI ComboBox updated.")
                else:
                    log_warning(f"{instance_log_prefix}: Could not find newly validated key '{next_key_name}' in combo box.")

                # --- Refresh models silently if needed (optional optimization, check if needed) ---
                # self._load_models(force_refresh=False) # May trigger UI updates

                self.status_update.emit(f"Switched to key '{next_key_name}'. Retrying...", 0)
                QApplication.processEvents()

                # --- Schedule the generation retry ---
                if self._continuous_loop_active:
                    retry_delay_ms = self.settings_service.get_setting("request_delay", constants.DEFAULT_REQUEST_DELAY) * 1000
                    log_info(f"{instance_log_prefix}: Scheduling generation retry with new key in {retry_delay_ms}ms.")
                    # Use the persistent timer if available, otherwise QTimer.singleShot
                    if hasattr(self, '_loop_timer') and self._loop_timer:
                         # Schedule the check/start method using the loop timer
                         self._loop_timer.start(max(500, retry_delay_ms))
                    else:
                         # Fallback if loop timer isn't set up (shouldn't happen)
                         QTimer.singleShot(max(500, retry_delay_ms), self.start_generation)
                else:
                    log_info(f"{instance_log_prefix}: Loop stopped during key switch. Not retrying.")
                    self._set_ui_generating(False)
                    self.status_update.emit("Ready.", 3000)

                break # Exit the loop once a valid key is found and processed
            else:
                log_warning(f"{instance_log_prefix}: Validation failed for key '{next_key_name}'. Trying next...")
                # Explicitly shut down the potentially invalid client in the handler?
                # self.gemini_handler.shutdown_client(next_key_name) # Optional: Ensure bad client removed

        # --- Handle case where no valid alternative key was found ---
        if not found_valid_key:
            log_error(f"{instance_log_prefix}: Rate limit hit on '{current_key_name}', and no other *valid* API keys could be found or validated.")
            show_warning_message(self, "Rate Limit", f"Key '{current_key_name}' hit rate limit. No other valid keys found.")
            self._continuous_loop_active = False
            self._set_ui_generating(False)
            self.generate_button.setEnabled(False)
            self.generate_button.setToolTip(f"Rate limit hit. No other valid keys.")
            self.status_update.emit(f"Rate limit (No other valid keys).", 0)



    @pyqtSlot()
    def _browse_save_path(self):
        """Opens a dialog to select a custom save directory."""
        start_dir = str(self._custom_save_path) if self._custom_save_path else str(constants.DATA_DIR / "output")

        dir_path_str = QFileDialog.getExistingDirectory(
            self,
            f"Select Custom Save Directory - Single Mode",
            start_dir
        )

        if dir_path_str:
            selected_path = Path(dir_path_str)
            self._custom_save_path = selected_path
            self.save_path_edit.setText(str(selected_path))
            self.clear_save_path_button.setEnabled(True)
            # Save setting persistently
            self.settings_service.set_setting("single_mode_custom_save_path", str(selected_path))
            log_info(f"Single Mode: Custom save path set to: {selected_path}")
        else:
            log_debug(f"Single Mode: Custom save path selection cancelled.")

    @pyqtSlot()
    def _clear_save_path(self):
        """Clears the custom save path, reverting to default."""
        if self._custom_save_path:
            self._custom_save_path = None
            self.save_path_edit.clear()
            self.save_path_edit.setPlaceholderText("[Default Output Path]")
            self.clear_save_path_button.setEnabled(False)
            # Clear persistent setting
            self.settings_service.set_setting("single_mode_custom_save_path", None)
            log_info(f"Single Mode: Custom save path cleared. Using default.")



    @pyqtSlot(bool)
    def _on_sequential_mode_toggled(self, checked):
        if self._is_running:
             log_warning("Cannot change sequential mode while generation is active.")
             # Prevent change by reverting the checkbox state
             self.sequential_image_checkbox.setChecked(not checked)
             return

        log_info(f"{self.__class__.__name__} {self.instance_id if hasattr(self, 'instance_id') else ''}: Sequential mode {'enabled' if checked else 'disabled'}.")
        self._sequential_mode_enabled = checked

        # Clear both lists and reset index to avoid confusion when switching modes
        self._selected_image_paths = []
        self._sequential_image_queue = []
        self._sequential_current_index = -1

        # Update UI elements
        self._update_image_label()
        add_tooltip = "Add images to the sequence queue." if checked else "Select image files to include with the prompt."
        clear_tooltip = "Clear the sequence queue." if checked else "Remove all selected images."
        self.add_image_button.setToolTip(add_tooltip)
        self.clear_images_button.setToolTip(clear_tooltip)
        # Re-evaluate if add/clear buttons should be enabled based on model support
        self._on_model_selected(self.model_combo.currentIndex())



    def update_prompt_list(self):
        """Refreshes the prompt dropdown, now including thumbnails."""
        log_debug("SingleMode: Updating prompt list with thumbnails.")
        current_selection_data = self.prompt_combo.currentData()
        self.prompt_combo.blockSignals(True)
        self.prompt_combo.clear()
        self.prompt_combo.addItem(" - Enter Prompt Below - ", "") # Placeholder
        self.prompt_combo.setIconSize(QSize(128, 128)) # Set desired icon size

        # Get full prompt data including thumbnail paths
        prompts_data = self.prompt_service.get_all_prompts_full()
        # Sort slots numerically for consistent order
        sorted_slots = sorted(prompts_data.keys(), key=lambda k: int(k.split('_')[-1]) if k.startswith("slot_") else float('inf'))

        item_found = False
        index_to_select = 0 # Default to placeholder

        for i, slot_key in enumerate(sorted_slots):
            data = prompts_data[slot_key]
            name = data.get("name", "Unnamed")
            relative_thumb_path = data.get("thumbnail_path") # Get the relative path

            display_text = f"{name} ({slot_key})" # Text for the item

            # --- Load Icon ---
            icon = QIcon() # Default empty icon
            if relative_thumb_path:
                try:
                    # Construct full path using constants
                    full_thumb_path = constants.PROMPTS_ASSETS_DIR / relative_thumb_path
                    if full_thumb_path.is_file():
                        icon = QIcon(str(full_thumb_path))
                    else:
                        log_warning(f"SingleMode: Thumbnail file not found for {slot_key}: {full_thumb_path}")
                except Exception as e:
                     log_error(f"SingleMode: Error creating QIcon for {slot_key} from path {relative_thumb_path}: {e}")

            # --- Add item and set icon ---
            self.prompt_combo.addItem(display_text, slot_key) # Add text and data first
            item_index = self.prompt_combo.count() - 1      # Get index of the item just added
            if not icon.isNull():
                self.prompt_combo.setItemIcon(item_index, icon) # Set icon for that index

            # Check if this is the item we need to re-select
            if slot_key == current_selection_data:
                index_to_select = item_index # Use the actual item index
                item_found = True

        self.prompt_combo.setCurrentIndex(index_to_select) # Restore selection
        self.prompt_combo.blockSignals(False)


    def on_settings_changed(self):
        """Called when global settings are updated."""
        log_debug("SingleMode: Reacting to settings change.")
        self.auto_save_checkbox.setChecked(self.settings_service.get_setting("auto_save_enabled", constants.DEFAULT_AUTO_SAVE_ENABLED))
        # Reload safety settings
        self._current_safety_settings = self.settings_service.get_setting("single_mode_safety_settings", None)
        log_info(f"Reloaded safety settings on settings changed: {self._current_safety_settings}")

    def load_prompt_from_meta(self, prompt_text: str, target: str):
        """Loads prompt text received from metadata viewer."""
        if target == 'current' or target.startswith('single'):
            log_info("Loading prompt from image metadata into Single Mode.")
            self.prompt_text_edit.setPlainText(prompt_text)
            self.prompt_combo.setCurrentIndex(0)
            self.status_update.emit("Prompt loaded from image metadata.", 3000)
        else:
            log_debug(f"Ignoring prompt load, target '{target}' is not for Single Mode.")


    # --- Internal Slots and Methods ---
    def _open_api_keys_external(self):
        """Triggers the API key dialog via the main window."""
        if self.main_window:
            self.main_window._open_api_keys()
        else:
            log_error("Cannot open API key manager, main window reference missing.")

    def _open_prompts_external(self):
        """Triggers the Prompt Manager dialog via the main window."""
        if self.main_window:
            self.main_window._open_prompts()
        else:
            log_error("Cannot open prompt manager, main window reference missing.")

    def _revalidate_api_key_and_load_models(self):
        """Checks the current key and loads models if valid."""
        log_debug("Revalidating current API key selection.")
        self._on_api_key_selected(self.api_key_combo.currentIndex())






    @pyqtSlot(int)
    def _on_api_key_selected(self, index):
        """Handles API key selection change."""
        selected_key_name = self.api_key_combo.itemData(index)
        log_debug(f"SingleMode: API Key selection changed to index {index}, name: {selected_key_name}")

        # --- Reset state if placeholder selected ---
        if selected_key_name is None:
            self._current_api_key_name = None
            self._current_api_key_value = None
            self.model_combo.clear()
            self.model_combo.setEnabled(False)
            self.generate_button.setEnabled(False)
            self.refresh_models_button.setEnabled(False)
            self.add_image_button.setEnabled(False)
            self.clear_images_button.setEnabled(False)
            # Reset image related state on key clear
            self._selected_image_paths = []
            self._sequential_image_queue = []
            self._sequential_current_index = -1
            self._update_image_label() # Update label to reflect cleared state
            self.status_update.emit("Please select an API key.", 0)
            log_info("API key placeholder selected. State reset.")
            return

        # --- Avoid redundant processing if same key is selected ---
        # Check if the selected key is the same as the one already active in this instance
        if selected_key_name == self._current_api_key_name:
             log_debug(f"SingleMode: Same key '{selected_key_name}' re-selected.")
             # Even if the same key is selected, ensure the UI state is correctly reflecting
             # whether models are loaded and available.
             is_client_ready = self.gemini_handler.is_client_available(selected_key_name)
             models_cached = selected_key_name in self.gemini_handler.available_models_cache
             models_loaded = models_cached and bool(self.gemini_handler.available_models_cache[selected_key_name])

             # Ensure UI buttons reflect this state
             self.model_combo.setEnabled(models_loaded)
             self.refresh_models_button.setEnabled(is_client_ready) # Can refresh if client is ready
             self.generate_button.setEnabled(models_loaded) # Can generate only if models loaded
             # Re-check image support based on current model selection (important!)
             self._on_model_selected(self.model_combo.currentIndex()) # This handles add/clear image button states
             self.status_update.emit("Ready.", 3000) # Assume Ready if client validated and models cached
             return # Stop here, no need to refetch models

        # --- Process NEW key selection ---
        key_value = self.api_key_service.get_key_value(selected_key_name)

        if not key_value:
            show_error_message(self, "API Key Error", f"Could not retrieve or decrypt API key '{selected_key_name}'.")
            self.status_update.emit("Failed to retrieve API key.", 5000)
            self.api_key_combo.setCurrentIndex(0) # Reset to placeholder
            # Explicitly call handler for placeholder index using QTimer
            QTimer.singleShot(0, lambda: self._on_api_key_selected(0))
            return # Stop processing

        self.status_update.emit(f"Validating API key '{selected_key_name}'...", 0)
        # Disable buttons while validating or loading models
        self.model_combo.clear() # Clear existing models immediately
        self.model_combo.setEnabled(False)
        self.generate_button.setEnabled(False)
        self.refresh_models_button.setEnabled(False)
        self.add_image_button.setEnabled(False)
        self.clear_images_button.setEnabled(False)
        # Reset image related state on key change
        self._selected_image_paths = []
        self._sequential_image_queue = []
        self._sequential_current_index = -1
        self._update_image_label()
        QApplication.processEvents() # Allow UI to update

        # Attempt to get/initialize the client for this key
        client_instance = self.gemini_handler.get_or_initialize_client(selected_key_name, key_value)

        if client_instance:
            log_info(f"SingleMode: Client for key '{selected_key_name}' validated/retrieved.")
            # --- Store the new active key name and value for this instance ---
            self._current_api_key_name = selected_key_name
            self._current_api_key_value = key_value
            # Save the new key name as the last used API key in settings
            self.settings_service.set_setting("last_used_api_key_name", self._current_api_key_name)

            # Enable refresh models button now that we have a valid client instance
            self.refresh_models_button.setEnabled(True)
            self.status_update.emit(f"API key '{selected_key_name}' active.", 0) # Status update after validation

            # --- Check Model Cache BEFORE loading models ---
            cached_models = self.gemini_handler.available_models_cache.get(selected_key_name)

            if cached_models:
                # Cache Hit: Models are already available for this key
                log_info(f"SingleMode: Using cached models for key: {selected_key_name}")
                # Populate the UI dropdown directly from the cached list
                self._populate_model_combo(cached_models)
                # Update status to indicate readiness *after* populating models
                if self.model_combo.count() > 0 and self.model_combo.isEnabled():
                    self.status_update.emit("Ready.", 3000) # Set Ready status only if models were successfully populated
            else:
                # Cache Miss: Models need to be fetched for this key
                log_info(f"SingleMode: No cached models found for key '{selected_key_name}'. Fetching models...")
                # Call _load_models to fetch, cache, and populate the UI.
                # Pass force_refresh=False as it's the initial load for this key.
                # _load_models will handle status updates related to fetching.
                self._load_models(force_refresh=False)

        else:
            # Initialization failed (error logged within get_or_initialize_client)
            # Reset state to placeholder
            show_error_message(self, "API Key Error", f"Failed to initialize or validate API key '{selected_key_name}'. Check the key and API access.")
            self.status_update.emit("API Key validation failed.", 5000)
            self.api_key_combo.setCurrentIndex(0) # Reset to placeholder
            # Explicitly call handler for placeholder index using QTimer to ensure state is fully reset
            QTimer.singleShot(0, lambda: self._on_api_key_selected(0))




    def _populate_model_combo(self, models: List[Dict[str, Any]]):
        """Populates the model combo box from a list of model dicts."""
        self.model_combo.blockSignals(True)
        self.model_combo.clear()

        if models:
            log_debug(f"Populating model combo with {len(models)} models.")
            for model_info in models:
                display = model_info.get('display_name', model_info['name'])
                name = model_info['name']
                support_indicator = " [IMG]" if model_info.get('likely_image_support') else ""
                self.model_combo.addItem(f"{display}{support_indicator}", name)

            self.model_combo.setEnabled(True)

            # Select default/last used or first
            default_model = self.settings_service.get_setting("default_model", constants.DEFAULT_MODEL_NAME)
            index = self.model_combo.findData(default_model)
            if index != -1:
                self.model_combo.setCurrentIndex(index)
                log_debug(f"Set model to default/last used: {default_model}")
            elif self.model_combo.count() > 0:
                self.model_combo.setCurrentIndex(0)
                log_debug(f"Default model '{default_model}' not found, selecting first available: {self.model_combo.itemData(0)}")

        else:
            # Handle case where empty list is passed (e.g., cache was empty, fetch failed)
            log_error("Model list provided to _populate_model_combo was empty.")
            self.model_combo.addItem("No models found")
            self.model_combo.setEnabled(False)

        # Update generate button state based on whether models were populated
        is_ready = self.model_combo.count() > 0 and self.model_combo.isEnabled() and bool(self._current_api_key_name)
        self.generate_button.setEnabled(is_ready)
        self.model_combo.blockSignals(False)

        # Manually trigger model selected handler to update image buttons etc.
        self._on_model_selected(self.model_combo.currentIndex()) # Ensure this runs


    @pyqtSlot()
    def _refresh_models(self):
        """Forces a refresh of the model list for the current API key."""
        # Use the currently stored key name and value
        if not self._current_api_key_name or not self._current_api_key_value:
            show_info_message(self, "Refresh Models", "Please select and validate an API key first.")
            return

        # Check if client is actually available for this key
        if not self.gemini_handler.is_client_available(self._current_api_key_name):
            show_error_message(self, "Client Error", f"Client for key '{self._current_api_key_name}' is not available. Try selecting the key again.")
            return

        log_info(f"Force refreshing model list for key: {self._current_api_key_name}...")
        self.status_update.emit("Refreshing model list...", 0)
        self.model_combo.setEnabled(False)
        self.generate_button.setEnabled(False)
        QApplication.processEvents()
        self._load_models(force_refresh=True)
        self.status_update.emit("Model list refreshed.", 3000)

    def _load_models(self, force_refresh=False):
        """
        Loads available models into the dropdown for the current key by calling
        the GeminiHandler and then populating the UI via _populate_model_combo.
        """
        # Use the currently stored key name and value for this instance
        api_key_name = self._current_api_key_name
        api_key_value = self._current_api_key_value

        if not api_key_name or not api_key_value:
            log_warning("Cannot load models, API key info missing.")
            self.model_combo.clear()
            self.model_combo.setEnabled(False)
            self.generate_button.setEnabled(False)
            self.status_update.emit("Select API Key to load models.", 0) # More informative message
            return

        # Set status message before potentially slow API call
        self.status_update.emit(f"Loading models for '{api_key_name}'...", 0)
        QApplication.processEvents() # Ensure UI updates

        # Fetch models using the handler.
        # The handler will use its cache unless force_refresh is True.
        models = self.gemini_handler.list_models(
            api_key_name=api_key_name,
            api_key_value=api_key_value,
            force_refresh=force_refresh
        )

        # Use the helper method to populate the UI combo box with the results
        # This method will also handle enabling/disabling the generate button.
        self._populate_model_combo(models)

        # Update status based on whether models were successfully loaded and populated
        if self.model_combo.count() > 0 and self.model_combo.isEnabled():
             # Models were successfully loaded (either from cache or API)
             if force_refresh:
                  self.status_update.emit("Model list refreshed.", 3000)
             else:
                  # If not forcing refresh, it means we fetched them now because they weren't cached
                  self.status_update.emit("Models loaded.", 3000)
                  # Optionally reset to "Ready." after a delay if you prefer that over "Models loaded."
                  QTimer.singleShot(4000, lambda: self.status_update.emit("Ready.", 3000) if not self._is_running else None)

        else:
             # _populate_model_combo handles adding error message to combo box
             self.status_update.emit("Failed to load models.", 5000)

    @pyqtSlot(int)
    def _on_model_selected(self, index):
        """Handles model selection change."""
        model_name = self.model_combo.itemData(index)
        if not model_name:
            log_debug("Model selection invalid or cleared.")
            self.add_image_button.setEnabled(False)
            # Keep clear button enabled only if images are selected
            self.clear_images_button.setEnabled(bool(self._selected_image_paths))
            # Generate button should only be enabled if both key AND model are valid
            self.generate_button.setEnabled(False)
            return

        log_info(f"Model selected: {model_name} ({self.model_combo.currentText()})")
        # Enable generate button now that a model is selected (assuming key is also valid)
        self.generate_button.setEnabled(bool(self._current_api_key_name))

        # Use the cache associated with the *current* API key
        models_for_current_key = self.gemini_handler.available_models_cache.get(self._current_api_key_name, [])
        model_info = next((m for m in models_for_current_key if m['name'] == model_name), None)
        supports_images = model_info.get('likely_image_support', False) if model_info else False

        self.add_image_button.setEnabled(supports_images)
        self._update_image_label() # Updates label and clear button state

        if not supports_images and self._selected_image_paths:
            log_warning(f"Model '{model_name}' likely doesn't support images, but images are selected. Clearing images.")
            self._clear_images() # This also calls _update_image_label

        if self.generate_button.isEnabled():
            self.status_update.emit(f"Selected model: {self.model_combo.currentText()}", 3000)
        else:
            self.status_update.emit("Select API Key and Model.", 0)



    @pyqtSlot(int)
    def _load_selected_prompt(self, index):
        """Loads the selected prompt text into the editor and remembers the slot key."""
        slot_key = self.prompt_combo.itemData(index)

        if slot_key:
            prompt_data = self.prompt_service.get_prompt(slot_key) # Get full data
            if prompt_data and "text" in prompt_data:
                prompt_text = prompt_data["text"]
                self.prompt_text_edit.setPlainText(prompt_text)
                self._loaded_prompt_slot_key = slot_key # <-- Store the loaded slot key
                log_info(f"Loaded prompt from slot: {slot_key}")
                self.status_update.emit(f"Loaded prompt: {self.prompt_combo.currentText()}", 3000)
            else:
                log_error(f"Could not retrieve text for prompt slot: {slot_key}")
                self.prompt_combo.setCurrentIndex(0) # Reset to placeholder
                self.prompt_text_edit.clear()
                self._loaded_prompt_slot_key = None # <-- Reset if load fails
        else:
            log_debug("Prompt placeholder selected.")
            # Clear the text edit and reset the loaded slot key when placeholder is chosen
            self.prompt_text_edit.clear()
            self._loaded_prompt_slot_key = None # <-- Reset the loaded slot key
            self.status_update.emit("Prompt text editor cleared.", 2000)


    @pyqtSlot()
    def _add_images(self):
        """Opens file dialog to select images and adds them to the appropriate list."""
        file_filter = "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"
        last_dir = self.settings_service.get_setting("last_image_dir", str(Path.home()))
        filepaths_tuple = QFileDialog.getOpenFileNames(self, "Select Image(s)", last_dir, file_filter)
        filepath_strs = filepaths_tuple[0]

        if filepath_strs:
            newly_added = 0
            first_added_path = None

            # Determine which list to add to based on the mode
            target_list = self._sequential_image_queue if self._sequential_mode_enabled else self._selected_image_paths
            mode_name = "Sequential" if self._sequential_mode_enabled else "Standard"

            for fp_str in filepath_strs:
                p_path = Path(fp_str)
                if p_path.is_file():
                    if p_path not in target_list: # Check against the target list
                        target_list.append(p_path) # Add to the target list
                        newly_added += 1
                        if first_added_path is None: first_added_path = p_path
                    else:
                        log_debug(f"SingleMode ({mode_name}): Image already selected: {p_path.name}")
                else:
                    log_warning(f"SingleMode ({mode_name}): Selected path is not a file: {p_path}")

            if newly_added > 0:
                log_info(f"SingleMode ({mode_name}): Added {newly_added} image(s). Total in current mode list: {len(target_list)}")

                # If sequential mode was just enabled or queue was empty/finished, reset index
                # Check index specifically to handle cases where user adds more images mid-sequence but before starting next
                if self._sequential_mode_enabled and self._sequential_current_index == -1:
                     log_debug("SingleMode (Sequential): Resetting sequence index to 0 after adding images.")
                     self._sequential_current_index = 0

                self._update_image_label() # Update label based on the current mode
                if first_added_path:
                    self.settings_service.set_setting("last_image_dir", str(first_added_path.parent))
            else:
                 log_info(f"SingleMode ({mode_name}): No new valid images were added.")




    @pyqtSlot()
    def _clear_images(self):
        """Clears the selected image list based on the current mode."""
        if self._sequential_mode_enabled:
            if self._sequential_image_queue:
                log_info("SingleModeWidget: Clearing sequential image queue.")
                self._sequential_image_queue = []
                self._sequential_current_index = -1 # Reset index
                self._update_image_label()
                self.status_update.emit("Sequential image queue cleared.", 3000)
            else:
                log_debug("SingleModeWidget: Sequential image queue already empty.")
        else:
            if self._selected_image_paths:
                log_info("SingleModeWidget: Clearing selected images.")
                self._selected_image_paths = []
                # No thumbnail handling needed in SingleModeWidget directly
                self._update_image_label()
                self.status_update.emit("Selected images cleared.", 3000)
            else:
                log_debug("SingleModeWidget: Selected images already empty.")


    def _update_image_label(self):
        """Updates the label showing the number/status of selected images."""
        # Check if the currently selected model supports image input
        model_supports_images = self.add_image_button.isEnabled()

        if self._sequential_mode_enabled:
            q_len = len(self._sequential_image_queue)
            current_idx = self._sequential_current_index

            # Base tooltip for sequential mode
            tooltip_base = f"Sequential Mode: {q_len} image{'s' if q_len != 1 else ''} in queue."

            if q_len == 0:
                self.image_label.setText("Seq: Queue Empty")
                self.image_label.setToolTip(tooltip_base)
                self.clear_images_button.setEnabled(False)
            elif 0 <= current_idx < q_len:
                # Currently processing or paused mid-sequence
                current_file = self._sequential_image_queue[current_idx]
                display_name = current_file.name[:25] + '...' if len(current_file.name) > 28 else current_file.name
                self.image_label.setText(f"Seq: Proc. {current_idx+1}/{q_len}")
                self.image_label.setToolTip(f"{tooltip_base}\nCurrent: {current_file.name}")
                self.clear_images_button.setEnabled(True)
            else: # Index is -1 (not started or finished loop) or out of bounds
                self.image_label.setText(f"Seq: {q_len} image{'s' if q_len != 1 else ''} ready")
                self.image_label.setToolTip(tooltip_base + "\nReady to start sequence.")
                self.clear_images_button.setEnabled(True)

        else: # Standard (Simultaneous) Mode
            count = len(self._selected_image_paths)
            tooltip_base = f"Simultaneous Mode: {count} image{'s' if count != 1 else ''} selected."

            if not model_supports_images:
                self.image_label.setText("Model may not support images.")
                if count > 0:
                     self.image_label.setText(f"{count} Img (Model Issue)")
                     self.image_label.setToolTip(tooltip_base + "\nWarning: Selected model may not support image input.")
                else:
                     self.image_label.setToolTip("Selected model may not support image input.")
                self.clear_images_button.setEnabled(count > 0) # Allow clearing even if model doesn't support
                return # Exit early

            # Model supports images, proceed with standard display
            if count == 0:
                self.image_label.setText("No image selected.")
                self.image_label.setToolTip(tooltip_base)
                self.clear_images_button.setEnabled(False)
            elif count == 1:
                file_path = self._selected_image_paths[0]
                display_name = file_path.name[:25] + '...' if len(file_path.name) > 28 else file_path.name
                self.image_label.setText(f"1 image: {display_name}")
                self.image_label.setToolTip(f"{tooltip_base}\nFile: {file_path.name}")
                self.clear_images_button.setEnabled(True)
            else:
                names = ", ".join(p.name for p in self._selected_image_paths[:2])
                suffix = "..." if count > 2 else ""
                self.image_label.setText(f"{count} images: {names}{suffix}")
                tooltip_files = "\n".join(f"- {p.name}" for p in self._selected_image_paths)
                self.image_label.setToolTip(f"{tooltip_base}\nFiles:\n{tooltip_files}")
                self.clear_images_button.setEnabled(True)

    @pyqtSlot()
    def _configure_safety(self):
        """Opens the dedicated safety settings dialog."""
        if not SDK_TYPES_AVAILABLE: # Should be disabled, but double-check
            log_error("Safety button clicked, but SDK types are not available.")
            show_error_message(self, "SDK Error", "Cannot configure safety settings - google-genai library types missing.")
            return

        # Pass the current settings to the dialog
        dialog = SafetySettingsDialog(self._current_safety_settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
             new_settings = dialog.get_selected_settings()
             # Check if settings actually changed before updating
             if new_settings != self._current_safety_settings:
                  self._current_safety_settings = new_settings
                  # Save immediately to settings service for persistence
                  self.settings_service.set_setting("single_mode_safety_settings", self._current_safety_settings)
                  log_info(f"Safety settings updated and saved: {self._current_safety_settings}")
                  self.status_update.emit("Safety settings updated.", 3000)
             else:
                  log_debug("Safety settings dialog accepted, but no changes made.")
        else:
             log_debug("Safety settings dialog cancelled.")



    def start_generation(self):
         """Public method to initiate generation (callable externally)."""
         # Use a static identifier for Single Mode
         instance_log_prefix = "SingleMode"
         log_info(f"--- {instance_log_prefix}: start_generation CALLED ---")

         # --- Cleanup block ---
         if self._generation_worker is not None:
             log_warning(f"{instance_log_prefix}: Previous worker reference exists. Clearing.")
             self._generation_worker = None

         # --- Defensive Guard ---
         if self._is_running:
              log_warning(f"{instance_log_prefix}: Already running. Aborting.")
              self._update_button_style(); return

         # --- Stop Timer ---
         if self._loop_timer.isActive():
             log_debug(f"{instance_log_prefix}: Stopping loop timer."); self._loop_timer.stop()

         # --- Loop Flag Check ---
         if not self._continuous_loop_active:
             self._continuous_loop_active = self.continuous_checkbox.isChecked()
             log_debug(f"{instance_log_prefix}: Loop flag check. Active: {self._continuous_loop_active}")

         # --- Validate Inputs ---
         log_debug(f"{instance_log_prefix}: Validating inputs...")
         api_key_name = self._current_api_key_name; api_key_value = self._current_api_key_value
         if not api_key_name or not api_key_value:
             show_error_message(self, "API Key Error", "Valid API Key not selected."); self._continuous_loop_active = False; self._set_ui_generating(False); self._update_button_style(); self.status_update.emit("Select API Key.", 0); return
         if not self.gemini_handler.is_client_available(api_key_name):
              show_error_message(self, "Client Error", f"Client for key '{api_key_name}' unavailable."); self._continuous_loop_active = False; self._set_ui_generating(False); self._update_button_style(); self.status_update.emit("Client Error.", 0); return
         model_name = self.model_combo.itemData(self.model_combo.currentIndex())
         if not model_name:
             show_error_message(self, "Input Error", "Select a Model."); self._continuous_loop_active = False; self._set_ui_generating(False); self._update_button_style(); self.status_update.emit("Select Model.", 0); return
         # --- GET UNRESOLVED PROMPT TEXT ---
         unresolved_prompt_text = self.prompt_text_edit.toPlainText().strip()
         if not unresolved_prompt_text:
             show_error_message(self, "Input Error", "Prompt cannot be empty."); self._continuous_loop_active = False; self._set_ui_generating(False); self._update_button_style(); self.status_update.emit("Enter Prompt.", 0); return
         log_debug(f"{instance_log_prefix}: Input validation passed.")

         # --- Resolve Wildcards ONCE ---
         try:
             log_debug(f"{instance_log_prefix}: Resolving wildcards...")
             # Use the unresolved text from the editor
             resolved_prompt, _original_unresolved, original_resolved_map = self.wildcard_resolver.resolve(unresolved_prompt_text)
             resolved_map_copy = copy.deepcopy(original_resolved_map) # Keep the copy for context
             log_info(f"[{instance_log_prefix} RESOLVED ONCE] resolved_prompt: {resolved_prompt[:100]}...")
             log_info(f"[{instance_log_prefix} RESOLVED ONCE] resolved_map_copy: {resolved_map_copy}")
         except Exception as wc_err:
              log_error(f"{instance_log_prefix}: Wildcard resolution error: {wc_err}", exc_info=True); self._set_ui_generating(False); self._update_button_style(); show_error_message(self, "Wildcard Error", f"Wildcard resolution failed: {wc_err}"); return

         # --- Validate Image Support & Prepare Images ---
         models_for_current_key = self.gemini_handler.available_models_cache.get(api_key_name, [])
         model_info = next((m for m in models_for_current_key if m['name'] == model_name), None)
         supports_images = model_info.get('likely_image_support', False) if model_info else False
         image_list_for_worker = []; current_image_for_context = None
         if self._sequential_mode_enabled:
             if not self._sequential_image_queue: log_error(f"{instance_log_prefix}: Seq mode, no images."); show_error_message(self, "Input Error", "Sequential mode enabled, but no images."); self._continuous_loop_active = False; self._set_ui_generating(False); self._update_button_style(); self._update_image_label(); self.status_update.emit("No Images in Seq.", 0); return
             if not supports_images: log_error(f"{instance_log_prefix}: Seq mode, model no img support."); show_error_message(self, "Input Error", "Model doesn't support images for sequential mode."); self._continuous_loop_active = False; self._set_ui_generating(False); self._update_button_style(); self.status_update.emit("Model lacks image support.", 0); return
             if not (0 <= self._sequential_current_index < len(self._sequential_image_queue)): self._sequential_current_index = 0; log_info(f"{instance_log_prefix}: Seq index reset to 0.")
             current_image_path = self._sequential_image_queue[self._sequential_current_index]; image_list_for_worker = [current_image_path]; current_image_for_context = current_image_path
             display_name = current_image_path.name[:15] + '...' if len(current_image_path.name) > 18 else current_image_path.name; status_msg = f"Seq: Proc. {self._sequential_current_index+1}/{len(self._sequential_image_queue)} ({display_name})..."; self.status_update.emit(status_msg, 0)
         else: # Standard mode
             if self._selected_image_paths and not supports_images: log_error(f"{instance_log_prefix}: Std mode, images selected, model no support."); show_error_message(self, "Input Error", "Model doesn't support images."); self._continuous_loop_active = False; self._set_ui_generating(False); self._update_button_style(); self.status_update.emit("Model lacks image support.", 0); return
             image_list_for_worker = self._selected_image_paths.copy(); current_image_for_context = None

         # --- Resolve Specific Wildcards for Filename Context ---
         log_debug(f"{instance_log_prefix}: Starting pre-resolve for filename wildcards...")
         filename_wildcard_values = {}
         active_pattern_name = self.settings_service.get_setting(constants.ACTIVE_FILENAME_PATTERN_NAME_KEY, constants.DEFAULT_FILENAME_PATTERN_NAME)
         saved_patterns = self.settings_service.get_saved_filename_patterns()
         current_pattern_string = saved_patterns.get(active_pattern_name, constants.DEFAULT_FILENAME_PATTERN)
         placeholder_indices_in_pattern = set()
         wc_value_matches = re.findall(r"\{wildcard_value:(\d+)\}", current_pattern_string)
         for index_str in wc_value_matches:
              try: placeholder_indices_in_pattern.add(int(index_str))
              except ValueError: pass
         if placeholder_indices_in_pattern: log_debug(f"{instance_log_prefix}: Pre-resolving {len(placeholder_indices_in_pattern)} wildcard_value placeholders...")
         # --- Pass UNRESOLVED text to resolve_specific_wildcard ---
         for index in sorted(list(placeholder_indices_in_pattern)):
             try:
                  resolved_wc_value = self.wildcard_resolver.resolve_specific_wildcard(unresolved_prompt_text, index)
                  if resolved_wc_value is not None: filename_wildcard_values[f"wildcard_value_{index}"] = resolved_wc_value; log_debug(f"{instance_log_prefix}:   Pre-resolved [wildcard_value_{index}]: '{resolved_wc_value}'")
                  else: log_warning(f"{instance_log_prefix}:   Failed pre-resolve index {index}."); filename_wildcard_values[f"wildcard_value_{index}"] = f"WC{index}_NOT_FOUND"
             except Exception as e: log_error(f"{instance_log_prefix}:   Error pre-resolving index {index}: {e}"); filename_wildcard_values[f"wildcard_value_{index}"] = f"WC{index}_ERROR"
         # --- End Pass UNRESOLVED text ---
         log_debug(f"{instance_log_prefix}: Finished pre-resolve for filename wildcards.")

         # --- Prepare Worker Parameters ---
         log_debug(f"{instance_log_prefix}: Preparing generation parameters...")
         gen_params = {
             "model_name": model_name,
             # --- PASS RESOLVED PROMPT TEXT TO WORKER ---
             "resolved_prompt_text": resolved_prompt,
             # --- Pass UNRESOLVED text separately for context ---
             "unresolved_prompt_text": unresolved_prompt_text,
             # --- REMOVE WILDCARD RESOLVER FROM PARAMS ---
             # "wildcard_resolver": self.wildcard_resolver, # No longer needed by handler
             "image_paths": image_list_for_worker,
             "temperature": self.temperature_spin.value(), "top_p": self.top_p_spin.value(),
             "max_output_tokens": self.max_tokens_spin.value(),
             "safety_settings_dict": self._current_safety_settings,
             "request_timeout": None,
             "retry_count": self.settings_service.get_setting("retry_count", constants.DEFAULT_RETRY_COUNT),
             "retry_delay": self.settings_service.get_setting("retry_delay", constants.DEFAULT_RETRY_DELAY),
             # resolved_wildcards_by_name is now passed explicitly to worker, not needed here
             **filename_wildcard_values
         }
         log_info(f"[{instance_log_prefix} PRE-WORKER INIT] Passing explicit resolved_map_copy")

         # --- Update UI State --- BEFORE starting worker
         log_debug(f"{instance_log_prefix}: Updating UI to 'generating' state...")
         self.save_prompt_button.setVisible(False); self._set_ui_generating(True)
         self.result_text_edit.clear()
         if not self._thumbnail_loaded: self.result_image_label.clear(); self._full_result_pixmap = None; self.result_image_label.setText("...")
         if not self._sequential_mode_enabled: self.status_update.emit("Generating...", 0)
         self.progress_bar.setVisible(True)

         # --- Create Worker and Move to Persistent Thread ---
         log_debug(f"{instance_log_prefix}: Creating GenerationWorker...")
         _image_filename_context = current_image_for_context.name if current_image_for_context else None
         if not hasattr(self, '_worker_thread') or self._worker_thread is None or not self._worker_thread.isRunning():
             log_critical(f"{instance_log_prefix}: Worker thread error!"); self._continuous_loop_active = False; self._set_ui_generating(False); self._update_button_style(); self.status_update.emit("Internal Error: Thread."); return

         self._generation_worker = GenerationWorker(
             gemini_handler=self.gemini_handler, api_key_name=api_key_name,
             api_key_value=api_key_value,
             params=gen_params, # Pass other params
             resolved_wildcards_map=resolved_map_copy, # Pass the map explicitly
             image_filename_context=_image_filename_context,
         )
         self._generation_worker.moveToThread(self._worker_thread)

         # --- Connect Signals ---
         log_debug(f"{instance_log_prefix}: Connecting worker signals...")
         self._generation_worker.finished.connect(self._on_generation_finished)
         self._generation_worker.finished.connect(self._generation_worker.deleteLater)

         # --- Trigger Worker ---
         log_info(f"{instance_log_prefix}: Invoking worker.run()...")
         QMetaObject.invokeMethod(self._generation_worker, "run", Qt.ConnectionType.QueuedConnection)

         # --- Final State Update ---
         self._is_running = True; self._set_ui_generating(True)
         log_info(f"--- {instance_log_prefix}: start_generation COMPLETE ---") # Note: generation_started signal removed from SingleMode


      
    def _start_sequential_next(self):
        """Helper method to start the next step in the sequence."""
        # This simply calls the main start method again, which will now
        # pick up the incremented _sequential_current_index.
        if hasattr(self, 'start_generation'): # InstanceWidget
            self.start_generation()
        elif hasattr(self, '_start_generation'): # SingleModeWidget
            self._start_generation()


    @pyqtSlot()
    def _cancel_generation(self):
         """Requests cancellation of the ongoing generation worker and updates UI."""
         # Note: This method ONLY cancels the current worker.
         # Stopping the loop is handled by _stop_loop.
         instance_log_prefix = "SingleMode" # For logging consistency

         # Check _is_running AND if the worker object exists
         if self._is_running and self._generation_worker:
             log_info(f"{instance_log_prefix}: Requesting generation cancellation.")
             self.status_update.emit("Cancelling generation...", 0) # Update status bar
             # Call the cancel method on the worker object
             # Use QMetaObject.invokeMethod for cross-thread call safety
             QMetaObject.invokeMethod(self._generation_worker, "cancel", Qt.ConnectionType.QueuedConnection)

             # Disable the cancel button immediately after requesting cancel
             # This is done in _set_ui_generating, but let's make sure the state is updated
             self._set_ui_generating(True) # Keep UI disabled while cancellation is processed
             self.cancel_button.setEnabled(False) # Explicitly disable CANCEL button
             self.cancel_button.setToolTip("Cancellation requested...")

             # --- UI Cleanup for Cancellation ---
             self.save_prompt_button.setVisible(False) # Hide save button
             # Clear result display immediately on cancel request
             self.result_text_edit.clear()
             self.result_text_edit.setPlaceholderText("[Generation Cancelled]")
             self.result_image_label.clear()
             self.result_image_label.setText("Cancelled.")
             self._full_result_pixmap = None # Clear stored pixmap
             # --- End UI Cleanup ---

             # The finished handler (_on_generation_finished) will eventually fire
             # with status "cancelled" and do the final UI state reset (_set_ui_generating(False)).
         elif self._is_running and not self._generation_worker:
              # Should not happen if state is consistent, but handle defensively
              log_error(f"{instance_log_prefix}: Cancel requested, _is_running=True, but no worker object found! Forcing stop.")
              self._is_running = False
              self._continuous_loop_active = False # Stop loop if state is inconsistent
              self._set_ui_generating(False) # Reset UI
         else:
             log_debug(f"{instance_log_prefix}: Cancel requested but worker not running.")
             # If cancel is called when not running, ensure UI is reset and loop is off
             self._continuous_loop_active = False
             self._set_ui_generating(False) # Ensure UI is reset correctly
             self.save_prompt_button.setVisible(False) # Ensure save button is hidden
             self.status_update.emit("Ready.", 3000) # Reset status bar


    def _set_ui_generating(self, generating: bool):
         """
         Helper to enable/disable UI elements based on generation state
         (worker running OR continuous loop active).
         """
         self._is_running = generating # Update the worker running state

         # Determine if config widgets should be disabled:
         # Disable if worker running OR if loop is intended to be active
         disable_config = self._is_running or self._continuous_loop_active
         log_debug(f"SingleMode: Setting UI Generating: worker_running={generating}, loop_active={self._continuous_loop_active}, disable_config={disable_config}")

         self.progress_bar.setVisible(self._is_running) # Progress bar only visible when worker is active

         # --- Disable Config Widgets based on disable_config ---
         self.api_key_combo.setEnabled(not disable_config)
         self.manage_keys_button.setEnabled(not disable_config)
         can_enable_model_widgets = not disable_config and self.api_key_combo.currentIndex() > 0
         self.model_combo.setEnabled(can_enable_model_widgets)
         self.refresh_models_button.setEnabled(can_enable_model_widgets)
         self.temperature_spin.setEnabled(not disable_config)
         self.top_p_spin.setEnabled(not disable_config)
         self.max_tokens_spin.setEnabled(not disable_config)
         self.reset_params_button.setEnabled(not disable_config)
         self.safety_button.setEnabled(not disable_config and SDK_TYPES_AVAILABLE)
         self.prompt_combo.setEnabled(not disable_config)
         self.manage_prompts_button.setEnabled(not disable_config)
         self.prompt_text_edit.setReadOnly(disable_config) # Readonly if running or loop active

         model_supports_images = False
         if not disable_config and self.model_combo.isEnabled():
              model_name = self.model_combo.itemData(self.model_combo.currentIndex())
              if model_name and self._current_api_key_name:
                    models_for_current_key = self.gemini_handler.available_models_cache.get(self._current_api_key_name, [])
                    model_info = next((m for m in models_for_current_key if m['name'] == model_name), None)
                    model_supports_images = model_info.get('likely_image_support', False) if model_info else False
         self.add_image_button.setEnabled(not disable_config and model_supports_images)
         image_list = self._sequential_image_queue if self._sequential_mode_enabled else self._selected_image_paths
         self.clear_images_button.setEnabled(not disable_config and bool(image_list))
         self.sequential_image_checkbox.setEnabled(not disable_config) # Disable if loop active or running
         self.browse_save_path_button.setEnabled(not disable_config)
         self.clear_save_path_button.setEnabled(not disable_config and bool(self._custom_save_path))

         # Checkboxes: Enable unless worker is active or loop active
         self.continuous_checkbox.setEnabled(not disable_config)
         self.auto_save_checkbox.setEnabled(not disable_config)
         # --- End Disable Config Widgets ---

         # --- Update Generate/Cancel Button States ---
         # Use the dedicated helper function
         self._update_single_mode_button_states()


    def _update_single_mode_button_states(self):
         """Updates Generate/Cancel/Stop Loop button states and text."""
         can_start_new = not (self._is_running or self._continuous_loop_active) and \
                         bool(self._current_api_key_name) and \
                         self.model_combo.count() > 0 and \
                         self.model_combo.currentIndex() >= 0 # Ensure a model is actually selected


         if self._continuous_loop_active:
             # Loop is active (or intended to be)
             self.generate_button.setText("Stop Loop")
             self.generate_button.setToolTip("Stop the continuous generation loop.")
             # Stop Loop button should be enabled IF the loop is active,
             # OR if the worker is currently running (even if loop flag was just turned off).
             self.generate_button.setEnabled(True)
             self.cancel_button.setEnabled(False) # Cancel is handled by Stop Loop button
             self.cancel_button.setVisible(False)
         elif self._is_running:
             # Worker running, but not in loop context (single run)
             self.generate_button.setText("Generate")
             self.generate_button.setToolTip("Generation in progress...")
             self.generate_button.setEnabled(False) # Generate button disabled during single run
             self.cancel_button.setEnabled(True)  # Separate cancel button is active for single run
             self.cancel_button.setVisible(True)
         else:
             # Idle state (worker not running, loop not active)
             self.generate_button.setText("Generate")
             self.generate_button.setToolTip("Start generation (check 'Continuous' to loop).")
             self.generate_button.setEnabled(can_start_new) # Enable based on config validity
             self.cancel_button.setEnabled(False)
             self.cancel_button.setVisible(True) # Keep visible but disabled



    @pyqtSlot(dict)
    def _on_generation_finished(self, result: dict):
        """Handles the result when the GenerationWorker thread finishes."""
        instance_log_prefix = "SingleMode"
        log_info(f"--- {instance_log_prefix}: _on_generation_finished RECEIVED result ---")
        log_info(f"[{instance_log_prefix} RECEIVED RESULT] result['resolved_wildcards_by_name']: {result.get('resolved_wildcards_by_name')}") # Log received map

        was_running = self._is_running; self._is_running = False # Reset running flag

        if not was_running:
            log_warning(f"{instance_log_prefix}: Received finish signal but wasn't marked as running."); self._set_ui_generating(False); self._update_button_style(); return
        else:
            log_info(f"{instance_log_prefix}: Worker finished. Status: {result.get('status')}")

        self._last_resolved_prompt = None; self._last_image_bytes = None; self._last_image_mime = None; self._thumbnail_loaded = False # Clear last result state

        trigger_next = False; final_status_message = "Ready."

        try:
            status = result.get("status")
            unresolved_prompt = result.get("unresolved_prompt", "")
            resolved_prompt = result.get("resolved_prompt", "")
            # --- Extract the CORRECT map ---
            resolved_values_by_name_map = result.get("resolved_wildcards_by_name", {})
            log_info(f"[{instance_log_prefix} EXTRACTED FROM RESULT] resolved_values_by_name_map: {resolved_values_by_name_map}")
            # --- End map extraction ---
            error_message = result.get("error_message", "Unknown error.")
            image_bytes = result.get("image_bytes"); image_mime = result.get("image_mime")
            text_result = result.get("text_result", "")
            image_filename_context = result.get("image_filename_context")
            filename_wildcard_values = { k: v for k, v in result.items() if k.startswith("wildcard_value_") }
            log_debug(f"{instance_log_prefix}: Received pre-resolved filename wildcard values: {filename_wildcard_values}")

            if status == "rate_limited":
                failed_key_name = result.get("api_key_name", self._current_api_key_name or "Unknown"); log_warning(f"{instance_log_prefix}: Rate limit on key '{failed_key_name}'."); self._set_ui_generating(False); self._update_button_style(); self.status_update.emit(f"Rate Limit '{failed_key_name}'. Trying next key...", 0); QTimer.singleShot(0, self._attempt_next_api_key_and_retry); trigger_next = False; self.progress_bar.setVisible(True); self.save_prompt_button.setVisible(False); return

            elif status == "success":
                image_successfully_displayed = False
                if image_bytes: image_successfully_displayed = self._display_image(image_bytes);
                if image_successfully_displayed: self._last_image_bytes = image_bytes; self._last_image_mime = image_mime
                else:
                    if self._full_result_pixmap is None: self.result_image_label.clear(); self.result_image_label.setText("No image generated."); self.result_image_label.setToolTip("")
                resolved_prompt_or_empty = resolved_prompt or ""; self.result_text_edit.setPlainText(text_result or "[No text generated]"); self.status_update.emit("Generation successful.", 3000)
                final_status_message = "Success."; trigger_next = True; self._last_resolved_prompt = resolved_prompt_or_empty
                can_save_prompt = self._last_resolved_prompt is not None and self._last_image_bytes is not None; self.save_prompt_button.setVisible(can_save_prompt)
                if self._last_image_bytes is not None and self.auto_save_checkbox.isChecked():
                    log_info(f"[{instance_log_prefix} PRE-AUTOSAVE] Passing resolved_values_by_name_map: {resolved_values_by_name_map}") # Log map before passing
                    self._auto_save_result(self._last_image_bytes, self._last_image_mime, text_result, unresolved_prompt, resolved_prompt, resolved_values_by_name_map, image_filename_context, filename_wildcard_values)

            elif status == "blocked":
                block_reason = result.get('block_reason', 'Unknown'); self.result_text_edit.setPlainText(f"--- BLOCKED ---\nReason: {block_reason}\n\n{text_result or '[No text]'}")
                log_warning(f"{instance_log_prefix}: Blocked: {block_reason}"); self.status_update.emit(f"Blocked: {block_reason}", 5000); final_status_message = f"Blocked: {block_reason}"; trigger_next = self._continuous_loop_active; self.save_prompt_button.setVisible(False)
                # --- Prepare map for score update ---
                if self.wildcard_resolver:
                    score_update_map = { f"[{name}]": values[0] for name, values in resolved_values_by_name_map.items() if values } # Approximate map
                    self.wildcard_resolver.update_scores(score_update_map, "blocked")

            elif status == "cancelled":
                self.status_update.emit("Generation cancelled.", 3000); final_status_message = "Cancelled."; self._continuous_loop_active = False; trigger_next = False; self.save_prompt_button.setVisible(False)

            elif status == "error":
                log_error(f"{instance_log_prefix} Error: {error_message}"); self.result_text_edit.setPlainText(f"--- ERROR ---\n{error_message}"); self.result_image_label.clear(); self.result_image_label.setText("[Error]"); self._full_result_pixmap = None; self._thumbnail_loaded = False
                self.status_update.emit("Error occurred.", 5000); final_status_message = "Error."; trigger_next = self._continuous_loop_active; self.save_prompt_button.setVisible(False)

            else: # Unknown status
                log_error(f"{instance_log_prefix} Unknown Status: {status}"); self.result_text_edit.setPlainText(f"[Unknown Status: {status}]\n{error_message}"); self.result_image_label.clear(); self.result_image_label.setText("[Unknown]"); self._full_result_pixmap = None; self._thumbnail_loaded = False
                self.status_update.emit(f"Unknown result status: {status}", 5000); final_status_message = "Unknown Status."; trigger_next = self._continuous_loop_active; self.save_prompt_button.setVisible(False)

            # Update scores on success
            if status == "success":
                if self.wildcard_resolver:
                    # --- Prepare CORRECT map for score update ---
                    # We need original_wildcard_text -> chosen_value
                    # Approximate using the first value from the map
                    score_update_map = { f"[{name}]": values[0] for name, values in resolved_values_by_name_map.items() if values }
                    log_info(f"[{instance_log_prefix} PRE-SCORE UPDATE] Using approximate map: {score_update_map}")
                    self.wildcard_resolver.update_scores(score_update_map, "success")

            # --- Sequential/Continuous Loop Logic ---
            should_schedule_next = False
            if trigger_next and self._continuous_loop_active:
                if self._sequential_mode_enabled:
                    log_debug(f"{instance_log_prefix}: Seq mode active, processing next."); self._sequential_current_index += 1
                    if self._sequential_current_index < len(self._sequential_image_queue): log_info(f"{instance_log_prefix}: Seq next index {self._sequential_current_index}."); should_schedule_next = True
                    else: # Sequence finished
                        log_info(f"{instance_log_prefix}: Seq complete."); self._sequential_current_index = -1; self._update_image_label()
                        if self._continuous_loop_active: log_info(f"{instance_log_prefix}: Seq looping."); self._sequential_current_index = 0; should_schedule_next = True
                        else: self.status_update.emit("Sequence Complete.", 3000); final_status_message = "Sequence Complete."; self._continuous_loop_active = False
                else: # Non-sequential continuous mode
                    log_info(f"{instance_log_prefix}: Continuous non-seq restarting."); should_schedule_next = True
            else:
                if self._continuous_loop_active and not trigger_next: log_info(f"{instance_log_prefix}: Loop stop triggered by non-success/cancel.")
                self._continuous_loop_active = False # Ensure flag is off if not scheduling next

            # --- Schedule Next Run or Reset UI ---
            if should_schedule_next:
                next_step_msg = "Next..." if self._sequential_mode_enabled and self._sequential_current_index != 0 else "Looping..."; self.status_update.emit(next_step_msg, 0)
                request_delay_ms = self.settings_service.get_setting("request_delay", constants.DEFAULT_REQUEST_DELAY) * 1000
                self._set_ui_generating(False); self._update_button_style(); self._loop_timer.start(max(100, request_delay_ms)); log_debug(f"{instance_log_prefix}: Scheduled next run check in {max(100, request_delay_ms)}ms.")
            else: # Loop is stopping/finished normally or due to error/block/cancel
                self._continuous_loop_active = False; self._set_ui_generating(False); self._update_button_style()
                # Reset status to Ready after a delay
                QTimer.singleShot(4000, lambda: self.status_update.emit("Ready.", 3000) if not self._is_running and not self._continuous_loop_active else None)

        except Exception as e:
            log_critical(f"{instance_log_prefix}: CRITICAL UNHANDLED EXCEPTION in _on_generation_finished: {e}", exc_info=True)
            self._is_running = False; self._continuous_loop_active = False
            if self._loop_timer.isActive(): self._loop_timer.stop()
            self._set_ui_generating(False); self._update_button_style(); self.result_text_edit.setPlainText(f"--- CRITICAL ERROR IN SINGLE MODE ---\n{e}\n\nCheck logs.");
            if not self._thumbnail_loaded: self.result_image_label.setText("[CRITICAL ERR]")
            self._full_result_pixmap = None; self._thumbnail_loaded = False; self.save_prompt_button.setVisible(False); self.status_update.emit("CRITICAL ERROR. Check logs.", 0)

        finally:
            self._generation_worker = None # Clear worker reference
            log_debug(f"{instance_log_prefix}: Cleared worker ref."); log_info(f"--- {instance_log_prefix}: _on_generation_finished COMPLETE ---")



    @pyqtSlot()
    def _try_start_next_generation(self):
         """Checks if the loop is still active before starting the next run."""
         # --- CRITICAL CHECK ---
         # This slot is triggered by the QTimer. Before starting a new generation run,
         # we MUST check if the continuous loop is still desired (`_continuous_loop_active`)
         # AND if the worker is currently idle (`!self._is_running`).
         # This prevents starting a new run if the user stopped the loop while the timer was pending,
         # or if somehow the previous worker hasn't finished yet (_is_running is still True).
         if self._continuous_loop_active and not self._is_running:
             log_debug("SingleMode: Timer fired, loop active, worker idle. Starting next generation.")
             # If the loop is still active and the worker is idle, call the main start function
             # to set up and run the next generation.
             self.start_generation() # <<< CORRECTED CALL
         elif self._continuous_loop_active and self._is_running:
              # This is a safeguard: if the timer fires but the worker is still marked as running,
              # something is wrong with the state management or the previous worker is stuck.
              log_warning("SingleMode: Timer fired, loop active, but worker is STILL running! Skipping start.")
              # We should NOT start a new worker. Update UI state just in case it's out of sync.
              self._set_ui_generating(True) # Ensure UI reflects running state
         else:
             # If the timer fires but the loop is no longer active (user stopped it),
             # log this and ensure the UI is in the idle state.
             log_info("SingleMode: Timer fired, but loop inactive or worker running. Not starting next generation.")
             # Ensure UI is fully idle if the loop was stopped while timer was pending
             self._set_ui_generating(False)


    @pyqtSlot()
    def _attempt_next_api_key_and_retry(self):
        """Finds the next available API key and attempts to restart generation."""
        log_info("SingleMode: Attempting to switch to the next API key due to rate limit.")

        current_key_index = self.api_key_combo.currentIndex()
        current_key_name = self.api_key_combo.itemData(current_key_index)

        if current_key_name is None: # Should not happen if generation started
            log_error("SingleMode: Cannot switch key: No valid key currently selected.")
            show_error_message(self,"Key Switch Error", "Cannot find the current API key to switch from.")
            self._continuous_loop_active = False # Stop loop on error
            self._set_ui_generating(False) # Stop the 'generating' state
            # Use status_update signal:
            self.status_update.emit("Key Error.", 5000)
            return

        total_keys = self.api_key_combo.count() - 1 # Exclude placeholder at index 0
        if total_keys <= 1:
            log_warning(f"SingleMode: Rate limit hit on '{current_key_name}', but no other API keys available to switch to.")
            show_warning_message(self, "Rate Limit", f"API Key '{current_key_name}' hit rate limit. No other keys available.")
            self._continuous_loop_active = False # Stop loop
            self._set_ui_generating(False) # Stop the 'generating' state
            self.generate_button.setEnabled(False) # Disable button
            self.generate_button.setToolTip(f"Rate limit hit on key '{current_key_name}'. No other keys.")
            # Use status_update signal:
            self.status_update.emit(f"Rate limit (No other keys).", 0)
            return

        # Calculate next index (wrapping around, skipping placeholder at index 0)
        current_actual_index = current_key_index - 1 # 0-based index among *valid* keys
        next_actual_index = (current_actual_index + 1) % total_keys
        next_combo_index = next_actual_index + 1 # Back to 1-based index for combo box

        next_key_name = self.api_key_combo.itemData(next_combo_index)
        next_key_value = self.api_key_service.get_key_value(next_key_name)

        if not next_key_name or not next_key_value:
            log_error(f"SingleMode: Failed to get next key details (Index: {next_combo_index}, Name: {next_key_name}). Stopping loop.")
            show_error_message(self, "Key Switch Error", "Could not retrieve the next API key value.")
            self._continuous_loop_active = False # Stop loop on error
            self._set_ui_generating(False)
            # Use status_update signal:
            self.status_update.emit("Key Switch Error.", 5000)
            return

        log_info(f"SingleMode: Switching from rate-limited key '{current_key_name}' to '{next_key_name}'.")

        # --- Directly update internal state ---
        self._current_api_key_name = next_key_name
        self._current_api_key_value = next_key_value
        self.settings_service.set_setting("last_used_api_key_name", self._current_api_key_name) # Save new key as last used

        # --- Update the UI ComboBox selection WITHOUT triggering signals ---
        self.api_key_combo.blockSignals(True)
        self.api_key_combo.setCurrentIndex(next_combo_index)
        self.api_key_combo.blockSignals(False)

        # Use status_update signal (0 timeout for persistent message until next update):
        self.status_update.emit(f"Switched to key '{next_key_name}'. Retrying...", 0)
        QApplication.processEvents() # Allow UI update

        # --- Schedule the generation retry ---
        if self._continuous_loop_active:
            retry_delay_ms = self.settings_service.get_setting("request_delay", constants.DEFAULT_REQUEST_DELAY) * 1000
            log_info(f"SingleMode: Scheduling generation retry with new key in {retry_delay_ms}ms.")
            # *** CORRECTED METHOD CALL HERE ***
            QTimer.singleShot(max(500, retry_delay_ms), self.start_generation)
        else:
            log_info("SingleMode: Loop stopped before key switch completed. Not retrying.")
            self._set_ui_generating(False) # Ensure UI is idle
            # Use status_update signal (3000ms timeout):
            self.status_update.emit("Ready.", 3000)

         
 
    def _display_image(self, image_bytes: bytes) -> bool: # Return bool
        """Safely displays image bytes in the result label."""
        try:
            qimage = QImage.fromData(image_bytes)
            if qimage.isNull():
                 log_error("Failed to create QImage from received bytes.")
                 self.result_image_label.setText("Error displaying image (Invalid Data).")
                 self.result_image_label.setPixmap(QPixmap())
                 self._full_result_pixmap = None
                 return False # Indicate failure

            pixmap = QPixmap.fromImage(qimage)
            self._full_result_pixmap = pixmap # Store original

            self._scale_and_set_pixmap() # Use helper method

            self.result_image_label.setToolTip(f"Generated Image ({pixmap.width()}x{pixmap.height()})")
            log_info(f"Generated image displayed ({pixmap.width()}x{pixmap.height()}).")
            return True # Indicate success

        except Exception as e:
            log_error(f"Failed to display generated image: {e}", exc_info=True)
            self.result_image_label.setText("Error displaying image.")
            self.result_image_label.setPixmap(QPixmap())
            self._full_result_pixmap = None
            return False # Indicate failure
 
 
    def _scale_and_set_pixmap(self):
         """Scales the stored _full_result_pixmap to fit the label."""
         if not self._full_result_pixmap or self._full_result_pixmap.isNull():
              # Keep existing text like "No image generated" if no pixmap
              return

         label_size = self.result_image_label.size()
         # Prevent scaling if label size is invalid (e.g., during layout changes)
         if label_size.width() <= 1 or label_size.height() <= 1:
             log_warning("Cannot scale image, invalid label size.")
             # Set unscaled initially or if size is invalid
             self.result_image_label.setPixmap(self._full_result_pixmap)
             return

         scaled_pixmap = self._full_result_pixmap.scaled(label_size,
                                               Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation)
         current_pm = self.result_image_label.pixmap()
         # Avoid redundant sets if the scaled size hasn't visually changed
         if not current_pm or scaled_pixmap.cacheKey() != current_pm.cacheKey():
              self.result_image_label.setPixmap(scaled_pixmap)



    def _auto_save_result(self,
                          image_bytes: bytes,
                          image_mime: Optional[str],
                          text_result: str,
                          unresolved_prompt: str,
                          resolved_prompt: str,
                          resolved_values_by_name: Dict[str, List[str]], # <<< ADDED PARAMETER
                          image_filename_context: Optional[str] = None,
                          filename_wildcard_values: Optional[Dict[str, str]] = None): # Parameter added
        """Automatically saves the generated image and text using the filename service."""
        # Use a static identifier for Single Mode
        instance_log_prefix = "SingleMode"
        log_info(f"{instance_log_prefix}: Attempting auto-save...")
        try:
            # 1. Determine extension
            ext = mimetypes.guess_extension(image_mime) if image_mime else ".png"
            if not ext or ext == ".jpe": ext = ".jpg"
            if ext == ".jpeg": ext = ".jpg"

            # 2. Gather Data for Placeholders
            model_name = self.model_combo.itemData(self.model_combo.currentIndex()) or "unknown_model"
            api_key_name = self._current_api_key_name or "unknown_key"

            context_data = {
                "timestamp": time.time(),
                "model_name": model_name,
                "api_key_name": api_key_name,
                "instance_id": "NA", # Always 'NA' for Single Mode
                "unresolved_prompt": unresolved_prompt,
                "resolved_prompt": resolved_prompt,
                "image_filename": image_filename_context,
                "resolved_wildcards_by_name": resolved_values_by_name,
                **(filename_wildcard_values or {}) # Merge pre-resolved index values
            }
            log_debug(f"{instance_log_prefix}: Context data prepared for filename generation: { {k: (v[:50] + '...' if isinstance(v, str) and len(v)>50 else v) for k,v in context_data.items()} }")

            # 3. Get Pattern and Output Directory
            active_pattern_name = self.settings_service.get_setting(constants.ACTIVE_FILENAME_PATTERN_NAME_KEY, constants.DEFAULT_FILENAME_PATTERN_NAME)
            saved_patterns = self.settings_service.get_saved_filename_patterns()
            pattern = saved_patterns.get(active_pattern_name, constants.DEFAULT_FILENAME_PATTERN)
            log_debug(f"{instance_log_prefix}: Using filename pattern '{pattern}' (from active name '{active_pattern_name}')")

            if self._custom_save_path and self._custom_save_path.is_dir():
                save_dir = self._custom_save_path
                log_debug(f"{instance_log_prefix}: Using custom save directory: {save_dir}")
            else:
                if self._custom_save_path:
                    log_warning(f"{instance_log_prefix}: Custom save path '{self._custom_save_path}' is invalid. Falling back to default.")
                save_dir = constants.DATA_DIR / "output"
                log_debug(f"{instance_log_prefix}: Using default save directory: {save_dir}")

            # 4. Generate Unique Filename
            if not hasattr(self, 'filename_generator') or self.filename_generator is None:
                 log_error(f"{instance_log_prefix}: FilenameGeneratorService not initialized. Cannot save.")
                 self.status_update.emit("Save Error: Filename generator missing.", 5000)
                 return

            image_filename_path: Path = self.filename_generator.generate_filename(
                pattern, context_data, save_dir, ext
            )
            text_filename_path = image_filename_path.with_suffix(".txt")

            save_dir.mkdir(parents=True, exist_ok=True)

            # 5. Save Image (Existing logic)
            if ImageProcessor.save_image(image_bytes, image_filename_path):
                log_info(f"{instance_log_prefix}: Image auto-saved to {image_filename_path.name}")
                if self.settings_service.get_setting(constants.EMBED_METADATA_ENABLED, constants.DEFAULT_EMBED_METADATA_ENABLED):
                    log_debug(f"{instance_log_prefix}: Embed Metadata enabled. Embedding prompts...")
                    embed_success = ImageProcessor.embed_prompts_in_image(image_filename_path, unresolved_prompt, resolved_prompt)
                    if not embed_success:
                        log_warning(f"{instance_log_prefix}: Failed to embed metadata into auto-saved image: {image_filename_path.name}")
                else:
                    log_debug(f"{instance_log_prefix}: Embed Metadata disabled. Skipping.")
            else:
                log_error(f"{instance_log_prefix}: Auto-save failed for image: {image_filename_path.name}")
                self.status_update.emit("Auto-save failed for image.", 4000)

            # 6. Save Text (Existing logic)
            if self.settings_service.get_setting(constants.SAVE_TEXT_FILE_ENABLED, constants.DEFAULT_SAVE_TEXT_FILE_ENABLED):
                log_debug(f"{instance_log_prefix}: Save Text File enabled. Saving text...")
                try:
                    text_filename_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(text_filename_path, "w", encoding="utf-8") as f:
                        f.write(f"--- Instance ID: NA (Single Mode) ---\n") # Add Instance ID
                        f.write(f"--- API Key Name: {api_key_name} ---\n")
                        f.write(f"--- Model: {model_name} ---\n\n")
                        f.write(f"--- Unresolved Prompt ---\n{unresolved_prompt}\n\n")
                        f.write(f"--- Resolved Prompt ---\n{resolved_prompt}\n\n")
                        f.write(f"--- Generated Text ---\n{text_result if text_result else '[No text generated]'}")
                    log_info(f"{instance_log_prefix}: Text/Prompts auto-saved to {text_filename_path.name}")
                except OSError as e:
                    log_error(f"{instance_log_prefix}: Auto-save failed for text file {text_filename_path.name}: {e}")
                    self.status_update.emit("Auto-save failed for text.", 4000)
            else:
                log_debug(f"{instance_log_prefix}: Save Text File disabled. Skipping.")

        except Exception as e:
            log_error(f"{instance_log_prefix}: Error during auto-save process: {e}", exc_info=True)
            self.status_update.emit("Error during auto-save.", 4000)





    def resizeEvent(self, event):
        """Rescales the displayed image when the widget is resized."""
        super().resizeEvent(event)
        # Rescale the image currently in the label, using the stored original if available
        self._scale_and_set_pixmap() # Use helper method
        
        
    @pyqtSlot()
    def _handle_generate_button_click(self):
         """Handles clicks on the main action button (Generate / Stop Loop)."""
         # Use the internal _continuous_loop_active flag to check if we are currently in a loop state.
         # This flag is set TRUE when the user clicks "Start" with the Continuous checkbox checked,
         # and set FALSE when the loop sequence finishes, encounters an error, or is stopped by the user.
         if self._continuous_loop_active:
             # If loop is active (or intended to be), button click means "Stop Loop"
             log_info("SingleMode: Stop Loop requested by user.")
             self._stop_loop() # Call the method to stop the loop
         elif not self._is_running: # If not running, it means it's idle and ready to start
             # If idle, button click means "Start Generation"
             # Check the state of the continuous checkbox to determine if the loop should be activated
             should_loop = self.continuous_checkbox.isChecked()
             log_info(f"SingleMode: Start Generation requested by user (Loop={should_loop}).")
             if should_loop:
                  # If continuous is checked, activate the loop flag BEFORE starting the first generation.
                  # The _on_generation_finished method will then check this flag to schedule the next run.
                  self._continuous_loop_active = True
                  log_debug("SingleMode: Continuous loop activated based on checkbox state.")
             # Call the main start generation method for the first (or only) run.
             # This method contains all input validation, worker setup, and UI state updates.
             self.start_generation()
         else:
             # This state (worker is running, but loop flag is OFF) should theoretically
             # be handled by the dedicated Cancel button when it's visible.
             # Log a warning if this happens unexpectedly, but no action needed here as the
             # button should be disabled when _is_running is True and _continuous_loop_active is False.
             log_warning("SingleMode: Generate button clicked unexpectedly while worker running but loop not active?")

    def _stop_loop(self):
        """Deactivates the continuous loop flag and cancels any current run."""
        instance_id_str = "SingleMode" # Adjusted log prefix
        if self._continuous_loop_active:
            log_info(f"{instance_id_str}: Deactivating continuous loop.")
            self._continuous_loop_active = False
            # Use the correct signal for status updates in Single Mode
            self.status_update.emit("Loop stopped.", 3000) # <-- CORRECTED LINE
            self._update_single_mode_button_states() # Update button state
            self.save_prompt_button.setVisible(False) # Hide save button

            # Also cancel any currently running generation
            self._cancel_generation() # cancel_generation now handles display clearing

            # If loop was stopped but worker wasn't running (between runs), ensure UI resets
            if not self._is_running:
                self._set_ui_generating(False)
                # Clear display explicitly if stopped between runs
                self.result_text_edit.clear()
                self.result_text_edit.setPlaceholderText("[Loop Stopped]")
                self.result_image_label.clear()
                self.result_image_label.setText("Loop stopped.")
                self._full_result_pixmap = None
                # Status already emitted above
        else:
            log_debug(f"{instance_id_str}: Stop loop called but loop was not active.")
            self._set_ui_generating(False) # Ensure UI is idle just in case
            

    @pyqtSlot()
    def _save_current_prompt(self):
        """
        Saves the current prompt text from the editor and the last generated image thumbnail
         as a BRAND NEW prompt entry. Does not attempt to update existing slots.
        """
        log_info("Attempting to save current prompt text and last generated thumbnail as a NEW prompt entry...")

        # Check if we have the necessary data (image bytes are essential for thumbnail)
        if self._last_image_bytes is None:
            log_warning("No last generated image data available to save thumbnail.")
            show_warning_message(self, "Save Error", "No generated image found from the last successful run to create a thumbnail.")
            self.save_prompt_button.setVisible(False) # Ensure button is hidden if data is missing
            return
        # Note: We save the *editor* text, not _last_resolved_prompt, as the user might have edited it.
        current_editor_text = self.prompt_text_edit.toPlainText().strip()
        if not current_editor_text:
             log_warning("Prompt text editor is empty, cannot save a new prompt.")
             show_warning_message(self, "Save Error", "Prompt text is empty, cannot save.")
             self.save_prompt_button.setVisible(False)
             return

        # Determine a default name for the new prompt entry
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        default_name = f"Saved_{timestamp}"

        # Call PromptService to add a *new* prompt entry with text and thumbnail
        log_info(f"Adding new prompt '{default_name}' with thumbnail using current editor text.")
        new_slot_key = self.prompt_service.add_new_prompt_with_thumbnail(
             name=default_name,
             text=current_editor_text, # Save text from the editor
             image_bytes=self._last_image_bytes # Use the stored image bytes for thumbnail creation
         )

        if new_slot_key:
             show_info_message(self, "Prompt Saved", f"Prompt saved successfully as '{default_name}' ({new_slot_key}).\n\nFind it in the Prompt Manager.")
             # Do NOT update _loaded_prompt_slot_key here, this button always creates a new prompt.
        else:
             show_error_message(self, "Save Error", "Failed to add new prompt with thumbnail. See logs.")
             self._update_status_label("Save Failed.")
             QTimer.singleShot(3000, lambda: self._update_status_label("Ready.") if not self._is_running else None)


        # Reset last result state *after* attempting to save
        # This ensures the button is hidden after it's clicked, regardless of save success
        self._last_resolved_prompt = None
        self._last_image_bytes = None
        self._last_image_mime = None
        self.save_prompt_button.setVisible(False) # Hide save button after action
        
        
        
    def _update_button_style(self):
         """Updates the Generate/Cancel/Stop Loop button states and text."""
         # Determine if configuration is valid enough to allow starting a new run
         # This depends on having a selected API key and a selected model.
         can_start_new = not (self._is_running or self._continuous_loop_active) and \
                         bool(self._current_api_key_name) and \
                         self.model_combo.count() > 0 and \
                         self.model_combo.currentIndex() >= 0 # Ensure a model is actually selected


         if self._continuous_loop_active:
             # Loop is active (or intended to be, even if worker is currently idle between runs)
             self.generate_button.setText("Stop Loop")
             self.generate_button.setToolTip("Stop the continuous generation loop.")
             self.generate_button.setChecked(True) # Use checked state to indicate active loop/run
             # The Stop Loop button must be ENABLED when the loop is active to be clickable
             self.generate_button.setEnabled(True)
             # In loop mode, the Cancel button is not used and should be hidden or disabled
             self.cancel_button.setEnabled(False)
             self.cancel_button.setVisible(False)
         elif self._is_running:
             # Worker is running, but loop is not active (single run in progress)
             self.generate_button.setText("Generate") # Button text doesn't change, just disabled
             self.generate_button.setToolTip("Generation in progress...")
             self.generate_button.setChecked(True) # Checked when running
             # The Generate button is disabled during a single run
             self.generate_button.setEnabled(False)
             # The Cancel button is enabled during a single run
             self.cancel_button.setText("Cancel")
             self.cancel_button.setToolTip("Cancel the current generation.")
             self.cancel_button.setEnabled(True)
             self.cancel_button.setVisible(True) # Ensure visible


         else:
             # Idle state (worker not running, loop not active)
             self.generate_button.setText("Generate")
             self.generate_button.setToolTip("Start generation (check 'Continuous' to loop).")
             self.generate_button.setChecked(False) # Unchecked when idle
             # Enable generate button only if configuration is valid
             self.generate_button.setEnabled(can_start_new)
             # In idle state, Cancel button is disabled but kept visible
             self.cancel_button.setText("Cancel")
             self.cancel_button.setToolTip("Cancel the current generation.") # Default tooltip
             self.cancel_button.setEnabled(False)
             self.cancel_button.setVisible(True) # Keep visible but disabled


         # Ensure tooltip reflects *why* it might be disabled if not enabled
         if not self.generate_button.isEnabled() and not (self._is_running or self._continuous_loop_active):
              if not self._current_api_key_name:
                   self.generate_button.setToolTip("Select an API Key to enable generation.")
              elif self.model_combo.count() == 0 or self.model_combo.currentIndex() < 0:
                   self.generate_button.setToolTip("Select a Model to enable generation.")
              # Add other checks if needed (e.g., empty prompt, no images in sequential mode)
              # Check if sequential mode is enabled AND the image queue is empty
              elif self._sequential_mode_enabled and not self._sequential_image_queue:
                   self.generate_button.setToolTip("Add images for sequential processing.")
              # Check if standard mode is enabled AND no images are selected
              elif not self._sequential_mode_enabled and not self._selected_image_paths and self.add_image_button.isEnabled():
                  # Only show this tip if the model actually supports images
                   self.generate_button.setToolTip("Add images to include with the prompt (Model supports images).")
              # Check if prompt is empty
              elif not self.prompt_text_edit.toPlainText().strip():
                   self.generate_button.setToolTip("Enter a prompt to enable generation.")
              # Generic fallback if none of the above
              # else:
              #      self.generate_button.setToolTip("Configuration required to enable generation.")