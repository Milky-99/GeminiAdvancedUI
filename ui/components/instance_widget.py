# -*- coding: utf-8 -*-
import re
import time
import mimetypes
import copy
import webbrowser
import os
import platform # Needed for fallback open method
import subprocess # Needed for fallback open method
from pathlib import Path
from typing import Optional, List, Dict, Any

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox, QComboBox,
    QDoubleSpinBox, QSpinBox, QPlainTextEdit, QCheckBox, QFileDialog, QSplitter,
    QSpacerItem, QSizePolicy, QProgressBar, QGridLayout, QFrame, QMessageBox, QApplication, QDialog, QLineEdit
)
from PyQt6.QtCore import pyqtSignal, Qt, QThread, QObject, pyqtSlot, QSize, QTimer, QEvent, QMetaObject, QCoreApplication 
from PyQt6.QtGui import QPixmap, QImage, QIcon

# --- Project Imports ---
from utils import constants
from utils.logger import log_info, log_debug, log_error, log_warning
from utils.helpers import show_error_message, show_info_message, show_warning_message, get_themed_icon
from core.settings_service import SettingsService
from core.api_key_service import ApiKeyService
from core.prompt_service import PromptService
from core.wildcard_resolver import WildcardResolver
from core.gemini_handler import GeminiHandler, SDK_AVAILABLE
# Import SafetySettingsDialog if needed (or handle via main window)
from ui.safety_settings_dialog import SafetySettingsDialog
from core.image_processor import ImageProcessor
# Import the worker thread (can likely reuse the one from single_mode_widget)
from ui.single_mode_widget import GenerationWorker # Reuse the worker
from core.filename_generator import FilenameGeneratorService




class ThumbnailLoaderWorker(QObject):
    finished = pyqtSignal(str, QPixmap) # slot_key, pixmap
    error = pyqtSignal(str, str)      # slot_key, error_message

    def __init__(self, slot_key: str, image_path: Path, target_size: QSize):
        super().__init__()
        self.slot_key = slot_key
        self.image_path = image_path
        self.target_size = target_size
        self._is_cancelled = False

    def run(self):
        try:
            if self._is_cancelled:
                self.error.emit(self.slot_key, "Thumbnail load cancelled before start.")
                return

            log_debug(f"ThumbnailLoaderWorker ({self.slot_key}): Starting for {self.image_path.name}")
            thumb_bytes = ImageProcessor.create_thumbnail_bytes(self.image_path, 
                                                                size=(self.target_size.width(), self.target_size.height()))
            if self._is_cancelled: # Check after potentially slow operation
                self.error.emit(self.slot_key, "Thumbnail load cancelled during processing.")
                return

            if thumb_bytes:
                qimage = QImage.fromData(thumb_bytes)
                if not qimage.isNull():
                    pixmap = QPixmap.fromImage(qimage)
                    # Scaling to target_size is already handled by create_thumbnail_bytes
                    # but we can ensure it fits the label via a final scale if desired,
                    # or rely on the label's setScaledContents (though explicit scaling is often better).
                    # For now, assume create_thumbnail_bytes gives us the right size.
                    self.finished.emit(self.slot_key, pixmap)
                    log_debug(f"ThumbnailLoaderWorker ({self.slot_key}): Finished successfully.")
                else:
                    self.error.emit(self.slot_key, "Failed to create QImage from thumbnail bytes.")
            else:
                self.error.emit(self.slot_key, "Failed to create thumbnail bytes.")
        except Exception as e:
            log_error(f"ThumbnailLoaderWorker ({self.slot_key}): Error: {e}", exc_info=True)
            self.error.emit(self.slot_key, f"Error loading thumbnail: {e}")
    
    def cancel(self):
        self._is_cancelled = True
        log_debug(f"ThumbnailLoaderWorker ({self.slot_key}): Cancellation requested.")








class InstanceWidget(QWidget):
    """Widget representing a single API instance in Multi Mode."""

    # Signals for MultiModeWidget
    request_delete = pyqtSignal(int) # instance_id
    status_update = pyqtSignal(str, int) # message, timeout (relay to main window)
    # Signals to reliably track running state for MultiModeWidget's counts/buttons
    generation_started = pyqtSignal(int) # instance_id
    generation_finished = pyqtSignal(int) # instance_id
    request_new_key = pyqtSignal(int) # instance_id
    INSTANCE_WIDGET_FIXED_HEIGHT = 720


    def __init__(self,
                 instance_id: int,
                 settings_service: SettingsService,
                 api_key_service: ApiKeyService,
                 prompt_service: PromptService,
                 wildcard_resolver: WildcardResolver,
                 gemini_handler: GeminiHandler, # Shared handler
                 initial_api_key_name: Optional[str] = None,
                 parent=None):
        super().__init__(parent)
        self.instance_id = instance_id
        self.settings_service = settings_service
        self.api_key_service = api_key_service
        self.prompt_service = prompt_service
        self.wildcard_resolver = wildcard_resolver
        self.gemini_handler = gemini_handler # Shared instance

        self._is_running = False # Internal state for worker activity
        self._continuous_loop_active = False # Flag for user intent to loop
        self._auto_save_enabled = False
        self._current_api_key_name: Optional[str] = None
        self._current_api_key_value: Optional[str] = None # Store actual key value
        self._selected_image_paths: List[Path] = []
        self._current_safety_settings: Optional[Dict[Any, Any]] = None # Store safety settings locally
        self._full_result_pixmap: Optional[QPixmap] = None # For resizing
        self._custom_save_path: Optional[Path] = None
        self._thumbnail_image_path: Optional[Path] = None
        self._thumbnail_loaded: bool = False
        self._initial_api_key_name = initial_api_key_name

        self._last_resolved_prompt: Optional[str] = None
        self._last_image_bytes: Optional[bytes] = None
        self._last_image_mime: Optional[str] = None

        self._worker_thread = QThread(self)
        self._worker_thread.setObjectName(f"WorkerThread_{self.instance_id}")
        self._worker_thread.start() 
        self._generation_worker: Optional[GenerationWorker] = None
        
        
        self._thumbnail_loader_thread: Optional[QThread] = None
        self._thumbnail_loader_worker: Optional[ThumbnailLoaderWorker] = None
        self._current_thumbnail_path_loading: Optional[Path] = None # Track path being loaded       
        
        
        self._loop_timer = QTimer(self)
        self._loop_timer.setSingleShot(True)

        # --- Set Fixed Height for the InstanceWidget ---
        self.setFixedHeight(self.INSTANCE_WIDGET_FIXED_HEIGHT)
        # --- End Set Fixed Height ---

        self._setup_ui()
        self._connect_signals()
        self._load_initial_data()
        self.installEventFilter(self) # Filter self for wheel events potentially
        self.filename_generator = FilenameGeneratorService(self.settings_service)
        
        # Install event filters for specific child widgets
        self.temperature_spin.installEventFilter(self)
        self.top_p_spin.installEventFilter(self)
        self.max_tokens_spin.installEventFilter(self)
        self.api_key_combo.installEventFilter(self)
        self.model_combo.installEventFilter(self)
        self.prompt_combo.installEventFilter(self)
        log_debug(f"Instance {self.instance_id}: Event filters installed.")
        
        self._sequential_mode_enabled: bool = False
        self._sequential_image_queue: List[Path] = []
        self._sequential_current_index: int = -1
        self._loaded_prompt_slot_key: Optional[str] = None



    def _setup_ui(self):
        """Set up the UI elements for this instance with Left/Right layout and full-height image."""
        self.setObjectName(f"instanceWidget_{self.instance_id}") 
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setObjectName(f"mainLayoutInstance_{self.instance_id}")
        self.main_layout.setContentsMargins(5, 5, 5, 5)
        self.main_layout.setSpacing(3)

        top_bar_layout = QHBoxLayout()
        top_bar_layout.setObjectName(f"topBarLayoutInstance_{self.instance_id}")
        self.id_label = QLabel(f"<b>Instance #{self.instance_id}</b>")
        self.id_label.setObjectName(f"idLabelInstance_{self.instance_id}")
        self.remove_button = QPushButton("Remove")
        self.remove_button.setObjectName(f"removeButtonInstance_{self.instance_id}")
        self.remove_button.setToolTip("Remove this instance.")
        self.remove_button.setMaximumWidth(80)
        top_bar_layout.addWidget(self.id_label)
        top_bar_layout.addStretch(1)
        top_bar_layout.addWidget(self.remove_button)
        self.main_layout.addLayout(top_bar_layout)

        main_frame = QFrame()
        main_frame.setObjectName(f"mainFrameInstance_{self.instance_id}")
        main_frame.setFrameShape(QFrame.Shape.StyledPanel)
        main_frame_layout = QHBoxLayout(main_frame)
        main_frame_layout.setObjectName(f"mainFrameLayoutInstance_{self.instance_id}")
        main_frame_layout.setContentsMargins(5, 5, 5, 5)
        main_frame_layout.setSpacing(8)

        # --- Left Panel ---
        left_panel_widget = QWidget()
        left_panel_widget.setObjectName(f"leftPanelWidgetInstance_{self.instance_id}")
        left_panel_layout = QVBoxLayout(left_panel_widget) # Main vertical layout for the left side
        left_panel_layout.setObjectName(f"leftPanelLayoutInstance_{self.instance_id}")
        left_panel_layout.setContentsMargins(0, 0, 0, 0)
        left_panel_layout.setSpacing(5)

        # --- Config Group (Stays at the top of left panel) ---
        config_group = QGroupBox("Config")
        config_group.setObjectName(f"configGroupInstance_{self.instance_id}")
        config_grid_layout = QGridLayout(config_group)
        config_grid_layout.setObjectName(f"configGridLayoutInstance_{self.instance_id}")
        config_grid_layout.setSpacing(5)

        config_grid_layout.addWidget(QLabel("API Key:"), 0, 0)
        self.api_key_combo = QComboBox()
        self.api_key_combo.setObjectName(f"apiKeyComboInstance_{self.instance_id}")
        self.api_key_combo.setToolTip(f"Select API Key for Instance #{self.instance_id}")
        config_grid_layout.addWidget(self.api_key_combo, 0, 1, 1, 2)
        self.manage_keys_button = QPushButton("Mng")
        self.manage_keys_button.setObjectName(f"manageKeysButtonInstance_{self.instance_id}")
        self.manage_keys_button.setToolTip("Open API Key Manager (Global)")
        self.manage_keys_button.setMaximumWidth(40)
        config_grid_layout.addWidget(self.manage_keys_button, 0, 3)

        config_grid_layout.addWidget(QLabel("Model:"), 1, 0)
        self.model_combo = QComboBox()
        self.model_combo.setObjectName(f"modelComboInstance_{self.instance_id}")
        self.model_combo.setToolTip(f"Select Model for Instance #{self.instance_id}")
        self.model_combo.setEnabled(False)
        config_grid_layout.addWidget(self.model_combo, 1, 1, 1, 2)
        self.refresh_models_button = QPushButton("Rfr")
        self.refresh_models_button.setObjectName(f"refreshModelsButtonInstance_{self.instance_id}")
        self.refresh_models_button.setToolTip("Refresh models for this key")
        self.refresh_models_button.setMaximumWidth(40)
        self.refresh_models_button.setEnabled(False)
        config_grid_layout.addWidget(self.refresh_models_button, 1, 3)

        config_grid_layout.addWidget(QLabel("T:"), 2, 0)
        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setObjectName(f"temperatureSpinInstance_{self.instance_id}")
        self.temperature_spin.setRange(0.0, 2.0); self.temperature_spin.setSingleStep(0.05); self.temperature_spin.setDecimals(2)
        self.temperature_spin.setToolTip("Temperature")
        config_grid_layout.addWidget(self.temperature_spin, 2, 1)

        config_grid_layout.addWidget(QLabel("P:"), 2, 2)
        self.top_p_spin = QDoubleSpinBox()
        self.top_p_spin.setObjectName(f"topPSpinInstance_{self.instance_id}")
        self.top_p_spin.setRange(0.0, 1.0); self.top_p_spin.setSingleStep(0.05); self.top_p_spin.setDecimals(2)
        self.top_p_spin.setToolTip("Top P")
        config_grid_layout.addWidget(self.top_p_spin, 2, 3)

        config_grid_layout.addWidget(QLabel("MaxT:"), 3, 0)
        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setObjectName(f"maxTokensSpinInstance_{self.instance_id}")
        self.max_tokens_spin.setRange(1, 8192); self.max_tokens_spin.setSingleStep(1)
        self.max_tokens_spin.setToolTip("Max Output Tokens")
        config_grid_layout.addWidget(self.max_tokens_spin, 3, 1)

        self.reset_params_button = QPushButton("Reset")
        self.reset_params_button.setObjectName(f"resetParamsButtonInstance_{self.instance_id}")
        self.reset_params_button.setToolTip("Reset Temp, Top P, Max Tokens")
        config_grid_layout.addWidget(self.reset_params_button, 3, 2)

        self.safety_button = QPushButton("Safety")
        self.safety_button.setObjectName(f"safetyButtonInstance_{self.instance_id}")
        self.safety_button.setToolTip("Configure Safety Settings for this Instance")
        self.safety_button.setEnabled(SDK_AVAILABLE)
        config_grid_layout.addWidget(self.safety_button, 3, 3)
        
        config_grid_layout.addWidget(QLabel("Save Path:"), 4, 0)
        self.save_path_edit = QLineEdit()
        self.save_path_edit.setObjectName(f"savePathEditInstance_{self.instance_id}")
        self.save_path_edit.setPlaceholderText("[Default Output Path]")
        self.save_path_edit.setReadOnly(True)
        self.save_path_edit.setToolTip(f"Custom output directory for instance {self.instance_id}. Default: {constants.DATA_DIR / 'output'}")
        config_grid_layout.addWidget(self.save_path_edit, 4, 1, 1, 1)

        save_path_button_layout = QHBoxLayout()
        save_path_button_layout.setObjectName(f"savePathButtonLayoutInstance_{self.instance_id}")
        save_path_button_layout.setContentsMargins(0,0,0,0); save_path_button_layout.setSpacing(2)
        self.browse_save_path_button = QPushButton("...")
        self.browse_save_path_button.setObjectName(f"browseSavePathButtonInstance_{self.instance_id}")
        self.browse_save_path_button.setToolTip("Browse for custom save directory")
        self.browse_save_path_button.setMaximumWidth(30)
        save_path_button_layout.addWidget(self.browse_save_path_button)
        self.clear_save_path_button = QPushButton("X")
        self.clear_save_path_button.setObjectName(f"clearSavePathButtonInstance_{self.instance_id}")
        self.clear_save_path_button.setToolTip("Clear custom path (use default)")
        self.clear_save_path_button.setMaximumWidth(30)
        self.clear_save_path_button.setEnabled(False)
        save_path_button_layout.addWidget(self.clear_save_path_button)
        
        self.open_save_folder_button = QPushButton()
        self.open_save_folder_button.setObjectName(f"openSaveFolderButtonInstance_{self.instance_id}")
        self.open_save_folder_button.setIcon(get_themed_icon("folder_open.png"))
        self.open_save_folder_button.setIconSize(QSize(16,16))
        self.open_save_folder_button.setToolTip(f"Open Instance {self.instance_id} output folder")
        self.open_save_folder_button.setMaximumWidth(30)
        save_path_button_layout.addWidget(self.open_save_folder_button)       
        save_path_button_layout.addStretch(1)
        config_grid_layout.addLayout(save_path_button_layout, 4, 2, 1, 2)
        config_grid_layout.setColumnStretch(1, 1) 
        left_panel_layout.addWidget(config_group) # Add Config Group to Left Panel

        # --- Input Group (Prompt only) ---
        prompt_input_group = QGroupBox("Input Prompt") # Changed title
        prompt_input_group.setObjectName(f"promptInputGroupInstance_{self.instance_id}")
        prompt_input_layout = QVBoxLayout(prompt_input_group) # QVBoxLayout for this group
        prompt_input_layout.setObjectName(f"promptInputLayoutInstance_{self.instance_id}")
        prompt_input_layout.setSpacing(3)

        prompt_controls_layout = QHBoxLayout()
        prompt_controls_layout.setObjectName(f"promptControlsLayoutInstance_{self.instance_id}")
        self.prompt_combo = QComboBox()
        self.prompt_combo.setObjectName(f"promptComboInstance_{self.instance_id}")
        self.prompt_combo.addItem("- Load Prompt -", "")
        self.prompt_combo.setToolTip("Load a saved prompt")
        prompt_controls_layout.addWidget(self.prompt_combo, 1)
        self.manage_prompts_button = QPushButton("Mng")
        self.manage_prompts_button.setObjectName(f"managePromptsButtonInstance_{self.instance_id}")
        self.manage_prompts_button.setToolTip("Open Prompt Manager (Global)")
        self.manage_prompts_button.setMaximumWidth(40)
        prompt_controls_layout.addWidget(self.manage_prompts_button)
        prompt_input_layout.addLayout(prompt_controls_layout)

        self.prompt_text_edit = QPlainTextEdit()
        self.prompt_text_edit.setObjectName(f"promptTextEditInstance_{self.instance_id}")
        self.prompt_text_edit.setPlaceholderText(f"Instance #{self.instance_id} Prompt...")
        self.prompt_text_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding) # Make it expand
        self.prompt_text_edit.setMinimumHeight(150) 
        prompt_input_layout.addWidget(self.prompt_text_edit, 1) # Stretch factor 1 within its group

        left_panel_layout.addWidget(prompt_input_group) # Add Input Group to Left Panel

        # --- Image Controls (Separate QHBoxLayout, below Input Group) ---
        image_controls_standalone_layout = QHBoxLayout()
        image_controls_standalone_layout.setObjectName(f"imageControlsStandaloneLayoutInstance_{self.instance_id}")
        
        self.add_image_button = QPushButton("Img+")
        self.add_image_button.setObjectName(f"addImageButtonInstance_{self.instance_id}")
        self.add_image_button.setToolTip("Add Image(s)")
        self.add_image_button.setEnabled(False)
        image_controls_standalone_layout.addWidget(self.add_image_button)

        self.clear_images_button = QPushButton("Clr")
        self.clear_images_button.setObjectName(f"clearImagesButtonInstance_{self.instance_id}")
        self.clear_images_button.setToolTip("Clear Images")
        self.clear_images_button.setEnabled(False)
        image_controls_standalone_layout.addWidget(self.clear_images_button)

        self.sequential_image_checkbox = QCheckBox("Seq")
        self.sequential_image_checkbox.setObjectName(f"sequentialImageCheckboxInstance_{self.instance_id}")
        self.sequential_image_checkbox.setToolTip("Process selected images sequentially one by one with the prompt.")
        image_controls_standalone_layout.addWidget(self.sequential_image_checkbox)
        
        self.image_label = QLabel("No Img")
        self.image_label.setObjectName(f"imageStatusLabelInstance_{self.instance_id}")
        self.image_label.setToolTip("Selected image status")
        font_metrics_img_label = self.image_label.fontMetrics() # Use different var name
        self.image_label.setMinimumWidth(font_metrics_img_label.horizontalAdvance("Seq: 100/100") + 10) 
        self.image_label.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Preferred) 
        image_controls_standalone_layout.addWidget(self.image_label, 1) 
        
        left_panel_layout.addLayout(image_controls_standalone_layout) # Add Image Controls to Left Panel

        # --- Text Result (Directly in Left Panel) ---
        left_panel_layout.addWidget(QLabel("Text Result:"))
        self.result_text_edit = QPlainTextEdit()
        self.result_text_edit.setObjectName(f"resultTextEditInstance_{self.instance_id}")
        self.result_text_edit.setReadOnly(True)
        self.result_text_edit.setPlaceholderText(f"Text result...")
        self.result_text_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding) # Make it expand
        self.result_text_edit.setMinimumHeight(80) 
        left_panel_layout.addWidget(self.result_text_edit, 1) # Give it stretch relative to other expanding widgets in left_panel_layout

        # --- Save Prompt Button (Directly in Left Panel) ---
        self.save_prompt_button = QPushButton("Save Prompt && Thumb")
        self.save_prompt_button.setObjectName(f"savePromptButtonInstance_{self.instance_id}")
        self.save_prompt_button.setToolTip(f"Save Instance {self.instance_id}'s last generated prompt and image thumbnail.")
        self.save_prompt_button.setVisible(False)
        left_panel_layout.addWidget(self.save_prompt_button)

        # --- Action Layout (Stays at the bottom of Left Panel) ---
        action_layout = QHBoxLayout()
        action_layout.setObjectName(f"actionLayoutInstance_{self.instance_id}")
        self.start_stop_button = QPushButton("Start")
        self.start_stop_button.setObjectName("startStopButtonInstance")
        self.start_stop_button.setStyleSheet("QPushButton { font-weight: bold; } QPushButton:checked { background-color: #ffaaaa; }")
        self.start_stop_button.setToolTip("Start/Stop Generation for this Instance")
        self.start_stop_button.setCheckable(True)
        self.start_stop_button.setEnabled(False)
        
        self.continuous_checkbox = QCheckBox("Cont.")
        self.continuous_checkbox.setObjectName(f"continuousCheckboxInstance_{self.instance_id}")
        self.continuous_checkbox.setToolTip("Enable continuous generation for this instance.")
        
        self.autosave_checkbox = QCheckBox("AS")
        self.autosave_checkbox.setObjectName(f"autosaveCheckboxInstance_{self.instance_id}")
        self.autosave_checkbox.setToolTip("Enable auto-save for this instance.")
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName(f"progressBarInstance_{self.instance_id}")
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 0); self.progress_bar.setTextVisible(False); self.progress_bar.setMaximumHeight(10)
        
        self.status_label = QLabel("Ready.")
        self.status_label.setObjectName("instanceStatusLabel")
        self.status_label.setStyleSheet("font-size: 9pt; color: grey;")
        font_metrics_status_label = self.status_label.fontMetrics() # Use different var name
        self.status_label.setMinimumWidth(font_metrics_status_label.horizontalAdvance("Rate Limit 'Long Key Name'. Requesting key...") + 5) 
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        
        action_layout.addWidget(self.start_stop_button)
        action_layout.addWidget(self.continuous_checkbox)
        action_layout.addWidget(self.autosave_checkbox)
        action_layout.addWidget(self.progress_bar, 1)
        action_layout.addWidget(self.status_label, 2) 
        left_panel_layout.addLayout(action_layout) # Add Action Layout to Left Panel

        # --- Right Panel (Image Display - Unchanged) ---
        right_panel_widget = QWidget()
        right_panel_widget.setObjectName(f"rightPanelWidgetInstance_{self.instance_id}")
        right_panel_layout = QVBoxLayout(right_panel_widget)
        right_panel_layout.setObjectName(f"rightPanelLayoutInstance_{self.instance_id}")
        right_panel_layout.setContentsMargins(0, 0, 0, 0)
        right_panel_layout.setSpacing(0)

        self.result_image_label = QLabel("No Img / Result")
        self.result_image_label.setObjectName(f"resultImageLabelInstance_{self.instance_id}")
        self.result_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_image_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding) 
        self.result_image_label.setMinimumSize(QSize(100, 100)) 
        self.result_image_label.setStyleSheet("QLabel { background-color: #e0e0e0; border: 1px solid grey; color: grey; font-size: 9pt; }")
        self.result_image_label.setScaledContents(False)
        right_panel_layout.addWidget(self.result_image_label, 1)

        # --- Add Left and Right Panels to Main Frame ---
        main_frame_layout.addWidget(left_panel_widget, 2) # Give left panel more horizontal stretch
        main_frame_layout.addWidget(right_panel_widget, 1)
        self.main_layout.addWidget(main_frame)



    def get_instance_id(self) -> int:
        return self.instance_id

    def is_running(self) -> bool:
        # Crucial method for MultiModeWidget
        return self._is_running




    @pyqtSlot()
    def _try_start_next_instance_generation(self):
         """Checks if the loop is still active AND the worker is not running before starting the next run."""
         # --- CRITICAL CHECK ---
         # Check both the loop flag AND the worker running state.
         if self._continuous_loop_active and not self._is_running:
             log_debug(f"Instance {self.instance_id}: Timer fired, loop active, worker idle. Starting next generation.")
             # Directly call start_generation as it contains all necessary validation and startup logic.
             self.start_generation()
         elif self._continuous_loop_active and self._is_running:
              log_warning(f"Instance {self.instance_id}: Timer fired, loop active, but worker is STILL running! Skipping start.")
              # This prevents recursion if something went wrong with state updates
              # Update UI just in case it's out of sync
              self._set_ui_generating(True)
         else:
             log_info(f"Instance {self.instance_id}: Timer fired, but loop inactive or worker running. Not starting next generation.")
             # Ensure UI is fully idle if the loop was stopped while timer was pending
             self._set_ui_generating(False)




    def get_status(self) -> str:
        """Returns the current operational status of the instance."""
        if self._is_running:
            return "Running"
        elif self._continuous_loop_active:
            return "Looping"
        else:
            return "Idle"

    def get_api_key_name(self) -> str:
        """Returns the name of the API key currently assigned."""
        return self._current_api_key_name or "None"

    def get_prompt_start(self, length: int = 30) -> str:
        """Returns the beginning of the current prompt text."""
        return self.prompt_text_edit.toPlainText()[:length].replace('\n', ' ') + ("..." if len(self.prompt_text_edit.toPlainText()) > length else "")


    def _connect_signals(self):
        self.remove_button.clicked.connect(self._remove_self)
        self.api_key_combo.currentIndexChanged.connect(self._on_api_key_selected)
        self.model_combo.currentIndexChanged.connect(self._on_model_selected)
        self.refresh_models_button.clicked.connect(self._refresh_models)
        self.manage_keys_button.clicked.connect(self._open_api_keys_external)
        self.manage_prompts_button.clicked.connect(self._open_prompts_external)
        self.continuous_checkbox.toggled.connect(self._set_internal_continuous)
        self.autosave_checkbox.toggled.connect(self._set_internal_autosave)
        if SDK_AVAILABLE:
            self.safety_button.clicked.connect(self._configure_safety)

        self.prompt_combo.currentIndexChanged.connect(self._load_selected_prompt)
        self.add_image_button.clicked.connect(self._add_images)
        self.clear_images_button.clicked.connect(self._clear_images)
        self.browse_save_path_button.clicked.connect(self._browse_save_path)
        self.clear_save_path_button.clicked.connect(self._clear_save_path)
        self.start_stop_button.clicked.connect(self._handle_start_stop_click)
        self.reset_params_button.clicked.connect(self._reset_parameters)
        self.sequential_image_checkbox.toggled.connect(self._on_sequential_mode_toggled)
        self.save_prompt_button.clicked.connect(self._save_current_prompt) # Added this line

        # Keep timer connection for scheduling next run
        self._loop_timer.timeout.connect(self._try_start_next_instance_generation)
        self.open_save_folder_button.clicked.connect(self._open_output_folder)

    def set_continuous(self, checked: bool):
        """Sets the continuous mode state and updates the checkbox."""
        self.continuous_checkbox.setChecked(checked)
        # The toggled signal will call _set_internal_continuous

    def set_autosave(self, checked: bool):
        """Sets the auto-save state and updates the checkbox."""
        self.autosave_checkbox.setChecked(checked)
        # The toggled signal will call _set_internal_autosave

    @pyqtSlot(bool)
    def _set_internal_continuous(self, checked):
        self._continuous_mode = checked
        log_debug(f"Instance {self.instance_id}: Continuous mode set to {checked}")

    @pyqtSlot(bool)
    def _set_internal_autosave(self, checked):
        self._auto_save_enabled = checked
        log_debug(f"Instance {self.instance_id}: Auto-save set to {checked}")

    def _load_initial_data(self):
        """Load initial settings, keys, prompts, etc."""
        log_debug(f"Instance {self.instance_id}: Loading initial data.")
        self.apply_global_settings() # Load defaults like temp, top_p, safety etc. first

        self.model_combo.setEnabled(False)
        self.start_stop_button.setEnabled(False)
        self.refresh_models_button.setEnabled(False)
        self.add_image_button.setEnabled(False)
        self.clear_images_button.setEnabled(False)
        # --------------------------------------------------
        self.save_path_edit.clear() # Ensure it's clear initially
        self.save_path_edit.setPlaceholderText("[Default Output Path]")
        self.clear_save_path_button.setEnabled(False)
        self._custom_save_path = None # Ensure internal state is reset
        # Load keys AFTER setting initial disabled state
        self.update_api_key_list() # This will trigger _on_api_key_selected if a key is selected/assigned
        self.update_prompt_list()

    @pyqtSlot()
    def _open_output_folder(self):
        """Opens the current output directory for this instance."""
        instance_log_prefix = f"Instance {self.instance_id}"
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

            # Ensure the directory exists
            path_to_open.mkdir(parents=True, exist_ok=True)
            log_debug(f"{instance_log_prefix}: Ensured directory exists: {path_to_open}")

            # Open the directory using webbrowser
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

    @pyqtSlot(str)
    def _update_status_label(self, message: str):
        """Updates the instance-specific status label and sets a dynamic property."""
        self.status_label.setText(message)

        # Determine the state based on the message content
        prop_value = "default" # Default state
        msg_lower = message.lower()
        if "error" in msg_lower or "fail" in msg_lower:
            prop_value = "error"
        elif "limit" in msg_lower or "block" in msg_lower or "cancel" in msg_lower or "warn" in msg_lower:
             prop_value = "warning"
        elif "success" in msg_lower or "ready" in msg_lower or "complete" in msg_lower or "saved" in msg_lower:
             prop_value = "success"
        elif "..." in msg_lower or "generat" in msg_lower or "load" in msg_lower or "refresh" in msg_lower:
             prop_value = "busy"


        # Set the custom property on the label
        self.status_label.setProperty("statusState", prop_value)

        # Force Qt to re-evaluate the style sheet for this widget
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        # Ensure the label updates visually immediately (sometimes needed)
        # self.status_label.update() # Usually unpolish/polish is enough


    @pyqtSlot()
    def _reset_parameters(self):
        """Resets Temperature, Top P, and Max Tokens to their default values."""
        log_info(f"Instance {self.instance_id}: Resetting parameters to defaults.")
        self.temperature_spin.setValue(constants.DEFAULT_TEMPERATURE)
        self.top_p_spin.setValue(constants.DEFAULT_TOP_P)
        self.max_tokens_spin.setValue(constants.DEFAULT_MAX_OUTPUT_TOKENS)
        self._update_status_label("Params reset.")
        # Reset status label back to Ready after a delay
        QTimer.singleShot(3000, lambda: self._update_status_label("Ready.") if not self._is_running else None)    


    def eventFilter(self, source: QObject, event: QEvent) -> bool:
        """
        Filters out Wheel events over specific child parameter spin boxes and combo boxes
        to prevent them from changing values when the user intends to scroll
        the main scroll area.
        """
        if event.type() == QEvent.Type.Wheel:
            # Check if the event source is one of the widgets we want to block
            widgets_to_block = [
                self.temperature_spin,
                self.top_p_spin,
                self.max_tokens_spin,
                self.api_key_combo,
                self.model_combo,
                self.prompt_combo # Added prompt combo
            ]
            if source in widgets_to_block:
                # Consume the event (return True) to stop it from being processed
                log_debug(f"Instance {self.instance_id}: Ignoring wheel event on {source.objectName() or source.__class__.__name__}")
                event.accept()
                return True # Event handled, stop default processing

        # For all other events or sources, pass them to the base class implementation
        return super().eventFilter(source, event)



    # --- Public Methods (called by MultiModeWidget) ---



    def _populate_model_combo_instance(self, models: List[Dict[str, Any]]):
        """Populates the instance's model combo box and selects the default."""
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        log_debug(f"Instance {self.instance_id}: Populating model combo with {len(models)} models.")

        models_found = False
        if models:
            for model_info in models:
                # Use display_name for display, name (API ID) as data
                display = model_info.get('display_name', model_info['name'])
                name = model_info['name']
                support_indicator = " [IMG]" if model_info.get('likely_image_support') else ""
                self.model_combo.addItem(f"{display}{support_indicator}", name) # Add display text + data
            models_found = True
            self.model_combo.setEnabled(True) # Enable combo

            # --- Default Selection Logic ---
            # Get the default *API name* from constants
            target_default_model_name = constants.DEFAULT_MODEL_NAME
            # Find the index where the *data* matches the default API name
            index = self.model_combo.findData(target_default_model_name)

            if index != -1:
                self.model_combo.setCurrentIndex(index)
                log_debug(f"Instance {self.instance_id}: Set model to default: {target_default_model_name}")
            elif self.model_combo.count() > 0:
                self.model_combo.setCurrentIndex(0) # Fallback to first item if default not found
                log_warning(f"Instance {self.instance_id}: Default model '{target_default_model_name}' not found in API list, selecting first: {self.model_combo.itemData(0)}")
            # -----------------------------

        else:
            log_error(f"Instance {self.instance_id}: Model list provided was empty or fetch failed.")
            self.model_combo.addItem("No models found")
            self.model_combo.setEnabled(False)

        # Enable start button ONLY if a model was successfully selected AND key is valid
        is_ready_to_generate = models_found and self.model_combo.isEnabled() and bool(self._current_api_key_name)
        self._set_ui_ready(is_ready_to_generate) # Update button state using helper

        self.model_combo.blockSignals(False)

        # Manually trigger model selected handler AFTER unblocking signals
        # to ensure UI elements dependent on model selection (like image buttons) are updated.
        # Use QTimer to ensure it runs after the current event processing cycle.
        final_index = self.model_combo.currentIndex()
        QTimer.singleShot(0, lambda idx=final_index: self._on_model_selected(idx))



    def update_api_key_list(self):
        """
        Refreshes the API key dropdown for this instance.
        Selects the initially assigned key if provided during creation.
        Ensures the combo box is always enabled when the instance is idle.
        """
        log_debug(f"Instance {self.instance_id}: Updating API key list.")

        # Determine the key to pre-select: the initial one if set, otherwise the current selection
        key_to_select = self._initial_api_key_name if hasattr(self, '_initial_api_key_name') and self._initial_api_key_name else self.api_key_combo.currentData()
        # Keep track if we started with an initial assignment intent (though this flag is less critical with the fix)
        was_initially_assigned = hasattr(self, '_initial_api_key_name') and self._initial_api_key_name is not None

        self.api_key_combo.blockSignals(True)
        self.api_key_combo.clear()
        self.api_key_combo.addItem(f"- Select Key -", None) # Placeholder always at index 0

        # Get all available keys from the service
        key_names = self.api_key_service.get_key_names()
        found_key_to_select = False
        idx_to_select = 0 # Default to placeholder

        # Populate the combo box
        for i, name in enumerate(key_names):
            self.api_key_combo.addItem(name, name) # Display name, store name as data
            if name == key_to_select:
                idx_to_select = i + 1 # Index is i + 1 because of the placeholder
                found_key_to_select = True

        # Set the current index based on whether the target key was found
        if found_key_to_select:
            self.api_key_combo.setCurrentIndex(idx_to_select)
            log_debug(f"Instance {self.instance_id}: Restored/Set API key selection to '{key_to_select}'.")
            # Store the *value* if the selection was successful (handles initial assignment case)
            self._current_api_key_name = key_to_select
            self._current_api_key_value = self.api_key_service.get_key_value(key_to_select)
        else:
            self.api_key_combo.setCurrentIndex(0) # Fallback to placeholder
            log_debug(f"Instance {self.instance_id}: Target key '{key_to_select}' not found or initial assignment failed. Selecting placeholder.")
            self._current_api_key_name = None
            self._current_api_key_value = None
            # If the initially assigned key wasn't found, clear the initial assignment flag
            if was_initially_assigned and key_to_select == self._initial_api_key_name:
                log_warning(f"Instance {self.instance_id}: Initially assigned key '{self._initial_api_key_name}' not found in current key list. Clearing assignment.")
                self._initial_api_key_name = None
                # was_initially_assigned remains True for the scope of this method but isn't used after this point.

        # --- FIX: Remove the permanent disabling logic based on _initial_api_key_name ---
        # The API key combo box should always be enabled when the instance is *idle*.
        # Its enabled state during activity (_is_running or _continuous_loop_active)
        # is controlled by the _set_ui_generating method.
        self.api_key_combo.setEnabled(True) # Always enable here when populating list
        self.api_key_combo.setToolTip(f"Select API Key for Instance #{self.instance_id}")
        # The refresh button's enabled state is still dependent on having a valid key selected (index > 0)
        self.refresh_models_button.setEnabled(self.api_key_combo.currentIndex() > 0)
        # --- END FIX ---


        self.api_key_combo.blockSignals(False)

        # Trigger the selection handler for the final index AFTER unblocking signals
        # Use QTimer to ensure it runs after the current event processing cycle
        final_index = self.api_key_combo.currentIndex()
        QTimer.singleShot(0, lambda idx=final_index: self._on_api_key_selected(idx))

    def update_prompt_list(self):
        """Refreshes the prompt dropdown for this instance, including thumbnails."""
        log_debug(f"Instance {self.instance_id}: Updating prompt list with thumbnails.")
        current_selection_data = self.prompt_combo.currentData()
        self.prompt_combo.blockSignals(True)
        self.prompt_combo.clear()
        self.prompt_combo.addItem("- Load Prompt -", "") # Placeholder
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

            display_text = f"{name}" # Keep it short for multi-mode

            # --- Load Icon ---
            icon = QIcon() # Default empty icon
            if relative_thumb_path:
                try:
                    # Construct full path using constants
                    full_thumb_path = constants.PROMPTS_ASSETS_DIR / relative_thumb_path
                    if full_thumb_path.is_file():
                        icon = QIcon(str(full_thumb_path))
                    else:
                        log_warning(f"Instance {self.instance_id}: Thumbnail file not found for {slot_key}: {full_thumb_path}")
                except Exception as e:
                     log_error(f"Instance {self.instance_id}: Error creating QIcon for {slot_key} from path {relative_thumb_path}: {e}")

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



    def apply_global_settings(self):
        """Applies relevant global settings to this instance."""
        log_debug(f"Instance {self.instance_id}: Applying global settings.")
        self.temperature_spin.setValue(self.settings_service.get_setting("default_temperature", constants.DEFAULT_TEMPERATURE))
        self.top_p_spin.setValue(self.settings_service.get_setting("default_top_p", constants.DEFAULT_TOP_P))
        self.max_tokens_spin.setValue(self.settings_service.get_setting("default_max_tokens", constants.DEFAULT_MAX_OUTPUT_TOKENS))

        global_autosave_default = self.settings_service.get_setting("auto_save_enabled", constants.DEFAULT_AUTO_SAVE_ENABLED)
        self.set_autosave(global_autosave_default)
        self.set_continuous(False) # Default continuous off

        # Load instance-specific safety settings
        instance_safety_key = f"instance_{self.instance_id}_safety_settings"
        # --- MODIFICATION START ---
        # Load potentially serialized settings (which might be None or {})
        loaded_safety_setting = self.settings_service.get_setting(instance_safety_key, None)

        # If loading resulted in None (setting never saved), default internal state to {}
        # If loading resulted in {} (user explicitly saved defaults), keep it as {}
        # If loading resulted in a populated dict (deserialized), keep it
        if loaded_safety_setting is None:
            self._current_safety_settings = {} # Default to empty dict (represents API defaults)
            log_debug(f"Instance {self.instance_id}: No saved safety settings found, defaulting to empty dict (API defaults).")
        else:
            # This will be the deserialized dict or {} if loaded/deserialized correctly
            self._current_safety_settings = loaded_safety_setting
            log_debug(f"Instance {self.instance_id}: Loaded safety settings: {self._current_safety_settings}")
        # --- MODIFICATION END ---



    def load_prompt(self, prompt_text: str):
        """Loads prompt text (e.g., from metadata viewer)."""
        log_info(f"Instance {self.instance_id}: Loading prompt externally.")
        self.prompt_text_edit.setPlainText(prompt_text)
        self.prompt_combo.setCurrentIndex(0) # Reset dropdown selection
        self._update_status_label("Prompt loaded.")
        QTimer.singleShot(3000, lambda: self._update_status_label("Ready.") if not self._is_running else None)

    # --- Internal Slots ---


    def shutdown_mode(self, is_closing=False) -> bool:
        """
        Prepares the instance widget for mode switching or app closing.
        In MultiMode, this is primarily a check if the instance is busy.
        The actual thread cleanup on app close happens when the widget is removed.
        """
        instance_log_prefix = f"Instance {self.instance_id}"
        log_info(f"{instance_log_prefix}: Shutdown requested (is_closing={is_closing}).")

        # Check if the instance is actively running or is in a continuous loop
        if self._is_running or self._continuous_loop_active:
            log_warning(f"{instance_log_prefix}: Generation is in progress or loop is active. Cannot shutdown/switch mode.")

            if is_closing:
                 # On application close, MultiModeWidget calls stop_generation (which acts as cancel)
                 # and then calls remove_instance (which calls remove_self on this widget).
                 # The removal logic in remove_self is where the thread is cleanly shut down.
                 # So here, we just indicate that it's busy but will be handled by the removal process.
                 log_debug(f"{instance_log_prefix}: Instance is busy but application is closing. Will attempt clean up via remove_self.")
                 return True # Indicate it will be handled, allow close to proceed to removal

            else:
                 # Just switching modes, not closing app - prevent switch if busy
                 log_warning(f"{instance_log_prefix}: Cannot switch mode while busy. Stop instance first.")
                 # Use the status signal relayed by MultiModeWidget for user feedback
                 self.status_update.emit(f"Instance {self.instance_id} busy.", 3000)
                 return False # Prevent mode switch

        # If the instance is not running and not looping, it's always safe
        log_info(f"{instance_id_str}: Instance is idle. State clean.")
        return True



    @pyqtSlot()
    def _remove_self(self, silent: bool = False): # Added silent flag
        """
        Requests deletion from parent and ensures all worker threads
        are stopped and cleaned up.
        """
        instance_id_str = f"Instance {self.instance_id}"
        log_info(f"{instance_id_str}: Removal requested (silent={silent}).")

        if self._is_running or self._continuous_loop_active:
            log_info(f"{instance_id_str}: Stopping generation/loop before removal.")
            self.stop_generation() 

        # Clean up the main generation worker thread
        if hasattr(self, '_worker_thread') and self._worker_thread is not None:
            if self._worker_thread.isRunning():
                log_debug(f"{instance_id_str}: Quitting main worker thread's event loop...")
                self._worker_thread.quit()
                if not self._worker_thread.wait(1000): # Reduced wait time
                    log_error(f"{instance_id_str}: Main worker thread did not terminate cleanly during removal.")
                else:
                    log_debug(f"{instance_id_str}: Main worker thread terminated.")
            try:
                self._worker_thread.finished.disconnect(self._worker_thread.deleteLater)
            except (TypeError, RuntimeError): pass
            self._worker_thread.finished.connect(self._worker_thread.deleteLater)
            log_debug(f"{instance_id_str}: Scheduled main worker thread object for deletion.")
        else:
            log_warning(f"{instance_id_str}: Main worker thread object not found or already cleaned up.")

        # Clean up the thumbnail loader thread
        if hasattr(self, '_thumbnail_loader_thread') and self._thumbnail_loader_thread is not None:
            if self._thumbnail_loader_worker: # Cancel worker first
                self._thumbnail_loader_worker.cancel()

            if self._thumbnail_loader_thread.isRunning():
                log_debug(f"{instance_id_str}: Quitting thumbnail loader thread's event loop...")
                self._thumbnail_loader_thread.quit()
                if not self._thumbnail_loader_thread.wait(500): # Shorter wait
                    log_error(f"{instance_id_str}: Thumbnail loader thread did not terminate cleanly during removal.")
                else:
                    log_debug(f"{instance_id_str}: Thumbnail loader thread terminated.")
            try:
                self._thumbnail_loader_thread.finished.disconnect(self._thumbnail_loader_thread.deleteLater)
            except (TypeError, RuntimeError): pass
            if self._thumbnail_loader_worker : # Worker needs to be deleted too
                try: self._thumbnail_loader_worker.finished.disconnect(self._thumbnail_loader_worker.deleteLater)
                except: pass
                self._thumbnail_loader_worker.deleteLater()
            self._thumbnail_loader_thread.finished.connect(self._thumbnail_loader_thread.deleteLater)
            log_debug(f"{instance_id_str}: Scheduled thumbnail loader thread object for deletion.")
        else:
            log_debug(f"{instance_id_str}: Thumbnail loader thread object not found or already cleaned up.")
        
        self._thumbnail_loader_worker = None
        self._thumbnail_loader_thread = None

        log_debug(f"{instance_id_str}: Emitting request_delete signal.")
        try:
            self.request_delete.emit(self.instance_id)
        except Exception as emit_err:
            log_error(f"{instance_id_str}: Error emitting request_delete signal: {emit_err}")


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


    @pyqtSlot(int)
    def _on_api_key_selected(self, index):
        """Handles API key selection change, deferring model loading."""
        selected_key_name = self.api_key_combo.itemData(index)
        log_debug(f"Instance {self.instance_id}: API Key selected: {selected_key_name}")

        # --- Reset state if placeholder selected ---
        if selected_key_name is None:
            self._current_api_key_name = None
            self._current_api_key_value = None
            self.model_combo.clear(); self.model_combo.setEnabled(False)
            self.refresh_models_button.setEnabled(False)
            self._set_ui_ready(False)
            self._update_status_label("Select API Key.")
            self._selected_image_paths = []
            self._sequential_image_queue = []
            self._sequential_current_index = -1
            self._update_image_label()
            return

        # --- Avoid redundant processing IF the key name is the same AND the client is already validated ---
        if selected_key_name == self._current_api_key_name and self.gemini_handler.is_client_available(selected_key_name):
             log_debug(f"Instance {self.instance_id}: Same API key '{selected_key_name}' re-selected and client available.")
             # Check cache - if models exist, populate immediately. If not, defer loading anyway.
             cached_models = self.gemini_handler.available_models_cache.get(selected_key_name)
             if cached_models:
                  log_debug(f"Instance {self.instance_id}: Populating models from cache for re-selected key.")
                  self._populate_model_combo_instance(cached_models)
                  self._set_ui_ready(self.model_combo.count() > 0)
                  self._update_status_label("Ready.")
             else:
                  log_debug(f"Instance {self.instance_id}: Key re-selected, client valid, but no cached models. Deferring load.")
                  # Defer load even if key is the same but models aren't cached
                  self._update_status_label("Validating key...") # Show validating status temporarily
                  QTimer.singleShot(0, self._validate_and_load_models_deferred)
             return # Exit, either populated from cache or deferred loading

        # --- New key selected, get value ---
        key_value = self.api_key_service.get_key_value(selected_key_name)
        if not key_value:
            show_error_message(self, "API Key Error", f"Could not retrieve/decrypt key '{selected_key_name}'.")
            self.api_key_combo.setCurrentIndex(0)
            QTimer.singleShot(0, lambda: self._on_api_key_selected(0))
            return

        # --- Store new key info ---
        self._current_api_key_name = selected_key_name
        self._current_api_key_value = key_value
        log_info(f"Instance {self.instance_id}: API Key set to '{self._current_api_key_name}'.")

        # --- Update UI for Validation/Loading state ---
        self._update_status_label("Validating key...")
        self.model_combo.clear(); self.model_combo.setEnabled(False)
        self.refresh_models_button.setEnabled(False)
        self._set_ui_ready(False)
        self._selected_image_paths = []
        self._sequential_image_queue = []
        self._sequential_current_index = -1
        self._update_image_label()
        QApplication.processEvents()

        # --- Defer the actual client validation and model loading ---
        # Schedule _validate_and_load_models_deferred to run soon in the event loop
        QTimer.singleShot(0, self._validate_and_load_models_deferred)


    @pyqtSlot()
    def _validate_and_load_models_deferred(self):
        """Validates the current client and loads models (called via QTimer)."""
        instance_log_prefix = f"Instance {self.instance_id}"
        api_key_name = self._current_api_key_name
        api_key_value = self._current_api_key_value

        if not api_key_name or not api_key_value:
            log_warning(f"{instance_log_prefix}: Deferred load called but API key info missing.")
            # State might have changed, re-call the main handler for index 0
            self._on_api_key_selected(0)
            return

        log_debug(f"{instance_log_prefix}: Executing deferred client validation and model load for key '{api_key_name}'...")

        # Attempt to get/initialize the client
        client_instance = self.gemini_handler.get_or_initialize_client(api_key_name, api_key_value)

        if client_instance:
            log_debug(f"{instance_log_prefix}: Client validated/retrieved.")
            self.refresh_models_button.setEnabled(True) # Enable refresh now
            self._update_status_label("Loading models...")
            QApplication.processEvents() # Ensure UI updates

            # Check cache before triggering API load
            cached_models = self.gemini_handler.available_models_cache.get(api_key_name)
            if cached_models:
                 log_info(f"{instance_log_prefix}: Using cached models for key: {api_key_name}")
                 self._populate_model_combo_instance(cached_models)
                 if self.model_combo.count() > 0 and self.model_combo.isEnabled():
                      self._update_status_label("Ready.")
                 else:
                      self._update_status_label("Model Load Failed.")
            else:
                 # Cache miss, load models from API
                 # _load_models uses the stored _current_api_key_name/_value
                 self._load_models(force_refresh=False) # Handles status updates internally
        else:
             # Client validation failed
             show_error_message(self, "API Key Error", f"Failed to initialize or validate API key '{api_key_name}'. Check the key and API access.")
             self._update_status_label("Key Validation Failed.")
             self.api_key_combo.setCurrentIndex(0) # Reset to placeholder
             # Ensure the handler for placeholder is called if index actually changes
             QTimer.singleShot(0, lambda: self._on_api_key_selected(0))

        # Update UI ready state after model loading attempt
        self._set_ui_ready(self.model_combo.count() > 0 and self.model_combo.isEnabled())

    def _set_ui_ready(self, ready: bool):
         """Enable/disable generation button based on valid key/model selection."""
         self.start_stop_button.setEnabled(ready)
         if not ready and self.start_stop_button.isChecked():
              self.start_stop_button.setChecked(False) # Force stop if config becomes invalid


    @pyqtSlot()
    def _refresh_models(self):
        """Forces refresh of model list for this instance's selected key."""
        # Use the currently stored key name and value for this instance
        api_key_name = self._current_api_key_name
        api_key_value = self._current_api_key_value

        if not api_key_name or not api_key_value:
            show_info_message(self, "Refresh Models", "Select a valid API key first.")
            return

        self._update_status_label("Refreshing models...")
        QApplication.processEvents()
        # Pass instance's key info to the handler's list_models
        self._load_models(force_refresh=True) # _load_models uses instance keys
        self._set_ui_ready(self.model_combo.count() > 0)
        if self.model_combo.count() > 0:
            self._update_status_label("Models refreshed.")
            QTimer.singleShot(3000, lambda: self._update_status_label("Ready.") if not self._is_running else None)


    def _load_models(self, force_refresh=False):
        """Loads models into this instance's dropdown using its selected API key."""
        api_key_name = self._current_api_key_name
        api_key_value = self._current_api_key_value

        if not api_key_name or not api_key_value:
             log_warning(f"Instance {self.instance_id}: Cannot load models, API key info missing.")
             self.model_combo.clear(); self.model_combo.setEnabled(False)
             self._set_ui_ready(False)
             self._update_status_label("Select API Key.")
             return

        # Use the status update method
        self._update_status_label("Loading models...")
        QApplication.processEvents() # Ensure UI updates

        # Call handler's list_models with the instance's specific key info
        models = self.gemini_handler.list_models(
            api_key_name=api_key_name,
            api_key_value=api_key_value,
            force_refresh=force_refresh
        )

        # --- Use the Helper Method ---
        self._populate_model_combo_instance(models)
        # --- End Use Helper Method ---

        # Update ready state based on whether models were successfully populated
        self._set_ui_ready(self.model_combo.count() > 0 and self.model_combo.isEnabled())

        # Update status after population attempt
        if self.model_combo.count() > 0 and self.model_combo.isEnabled():
            if not self._is_running: # Avoid overwriting "Generating..."
                self._update_status_label("Ready.")
        else:
            self._update_status_label("Model Load Failed.") # Status updated by populate helper if list is empty



    @pyqtSlot(int)
    def _on_model_selected(self, index):
        """Handles model selection change for this instance."""
        model_name = self.model_combo.itemData(index)
        if not model_name:
            self.add_image_button.setEnabled(False)
            self.clear_images_button.setEnabled(False)
            self._set_ui_ready(False)
            return

        log_info(f"Instance {self.instance_id}: Model selected: {model_name}")
        self._set_ui_ready(True) # Enable generation if model is selected

        # Get the list of models specifically for the current instance's API key
        models_for_current_key = self.gemini_handler.available_models_cache.get(self._current_api_key_name, [])
        # Now iterate through the correct list
        model_info = next((m for m in models_for_current_key if m['name'] == model_name), None)
        supports_images = model_info.get('likely_image_support', False) if model_info else False

        self.add_image_button.setEnabled(supports_images)
        self._update_image_label() # Update label and clear button state
        if not supports_images and self._selected_image_paths:
             log_warning(f"Instance {self.instance_id}: Model doesn't support images, clearing selection.")
             self._clear_images()


    @pyqtSlot()
    def _open_api_keys_external(self):
        main_window = self.window()
        if main_window and hasattr(main_window, '_open_api_keys'):
            main_window._open_api_keys()
        else: log_error(f"Instance {self.instance_id}: Cannot find main window or _open_api_keys method.")

    @pyqtSlot()
    def _open_prompts_external(self):
        main_window = self.window()
        if main_window and hasattr(main_window, '_open_prompts'):
            main_window._open_prompts()
        else: log_error(f"Instance {self.instance_id}: Cannot find main window or _open_prompts method.")



    @pyqtSlot()
    def _configure_safety(self):
        """Opens safety dialog specific to this instance."""
        if not SDK_AVAILABLE: return
        log_debug(f"Instance {self.instance_id}: Configuring safety settings.")

        dialog = SafetySettingsDialog(self._current_safety_settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_settings = dialog.get_selected_settings() # Returns None if all unspecified

            # --- MODIFICATION START ---
            # If dialog returns None (user chose defaults), store {} internally.
            # Otherwise, store the returned dictionary.
            effective_new_settings = new_settings if new_settings is not None else {}
            # --- MODIFICATION END ---

            # Check if settings actually changed before updating/saving
            if effective_new_settings != self._current_safety_settings:
                self._current_safety_settings = effective_new_settings
                instance_safety_key = f"instance_{self.instance_id}_safety_settings"
                # Save the effective settings (which could be {}) using the service
                self.settings_service.set_setting(instance_safety_key, self._current_safety_settings)
                log_info(f"Instance {self.instance_id}: Safety settings updated and saved: {self._current_safety_settings}")
                self._update_status_label("Safety updated.")
                QTimer.singleShot(3000, lambda: self._update_status_label("Ready.") if not self._is_running else None)
            else:
                log_debug(f"Instance {self.instance_id}: Safety settings dialog accepted, but no changes made.")
        else:
            log_debug(f"Instance {self.instance_id}: Safety settings dialog cancelled.")


    @pyqtSlot(int)
    def _load_selected_prompt(self, index):
        """Loads the selected prompt text into the editor and remembers the slot key."""
        slot_key = self.prompt_combo.itemData(index)

        if slot_key:
            # Use get_prompt to get the full data, including text and name for consistency
            prompt_data = self.prompt_service.get_prompt(slot_key)
            if prompt_data and "text" in prompt_data:
                prompt_text = prompt_data["text"]
                self.prompt_text_edit.setPlainText(prompt_text)
                self._loaded_prompt_slot_key = slot_key # <-- Store the loaded slot key
                log_info(f"Instance {self.instance_id}: Loaded prompt from slot: {slot_key}")
                self._update_status_label("Prompt loaded.")
                QTimer.singleShot(3000, lambda: self._update_status_label("Ready.") if not self._is_running else None)
            else:
                # Error retrieving prompt data
                log_error(f"Instance {self.instance_id}: Could not retrieve data for prompt slot: {slot_key}")
                self.prompt_combo.setCurrentIndex(0) # Reset dropdown selection to placeholder
                self.prompt_text_edit.clear()
                self._loaded_prompt_slot_key = None # <-- Reset loaded slot key
                self._update_status_label("Prompt Load Failed.")
                QTimer.singleShot(3000, lambda: self._update_status_label("Ready.") if not self._is_running else None)
        else:
            # Placeholder selected
            log_debug(f"Instance {self.instance_id}: Prompt placeholder selected.")
            self.prompt_text_edit.clear() # Clear the text editor
            self._loaded_prompt_slot_key = None # <-- Reset the loaded slot key
            # Optional: Update status bar if clearing had an effect
            # self._update_status_label("Prompt text editor cleared.")
            # QTimer.singleShot(2000, lambda: self._update_status_label("Ready.") if not self._is_running else None) # Reset status later



    @pyqtSlot()
    def _add_images(self):
        """Opens file dialog to select images and adds them to the appropriate list for this instance."""
        file_filter = "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"
        last_dir = self.settings_service.get_setting("last_image_dir", str(Path.home()))
        # Add instance ID to dialog title for clarity
        filepaths_tuple = QFileDialog.getOpenFileNames(self, f"Select Image(s) - Instance {self.instance_id}", last_dir, file_filter)
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
                        log_debug(f"Instance {self.instance_id} ({mode_name}): Image already selected: {p_path.name}")
                else:
                    log_warning(f"Instance {self.instance_id} ({mode_name}): Selected path is not a file: {p_path}")

            if newly_added > 0:
                log_info(f"Instance {self.instance_id} ({mode_name}): Added {newly_added} image(s). Total in current mode list: {len(target_list)}")

                # --- Thumbnail Logic (InstanceWidget only) ---
                # Set thumbnail path if not already set (based on first image added in this batch)
                if first_added_path and not self._thumbnail_image_path:
                     self._thumbnail_image_path = first_added_path
                     log_debug(f"Instance {self.instance_id}: Set thumbnail path to {self._thumbnail_image_path.name}")
                     # No need to explicitly load here, MultiModeWidget's visibility check will handle it.
                # --- End Thumbnail Logic ---

                # If sequential mode was just enabled or queue was empty/finished, reset index
                # Check index specifically to handle cases where user adds more images mid-sequence but before starting next
                if self._sequential_mode_enabled and self._sequential_current_index == -1:
                     log_debug(f"Instance {self.instance_id} (Sequential): Resetting sequence index to 0 after adding images.")
                     self._sequential_current_index = 0

                self._update_image_label() # Update label based on the current mode
                if first_added_path:
                    self.settings_service.set_setting("last_image_dir", str(first_added_path.parent))
            else:
                 log_info(f"Instance {self.instance_id} ({mode_name}): No new valid images were added.")



    @pyqtSlot()
    def _clear_images(self):
        """Clears the selected image list for this instance based on the current mode."""
        instance_id_str = f"Instance {self.instance_id}" 

        cleared_something = False
        if self._sequential_mode_enabled:
            if self._sequential_image_queue:
                log_info(f"{instance_id_str}: Clearing sequential image queue.")
                self._sequential_image_queue = []
                self._sequential_current_index = -1 
                cleared_something = True
        else:
            if self._selected_image_paths:
                log_info(f"{instance_id_str}: Clearing selected images.")
                self._selected_image_paths = []
                cleared_something = True

        if cleared_something:
            # If any image list was cleared, also clear the primary thumbnail path
            # and call clear_thumbnail() which will handle cancelling any pending load
            # for that path and reset the UI for the thumbnail.
            if self._thumbnail_image_path:
                log_debug(f"{instance_id_str}: Primary thumbnail path was set. Clearing it now.")
                self._thumbnail_image_path = None
                self.clear_thumbnail() # This also sets _thumbnail_loaded to False

            self._update_image_label()
            self._update_status_label("Images cleared.")
            QTimer.singleShot(3000, lambda: self._update_status_label("Ready.") if not self._is_running else None)
        else:
            log_debug(f"{instance_id_str}: No images to clear for the current mode.")





    def update_thumbnail_visibility(self, is_visible: bool):
        """Loads or clears the thumbnail based on visibility."""
        if is_visible:
            self._load_thumbnail_if_needed()
        else:
            self.clear_thumbnail()




    def _load_thumbnail_if_needed(self):
        """Loads the thumbnail asynchronously if needed and not already loaded/loading."""
        if self._thumbnail_loaded or not self._thumbnail_image_path or not self.isVisible():
            return
        if self.window().isMinimized():
            return
        
        # If already loading this exact path, don't start another
        if self._thumbnail_loader_worker is not None and self._current_thumbnail_path_loading == self._thumbnail_image_path:
            log_debug(f"Instance {self.instance_id}: Thumbnail for {self._thumbnail_image_path.name} is already being loaded.")
            return

        # Cancel any previous pending thumbnail load if path changed
        if self._thumbnail_loader_worker is not None:
            log_debug(f"Instance {self.instance_id}: New thumbnail request, cancelling previous one.")
            self._thumbnail_loader_worker.cancel()
            if self._thumbnail_loader_thread and self._thumbnail_loader_thread.isRunning():
                self._thumbnail_loader_thread.quit()
                self._thumbnail_loader_thread.wait(500) # Brief wait

        log_debug(f"Instance {self.instance_id}: Requesting async thumbnail load for {self._thumbnail_image_path.name}")
        self.result_image_label.setText("Loading Thumb...") # Indicate loading
        self._current_thumbnail_path_loading = self._thumbnail_image_path

        self._thumbnail_loader_thread = QThread(self)
        self._thumbnail_loader_worker = ThumbnailLoaderWorker(
            slot_key=f"instance_{self.instance_id}", # Use instance_id as a unique key
            image_path=self._thumbnail_image_path,
            target_size=self.result_image_label.size() # Or a fixed thumbnail size like QSize(128,128)
        )
        self._thumbnail_loader_worker.moveToThread(self._thumbnail_loader_thread)

        self._thumbnail_loader_worker.finished.connect(self._on_thumbnail_loaded)
        self._thumbnail_loader_worker.error.connect(self._on_thumbnail_load_error)
        self._thumbnail_loader_thread.started.connect(self._thumbnail_loader_worker.run)
        self._thumbnail_loader_worker.finished.connect(self._thumbnail_loader_thread.quit)
        self._thumbnail_loader_worker.error.connect(self._thumbnail_loader_thread.quit)
        # Clean up thread and worker once thread finishes
        self._thumbnail_loader_thread.finished.connect(self._thumbnail_loader_thread.deleteLater)
        self._thumbnail_loader_worker.finished.connect(self._thumbnail_loader_worker.deleteLater)
        self._thumbnail_loader_worker.error.connect(self._thumbnail_loader_worker.deleteLater)
        
        self._thumbnail_loader_thread.start()


    @pyqtSlot(str, QPixmap)
    def _on_thumbnail_loaded(self, slot_key: str, pixmap: QPixmap):
        # Ensure this update is for the current instance and path
        instance_slot_key = f"instance_{self.instance_id}"
        if slot_key == instance_slot_key and self._current_thumbnail_path_loading == self._thumbnail_image_path:
            log_debug(f"Instance {self.instance_id}: Async thumbnail loaded successfully.")
            self.result_image_label.setPixmap(pixmap)
            self.result_image_label.setToolTip(f"Thumbnail: {self._thumbnail_image_path.name if self._thumbnail_image_path else 'N/A'}")
            self._thumbnail_loaded = True
            self.result_image_label.setText("") # Clear "Loading Thumb..."
        else:
            log_debug(f"Instance {self.instance_id}: Received stale/mismatched thumbnail for {slot_key} (current path: {self._thumbnail_image_path}). Ignoring.")
        
        # Reset loading trackers
        self._current_thumbnail_path_loading = None
        self._thumbnail_loader_worker = None # Allow new requests

    @pyqtSlot(str, str)
    def _on_thumbnail_load_error(self, slot_key: str, error_message: str):
        instance_slot_key = f"instance_{self.instance_id}"
        if slot_key == instance_slot_key: # Check if the error is for this instance
            log_error(f"Instance {self.instance_id}: Error loading thumbnail async: {error_message}")
            self.result_image_label.setText("Thumb Err")
            self._thumbnail_loaded = False
        
        # Reset loading trackers
        self._current_thumbnail_path_loading = None
        self._thumbnail_loader_worker = None # Allow new requests


    def clear_thumbnail(self):
        """Clears the currently displayed thumbnail and cancels ongoing load if any."""
        # Cancel ongoing load
        if self._thumbnail_loader_worker is not None:
            log_debug(f"Instance {self.instance_id}: Clearing thumbnail, cancelling ongoing load for {self._current_thumbnail_path_loading}.")
            self._thumbnail_loader_worker.cancel()
            if self._thumbnail_loader_thread and self._thumbnail_loader_thread.isRunning():
                self._thumbnail_loader_thread.quit()
                self._thumbnail_loader_thread.wait(200) # Short wait
            self._thumbnail_loader_worker = None
            self._current_thumbnail_path_loading = None

        if self._thumbnail_loaded: # Only clear if a thumbnail was actually loaded and displayed
            log_debug(f"Instance {self.instance_id}: Clearing displayed thumbnail.")
            self.result_image_label.clear()
            self.result_image_label.setText("No Img") 
            self.result_image_label.setToolTip("")
            self._thumbnail_loaded = False
        # If it wasn't a thumbnail but a full image, or "Loading Thumb...", don't just set "No Img"
        # Let the normal flow (e.g. _display_image or error handlers) set the text.

    def _update_image_label(self):
        """Updates the label showing the number/status of selected images for this instance."""
        # Check if the currently selected model supports image input
        model_supports_images = self.add_image_button.isEnabled()

        if self._sequential_mode_enabled:
            q_len = len(self._sequential_image_queue)
            current_idx = self._sequential_current_index

            # Tooltip for sequential mode
            tooltip_base = f"Sequential: {q_len} image{'s' if q_len != 1 else ''} queued."

            if q_len == 0:
                self.image_label.setText("Seq: 0") # Keep it short
                self.image_label.setToolTip(tooltip_base)
                self.clear_images_button.setEnabled(False)
            elif 0 <= current_idx < q_len:
                # Currently processing or paused mid-sequence
                current_file = self._sequential_image_queue[current_idx]
                # Short display, full in tooltip
                self.image_label.setText(f"Seq: {current_idx+1}/{q_len}")
                self.image_label.setToolTip(f"{tooltip_base}\nCurrent: {current_file.name}")
                self.clear_images_button.setEnabled(True)
            else: # Index is -1 (not started or finished loop) or out of bounds
                self.image_label.setText(f"Seq: {q_len} Rdy") # Keep it short
                self.image_label.setToolTip(tooltip_base + "\nReady to start sequence.")
                self.clear_images_button.setEnabled(True)

        else: # Standard (Simultaneous) Mode
            count = len(self._selected_image_paths)
            tooltip_base = f"Simultaneous: {count} image{'s' if count != 1 else ''} selected."

            if not model_supports_images:
                if count > 0:
                    self.image_label.setText(f"{count} Img(?)") # Short indicator
                    self.image_label.setToolTip(tooltip_base + "\nWarning: Selected model may not support image input.")
                else:
                    self.image_label.setText("No Img")
                    self.image_label.setToolTip("Selected model may not support image input.")
                self.clear_images_button.setEnabled(count > 0) # Allow clearing
                return

            # Model supports images, proceed with standard display
            if count == 0:
                self.image_label.setText("No Img")
                self.image_label.setToolTip(tooltip_base)
                self.clear_images_button.setEnabled(False)
            elif count == 1:
                self.image_label.setText(f"1 Img") # Short
                self.image_label.setToolTip(f"{tooltip_base}\nFile: {self._selected_image_paths[0].name}")
                self.clear_images_button.setEnabled(True)
            else:
                self.image_label.setText(f"{count} Imgs") # Short
                tooltip_files = "\n".join(f"- {p.name}" for p in self._selected_image_paths)
                self.image_label.setToolTip(f"{tooltip_base}\nFiles:\n{tooltip_files}")
                self.clear_images_button.setEnabled(True)

    # --- Generation Control ---


    @pyqtSlot()
    def _browse_save_path(self):
        """Opens a dialog to select a custom save directory."""
        current_path_str = self.save_path_edit.text()
        # Use current custom path as start, fallback to default output dir
        start_dir = str(self._custom_save_path) if self._custom_save_path else str(constants.DATA_DIR / "output")

        dir_path_str = QFileDialog.getExistingDirectory(
            self,
            f"Select Custom Save Directory - Instance {self.instance_id}",
            start_dir
        )

        if dir_path_str:
            selected_path = Path(dir_path_str)
            self._custom_save_path = selected_path
            self.save_path_edit.setText(str(selected_path))
            self.clear_save_path_button.setEnabled(True) # Enable clear button
            log_info(f"Instance {self.instance_id}: Custom save path set to: {selected_path}")
        else:
            log_debug(f"Instance {self.instance_id}: Custom save path selection cancelled.")

    @pyqtSlot()
    def _clear_save_path(self):
        """Clears the custom save path, reverting to default."""
        if self._custom_save_path:
            self._custom_save_path = None
            self.save_path_edit.clear() # Clear the display
            self.save_path_edit.setPlaceholderText("[Default Output Path]") # Restore placeholder
            self.clear_save_path_button.setEnabled(False) # Disable clear button
            log_info(f"Instance {self.instance_id}: Custom save path cleared. Using default.")




    @pyqtSlot()
    def _handle_start_stop_click(self):
         """Handles clicks on the main action button (Start/Stop/Stop Loop)."""
         instance_id_str = f"Instance {self.instance_id}"

         if self._continuous_loop_active:
             # If loop is marked active, button click means "Stop Loop"
             log_info(f"{instance_id_str}: Stop Loop requested by user.")
             self._stop_loop()
         elif self._is_running:
             # If worker is running (but loop not active), click means "Stop" (Cancel)
             log_info(f"{instance_id_str}: Stop Generation requested by user.")
             self.stop_generation() # stop_generation now acts as cancel
         else:
             # If idle, click means "Start Generation"
             # Check continuous checkbox to determine if loop should be activated
             should_loop = self.continuous_checkbox.isChecked()
             log_info(f"{instance_id_str}: Start Generation requested by user (Loop={should_loop}).")
             if should_loop:
                 self._continuous_loop_active = True # Activate loop flag *before* starting
             self.start_generation() # Start the first (or only) run


    def _stop_loop(self):
         """Deactivates the continuous loop flag and cancels any current run."""
         instance_id_str = f"Instance {self.instance_id}"
         if self._continuous_loop_active:
             log_info(f"{instance_id_str}: Deactivating continuous loop.")
             self._continuous_loop_active = False
             self.save_prompt_button.setVisible(False) # Hide save button

             # Also cancel any currently running generation
             if self._is_running:
                 self.stop_generation() # Request worker cancellation (handles UI clearing)
             else:
                 # If loop was active but worker wasn't (e.g., between runs), ensure UI resets
                 self._set_ui_generating(False) # Resets buttons etc.
                 # Explicitly clear display state if stopped between runs
                 self.result_text_edit.clear()
                 self.result_text_edit.setPlaceholderText(f"Text result...")
                 if not self._thumbnail_loaded: # Keep loaded thumbnail
                     self.result_image_label.clear()
                     self.result_image_label.setText("No Img / Result")
                 self._full_result_pixmap = None
                 self._update_status_label("Loop stopped.")
             # Update button style after potential stop_generation call or immediate reset
             self._update_button_style()
         else:
             log_debug(f"{instance_id_str}: Stop loop called but loop was not active.")
             self._set_ui_generating(False) # Ensure UI is idle just in case


    @pyqtSlot()
    def _save_current_prompt(self):
        """
        Saves the current prompt text from the editor and the last generated image thumbnail
        as a BRAND NEW prompt entry for this instance. Does not attempt to update existing slots.
        """
        instance_id_str = f"Instance {self.instance_id}"
        log_info(f"{instance_id_str}: Attempting to save current prompt text and last generated thumbnail as a NEW prompt entry...")

        # Check if we have the necessary data (image bytes are essential for thumbnail)
        if self._last_image_bytes is None:
            log_warning(f"{instance_id_str}: No last generated image data available to save thumbnail.")
            show_warning_message(self, "Save Error", "No generated image found from the last successful run to create a thumbnail.")
            self.save_prompt_button.setVisible(False) # Ensure button is hidden if data is missing
            return
        # Note: We save the *editor* text, not _last_resolved_prompt, as the user might have edited it.
        current_editor_text = self.prompt_text_edit.toPlainText().strip()
        if not current_editor_text:
             log_warning(f"{instance_id_str}: Prompt text editor is empty, cannot save a new prompt.")
             show_warning_message(self, "Save Error", "Prompt text is empty, cannot save.")
             self.save_prompt_button.setVisible(False)
             return


        # Determine a default name for the new prompt entry
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        # Include instance ID in the default name for easier identification
        default_name = f"Saved_I{self.instance_id}_{timestamp}"


        # Call PromptService to add a *new* prompt entry with text and thumbnail
        log_info(f"{instance_id_str}: Adding new prompt '{default_name}' with thumbnail using current editor text.")
        new_slot_key = self.prompt_service.add_new_prompt_with_thumbnail(
             name=default_name,
             text=current_editor_text, # Save text from the editor
             image_bytes=self._last_image_bytes # Use the stored image bytes for thumbnail creation
         )

        if new_slot_key:
             # Use instance-specific status update and message box
             show_info_message(self, "Prompt Saved", f"Prompt saved successfully as '{default_name}' ({new_slot_key}).\n\nFind it in the Prompt Manager.")
             self._update_status_label(f"Prompt {new_slot_key} Saved.")
             QTimer.singleShot(2000, lambda: self._update_status_label("Ready.") if not self._is_running else None)
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




    def start_generation(self):
         """Public method to initiate generation (callable externally)."""
         instance_log_prefix = f"Instance {self.instance_id}"
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
             log_error(f"{instance_log_prefix}: API Key validation failed."); show_error_message(self, "API Key Error", "Valid API Key not selected."); self._continuous_loop_active = False; self._set_ui_generating(False); self._update_button_style(); self._update_status_label("Select API Key."); return
         if not self.gemini_handler.is_client_available(api_key_name):
              log_error(f"{instance_log_prefix}: Client for key '{api_key_name}' unavailable."); show_error_message(self, "Client Error", f"Client for key '{api_key_name}' unavailable."); self._continuous_loop_active = False; self._set_ui_generating(False); self._update_button_style(); self._update_status_label("Client Error."); return
         model_name = self.model_combo.itemData(self.model_combo.currentIndex())
         if not model_name:
             log_error(f"{instance_log_prefix}: Model validation failed."); show_error_message(self, "Input Error", "Select a Model."); self._continuous_loop_active = False; self._set_ui_generating(False); self._update_button_style(); self._update_status_label("Select Model."); return
         # --- GET UNRESOLVED PROMPT TEXT ---
         unresolved_prompt_text = self.prompt_text_edit.toPlainText().strip()
         if not unresolved_prompt_text:
             log_error(f"{instance_log_prefix}: Prompt validation failed."); show_error_message(self, "Input Error", "Prompt cannot be empty."); self._continuous_loop_active = False; self._set_ui_generating(False); self._update_button_style(); self._update_status_label("Enter Prompt."); return
         log_debug(f"{instance_log_prefix}: Input validation passed.")

         # --- Resolve Wildcards ONCE ---
         try:
             log_debug(f"{instance_log_prefix}: Resolving wildcards...")
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
             if not self._sequential_image_queue: log_error(f"{instance_log_prefix}: Seq mode, no images."); show_error_message(self, "Input Error", "Sequential mode enabled, but no images."); self._continuous_loop_active = False; self._set_ui_generating(False); self._update_button_style(); self._update_image_label(); self._update_status_label("No Images in Seq."); return
             if not supports_images: log_error(f"{instance_log_prefix}: Seq mode, model no img support."); show_error_message(self, "Input Error", "Model doesn't support images for sequential mode."); self._continuous_loop_active = False; self._set_ui_generating(False); self._update_button_style(); self._update_status_label("Model lacks image support."); return
             if not (0 <= self._sequential_current_index < len(self._sequential_image_queue)): self._sequential_current_index = 0; log_info(f"{instance_log_prefix}: Seq index reset to 0.")
             current_image_path = self._sequential_image_queue[self._sequential_current_index]; image_list_for_worker = [current_image_path]; current_image_for_context = current_image_path
             display_name = current_image_path.name[:15] + '...' if len(current_image_path.name) > 18 else current_image_path.name; status_msg = f"Seq: Proc. {self._sequential_current_index+1}/{len(self._sequential_image_queue)} ({display_name})..."; self._update_status_label(status_msg)
         else: # Standard mode
             if self._selected_image_paths and not supports_images: log_error(f"{instance_log_prefix}: Std mode, images selected, model no support."); show_error_message(self, "Input Error", "Model doesn't support images."); self._continuous_loop_active = False; self._set_ui_generating(False); self._update_button_style(); self._update_status_label("Model lacks image support."); return
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
             **filename_wildcard_values
         }
         log_info(f"[{instance_log_prefix} PRE-WORKER INIT] Passing explicit resolved_map_copy")

         # --- Update UI State ---
         log_debug(f"{instance_log_prefix}: Updating UI to 'generating' state...")
         self.save_prompt_button.setVisible(False); self._set_ui_generating(True)
         self.result_text_edit.clear()
         if not self._thumbnail_loaded: self.result_image_label.clear(); self._full_result_pixmap = None; self.result_image_label.setText("...")
         if not self._sequential_mode_enabled: self._update_status_label("Generating...")
         self.progress_bar.setVisible(True)

         # --- Create Worker and Move to Thread ---
         log_debug(f"{instance_log_prefix}: Creating GenerationWorker...")
         _image_filename_context = current_image_for_context.name if current_image_for_context else None
         if not hasattr(self, '_worker_thread') or self._worker_thread is None or not self._worker_thread.isRunning():
             log_critical(f"{instance_log_prefix}: Worker thread error!"); self._continuous_loop_active = False; self._set_ui_generating(False); self._update_button_style(); self._update_status_label("Internal Error: Thread."); return

         self._generation_worker = GenerationWorker(
             gemini_handler=self.gemini_handler, api_key_name=api_key_name,
             api_key_value=api_key_value, # Pass key value
             params=gen_params, # Pass other params
             resolved_wildcards_map=resolved_map_copy, # <<< PASS EXPLICITLY
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
         self.generation_started.emit(self.instance_id)
         log_info(f"--- {instance_log_prefix}: start_generation COMPLETE ---")




    def _update_button_style(self):
        """Updates the Start/Stop button text, tooltip, checked state, and enabled state."""
        instance_id_str = f"Instance {self.instance_id}"
        # Determine if configuration is valid enough to allow starting a new run
        can_start_new = not (self._is_running or self._continuous_loop_active) and \
                         bool(self._current_api_key_name) and \
                         self.model_combo.count() > 0 and \
                         self.model_combo.currentIndex() >= 0 # Check if model selected


        if self._continuous_loop_active:
             # Loop is active (or intended to be, even if worker is currently idle between runs)
             self.start_stop_button.setText("Stop Loop")
             self.start_stop_button.setToolTip(f"{instance_id_str}: Stop the continuous generation loop.")
             self.start_stop_button.setChecked(True) # Use checked state to indicate active loop/run
             # *** FIX: The button must be ENABLED when the loop is active to be clickable ***
             self.start_stop_button.setEnabled(True)
        elif self._is_running:
             # Worker is running, but loop is not active (single run in progress)
             self.start_stop_button.setText("Stop") # Changed from "Cancel" for consistency
             self.start_stop_button.setToolTip(f"{instance_id_str}: Stop the current generation.")
             self.start_stop_button.setChecked(True) # Checked when running
             # The button should be enabled to allow stopping the current run
             # *** FIX: Ensure the button is enabled when _is_running is True ***
             self.start_stop_button.setEnabled(True)
         

        else:
             # Idle state (worker not running, loop not active)
             self.start_stop_button.setText("Start")
             self.start_stop_button.setToolTip(f"{instance_id_str}: Start generation.")
             self.start_stop_button.setChecked(False) # Unchecked when idle
             self.start_stop_button.setEnabled(can_start_new)

       
        is_enabled = self._continuous_loop_active or self._is_running or can_start_new
        self.start_stop_button.setEnabled(is_enabled)
        log_debug(f"Instance {self.instance_id}: Button state update: Enabled={is_enabled}, Running={self._is_running}, Looping={self._continuous_loop_active}, CanStartNew={can_start_new}")


      
    def _start_sequential_next(self):
        """Helper method to start the next step in the sequence."""
        # This simply calls the main start method again, which will now
        # pick up the incremented _sequential_current_index.
        if hasattr(self, 'start_generation'): # InstanceWidget
            self.start_generation()
        elif hasattr(self, '_start_generation'): # SingleModeWidget
            self._start_generation()

    def is_looping(self) -> bool:
        """Returns True if the instance is set to loop continuously."""
        return self._continuous_loop_active


    def stop_generation(self):
         """
         Public method to request cancellation of the current generation worker.
         Does NOT change the _continuous_loop_active flag directly; that's handled by _stop_loop.
         """
         instance_id_str = f"Instance {self.instance_id}"

         # --- Stop the pending loop timer if active ---
         if self._loop_timer.isActive():
             log_debug(f"{instance_id_str}: Stopping active loop timer during stop_generation call.")
             self._loop_timer.stop()
         # --- End Stop Timer ---

         # --- Request Worker Cancellation ---
         # Check _is_running AND if the worker object exists
         if self._is_running and self._generation_worker:
             log_info(f"{instance_id_str}: Requesting worker cancellation.")
             self._update_status_label("Cancelling...")
             # Call the cancel method on the worker object
             # Use QMetaObject.invokeMethod for cross-thread call safety
             QMetaObject.invokeMethod(self._generation_worker, "cancel", Qt.ConnectionType.QueuedConnection)

             # Update button state immediately
             # This is done in _set_ui_generating, but let's force an update
             self._set_ui_generating(True) # Keep UI disabled while cancellation is processed
             self._update_button_style() # Update button to "Stop"
             self.start_stop_button.setEnabled(False) # Temporarily disable until worker confirms finish
             self.start_stop_button.setToolTip("Cancellation requested...")

             # --- UI Cleanup for Cancellation ---
             self.save_prompt_button.setVisible(False) # Hide save button
             # Clear result display immediately on cancel request
             self.result_text_edit.clear()
             self.result_text_edit.setPlaceholderText(f"Text result...") # Reset placeholder
             if not self._thumbnail_loaded: # Keep thumbnail if loaded
                 self.result_image_label.clear()
                 self.result_image_label.setText("Cancelled.")
             self._full_result_pixmap = None # Clear stored pixmap
             # --- End UI Cleanup ---

             # The finished handler (_on_generation_finished) will eventually fire
             # with status "cancelled" and do the final UI state reset (_set_ui_generating(False)).
         elif self._is_running and not self._generation_worker:
              # Should not happen if state is consistent, but handle defensively
              log_error(f"{instance_id_str}: Stop requested, _is_running=True, but no worker object found! Forcing stop.")
              self._is_running = False
              self._continuous_loop_active = False # Stop loop if state is inconsistent
              self._set_ui_generating(False) # Reset UI
         else:
             log_debug(f"{instance_id_str}: Stop requested but worker not running.")
             # If stop is called when idle, ensure loop is off and UI is idle
             self._continuous_loop_active = False
             self._set_ui_generating(False) # Ensure UI is reset correctly
             self.save_prompt_button.setVisible(False) # Ensure save button is hidden
             self._update_status_label("Ready.")




    @pyqtSlot(dict)
    def _on_generation_finished(self, result: dict):
         """Handles the result when the GenerationWorker object finishes."""
         instance_log_prefix = f"Instance {self.instance_id}"
         log_info(f"--- {instance_log_prefix}: _on_generation_finished RECEIVED result ---")
         log_info(f"[{instance_log_prefix} RECEIVED RESULT] result['resolved_wildcards_by_name']: {result.get('resolved_wildcards_by_name')}") # Log received map

         was_running = self._is_running; self._is_running = False # Reset flag

         if not was_running:
             log_warning(f"{instance_log_prefix}: Received finish signal but not marked as running."); self._set_ui_generating(False); self._update_button_style(); return
         else:
             log_info(f"{instance_log_prefix}: Worker finished. Status: {result.get('status')}"); self.generation_finished.emit(self.instance_id)

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
                 failed_key_name = result.get("api_key_name", self._current_api_key_name or "Unknown"); log_warning(f"{instance_log_prefix}: Rate limit on key '{failed_key_name}'.")
                 self._set_ui_generating(False); self._update_button_style(); self.start_stop_button.setEnabled(False); self.start_stop_button.setToolTip("Rate limit hit, requesting new key...")
                 self.generation_finished.emit(self.instance_id)
                 self._update_status_label(f"Rate Limit '{failed_key_name}'. Requesting key..."); self.progress_bar.setVisible(True); self.save_prompt_button.setVisible(False); self.request_new_key.emit(self.instance_id); return

             elif status == "success":
                 image_successfully_displayed = False
                 if image_bytes: image_successfully_displayed = self._display_image(image_bytes);
                 if image_successfully_displayed: self._last_image_bytes = image_bytes; self._last_image_mime = image_mime
                 else:
                      if not self._thumbnail_loaded: self._full_result_pixmap = None; self.result_image_label.clear(); self.result_image_label.setText("No Img"); self.result_image_label.setToolTip(""); self._full_result_pixmap = None
                 resolved_prompt_or_empty = resolved_prompt or ""; self.result_text_edit.setPlainText(text_result or "[No text generated]")
                 self._update_status_label("Success."); final_status_message = "Success."; trigger_next = True; self._last_resolved_prompt = resolved_prompt_or_empty
                 # Show save button only if image data exists
                 can_save_prompt = self._last_resolved_prompt is not None and self._last_image_bytes is not None; self.save_prompt_button.setVisible(can_save_prompt)
                 # Auto-save logic
                 if self._last_image_bytes and self.autosave_checkbox.isChecked():
                      log_info(f"[{instance_log_prefix} PRE-AUTOSAVE] Passing resolved_values_by_name_map: {resolved_values_by_name_map}") # Log map before passing
                      self._auto_save_result(self._last_image_bytes, self._last_image_mime, text_result, unresolved_prompt, resolved_prompt, resolved_values_by_name_map, image_filename_context, filename_wildcard_values)

             elif status == "blocked":
                 block_reason = result.get('block_reason', 'Unknown'); self.result_text_edit.setPlainText(f"--- BLOCKED ---\nReason: {block_reason}\n\n{text_result or '[No text]'}")
                 log_warning(f"{instance_log_prefix}: Blocked: {block_reason}"); self._update_status_label(f"Blocked: {block_reason}"); final_status_message = f"Blocked: {block_reason}"
                 trigger_next = self._continuous_loop_active; self.save_prompt_button.setVisible(False)
                 # --- Prepare map for score update ---
                 if self.wildcard_resolver:
                    score_update_map = { f"[{name}]": values[0] for name, values in resolved_values_by_name_map.items() if values } # Approximate map
                    self.wildcard_resolver.update_scores(score_update_map, "blocked")

             elif status == "cancelled":
                 # Display clearing is handled by stop_generation/cancel logic
                 self._update_status_label("Cancelled."); final_status_message = "Cancelled."; self._continuous_loop_active = False; trigger_next = False; self.save_prompt_button.setVisible(False)

             elif status == "error":
                 log_error(f"{instance_log_prefix} Error: {error_message}"); self.result_text_edit.setPlainText(f"--- ERROR ---\n{error_message}")
                 if not self._thumbnail_loaded: self.result_image_label.setText("[Error]")
                 self._full_result_pixmap = None; self._update_status_label("Error."); final_status_message = "Error."; trigger_next = self._continuous_loop_active; self.save_prompt_button.setVisible(False)

             else: # Unknown status
                 log_error(f"{instance_log_prefix} Unknown Status: {status}"); self.result_text_edit.setPlainText(f"[Unknown Status: {status}]\n{error_message}")
                 if not self._thumbnail_loaded: self.result_image_label.setText("[Unknown]")
                 self._full_result_pixmap = None; self._update_status_label("Unknown Status."); final_status_message = "Unknown Status."; trigger_next = self._continuous_loop_active; self.save_prompt_button.setVisible(False)

             # Update scores on success
             if status == "success":
                 if self.wildcard_resolver:
                    # --- Prepare CORRECT map for score update ---
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
                         else: self._update_status_label("Sequence Complete."); final_status_message = "Sequence Complete."; self._continuous_loop_active = False
                 else: # Non-sequential continuous mode
                     log_info(f"{instance_log_prefix}: Continuous non-seq restarting."); should_schedule_next = True
             else:
                 if self._continuous_loop_active and not trigger_next: log_info(f"{instance_log_prefix}: Loop stop triggered by non-success/cancel.")
                 self._continuous_loop_active = False # Ensure flag is off if not scheduling next

             # --- Schedule Next Run or Reset UI ---
             if should_schedule_next:
                 next_step_msg = "Next..." if self._sequential_mode_enabled and self._sequential_current_index != 0 else "Looping..."; self._update_status_label(next_step_msg)
                 request_delay_ms = self.settings_service.get_setting("request_delay", constants.DEFAULT_REQUEST_DELAY) * 1000
                 self._set_ui_generating(False); self._update_button_style(); self._loop_timer.start(max(100, request_delay_ms)); log_debug(f"{instance_log_prefix}: Scheduled next run check in {max(100, request_delay_ms)}ms.")
             else: # Loop is stopping/finished normally or due to error/block/cancel
                 self._continuous_loop_active = False; self._set_ui_generating(False); self._update_button_style()
                 # Reset status label after a short delay
                 QTimer.singleShot(4000, lambda: self._update_status_label("Ready.") if not self._is_running and not self._continuous_loop_active else None)

         except Exception as e:
             log_critical(f"{instance_log_prefix}: CRITICAL EXCEPTION in _on_generation_finished: {e}", exc_info=True)
             self._is_running = False; self._continuous_loop_active = False
             if self._loop_timer.isActive(): self._loop_timer.stop()
             self._set_ui_generating(False); self._update_button_style(); self.result_text_edit.setPlainText(f"--- CRITICAL ERROR ---\n{e}\n\nCheck logs.");
             if not self._thumbnail_loaded: self.result_image_label.setText("[CRITICAL ERR]")
             self._full_result_pixmap = None; self.save_prompt_button.setVisible(False); self._update_status_label("CRITICAL ERROR.")

         finally:
              self._generation_worker = None # Clear worker reference
              log_debug(f"{instance_log_prefix}: Cleared worker ref."); log_info(f"--- {instance_log_prefix}: _on_generation_finished COMPLETE ---")



    def switch_to_api_key(self, new_key_name: str):
        """
        Switches the instance to use the specified API key, validates the new client,
        and retries generation if looping.
        Called by MultiModeWidget after it determines the new key.
        """
        instance_log_prefix = f"Instance {self.instance_id}"
        log_info(f"{instance_log_prefix}: Being switched to API key '{new_key_name}'.")
        new_key_value = self.api_key_service.get_key_value(new_key_name)

        if not new_key_value:
            log_error(f"{instance_log_prefix}: Failed to get value for new key '{new_key_name}'. Stopping.")
            self.handle_no_available_key("Key value error") # Stop gracefully
            return

        # --- Update internal state FIRST ---
        self._current_api_key_name = new_key_name
        self._current_api_key_value = new_key_value
        log_debug(f"{instance_log_prefix}: Internal key state updated to '{new_key_name}'.")

        # --- Explicitly Validate/Initialize Client for the NEW Key NOW ---
        log_debug(f"{instance_log_prefix}: Explicitly validating client for new key '{new_key_name}'...")
        new_client = self.gemini_handler.get_or_initialize_client(new_key_name, new_key_value)
        if not new_client:
             log_error(f"{instance_log_prefix}: Failed to validate/initialize client for the new key '{new_key_name}'. Stopping loop.")
             show_error_message(self, "Key Switch Error", f"Failed to validate the new API key '{new_key_name}'. Check the key and API access.")
             self.handle_no_available_key("New key validation failed") # Stop gracefully
             return # Do not proceed to schedule retry
        else:
             log_info(f"{instance_log_prefix}: Client for new key '{new_key_name}' validated successfully.")

        # --- Update the UI ComboBox selection WITHOUT triggering signals ---
        combo_index_to_select = self.api_key_combo.findData(new_key_name)
        if combo_index_to_select != -1:
            self.api_key_combo.blockSignals(True)
            self.api_key_combo.setCurrentIndex(combo_index_to_select)
            self.api_key_combo.blockSignals(False)
            log_debug(f"{instance_log_prefix}: UI ComboBox selection updated.")
            # Enable refresh button if it was disabled
            self.refresh_models_button.setEnabled(True)
            # Ensure the combo tooltip is updated if it was previously disabled
            if not self.api_key_combo.isEnabled():
                self.api_key_combo.setToolTip(f"API Key '{new_key_name}' assigned by Multi-Mode.")
        else:
            log_warning(f"{instance_log_prefix}: Could not find newly selected key '{new_key_name}' in combo box during switch.")


        # --- Schedule the generation retry IF LOOPING (Now that new client is validated) ---
        # Reset progress bar and update status
        self.progress_bar.setVisible(False)
        self._update_status_label(f"Switched to key '{new_key_name}'. Preparing retry...")
        QApplication.processEvents() # Allow UI update

        if self._continuous_loop_active:
            retry_delay_ms = self.settings_service.get_setting("request_delay", constants.DEFAULT_REQUEST_DELAY) * 1000
            log_info(f"{instance_log_prefix}: Scheduling generation retry with new key in {retry_delay_ms}ms.")
            # Use the persistent timer to schedule the check/start
            self._loop_timer.start(max(500, retry_delay_ms)) # Retry after delay
        else:
            # If loop was stopped while waiting for key, just mark as ready
            log_info(f"{instance_log_prefix}: Loop stopped before key switch completed. Marking as ready.")
            self._set_ui_generating(False) # Ensure UI is idle
            self._update_status_label("Ready.")



    def handle_no_available_key(self, reason: str = "No unused keys"):
        """
        Handles the case where no alternative key could be assigned by MultiModeWidget.
        Stops the loop and updates the UI to reflect the stopped state.
        """
        instance_log_prefix = f"Instance {self.instance_id}"
        log_warning(f"{instance_log_prefix}: Could not get new API key ({reason}). Stopping continuous loop.")

        # --- Stop the loop and timer ---
        self._continuous_loop_active = False
        if self._loop_timer.isActive():
            log_debug(f"{instance_log_prefix}: Stopping active loop timer.")
            self._loop_timer.stop()

        # --- Reset UI State ---
        # Mark worker as stopped (_is_running should ideally be false already, but ensure it)
        self._is_running = False
        self._set_ui_generating(False) # Mark worker stopped, enable config widgets
        self._update_button_style()    # Update button to "Start", potentially disable

        # --- Update Status & Disable Start Button ---
        self.progress_bar.setVisible(False)
        self.start_stop_button.setEnabled(False) # Disable start button explicitly
        self.start_stop_button.setToolTip(f"Stopped: {reason}. No free API keys.")
        self._update_status_label(f"Stopped ({reason}). No keys.")

        # Emit final finished signal if it wasn't already running
        # This helps MultiModeWidget track the final state accurately.
        # (No need to check _is_running here, just emit to signal completion)
        self.generation_finished.emit(self.instance_id)
        log_debug(f"{instance_log_prefix}: Emitted final generation_finished signal after failing to find key.")







    def _set_ui_generating(self, generating: bool):
         """
         Helper to enable/disable UI elements based on generation state
         (worker running OR continuous loop active).
         """
         self._is_running = generating # Update the worker running state

         # Determine if config widgets should be disabled:
         # Disable if worker running OR if loop is intended to be active
         disable_config_general = self._is_running or self._continuous_loop_active
         log_debug(f"Instance {self.instance_id}: Setting UI Generating: worker_running={generating}, loop_active={self._continuous_loop_active}, disable_config_general={disable_config_general}")

         self.progress_bar.setVisible(self._is_running) # Progress bar only visible when worker is active

         # --- Disable Config Widgets based on disable_config_general ---
         # --- CORRECTED LINE: Key selection only disabled if looping/running ---
         self.api_key_combo.setEnabled(not disable_config_general)
         # --------------------------------------------------------------------
         self.manage_keys_button.setEnabled(not disable_config_general) # Manage always enabled when idle? Maybe disable too? Let's keep it enabled.

         # Model interaction enabled only if not disabled AND key is selected
         can_enable_model_widgets = not disable_config_general and self.api_key_combo.currentIndex() > 0
         self.model_combo.setEnabled(can_enable_model_widgets)
         self.refresh_models_button.setEnabled(can_enable_model_widgets)

         # Other config elements
         self.temperature_spin.setEnabled(not disable_config_general)
         self.top_p_spin.setEnabled(not disable_config_general)
         self.max_tokens_spin.setEnabled(not disable_config_general)
         self.reset_params_button.setEnabled(not disable_config_general)
         self.safety_button.setEnabled(not disable_config_general and SDK_AVAILABLE)
         self.prompt_combo.setEnabled(not disable_config_general)
         self.manage_prompts_button.setEnabled(not disable_config_general) # Keep enabled? OK for now.
         self.prompt_text_edit.setReadOnly(disable_config_general) # Readonly if running or loop active
         self.browse_save_path_button.setEnabled(not disable_config_general)
         self.clear_save_path_button.setEnabled(not disable_config_general and bool(self._custom_save_path))
         self.remove_button.setEnabled(not disable_config_general) # Cannot remove while loop active or running

         # Image/Sequence buttons disabled if loop active or running
         model_supports_images = False
         if not disable_config_general and self.model_combo.isEnabled():
              model_name = self.model_combo.itemData(self.model_combo.currentIndex())
              if model_name and self._current_api_key_name:
                    models_for_current_key = self.gemini_handler.available_models_cache.get(self._current_api_key_name, [])
                    model_info = next((m for m in models_for_current_key if m['name'] == model_name), None)
                    model_supports_images = model_info.get('likely_image_support', False) if model_info else False
         self.add_image_button.setEnabled(not disable_config_general and model_supports_images)
         image_list = self._sequential_image_queue if self._sequential_mode_enabled else self._selected_image_paths
         self.clear_images_button.setEnabled(not disable_config_general and bool(image_list))
         self.sequential_image_checkbox.setEnabled(not disable_config_general) # Disable if loop active or running

         # Checkboxes: Enable unless worker is active or loop active
         self.continuous_checkbox.setEnabled(not disable_config_general)
         self.autosave_checkbox.setEnabled(not disable_config_general)
         # --- End Disable Config Widgets ---

         # --- Update Start/Stop Button State ---
         # Call the dedicated helper function to handle text, tooltip, enabled state
         self._update_button_style()




    def _display_image(self, image_bytes: bytes) -> bool:
         """Safely displays image bytes in the result label."""
         try:
              qimage = QImage.fromData(image_bytes)
              if qimage.isNull():
                   log_error(f"Instance {self.instance_id}: Failed to create QImage from received result bytes.")
                   self.result_image_label.setText("Display Error")
                   self._full_result_pixmap = None
                   self._thumbnail_loaded = False
                   return False # Indicate failure

              pixmap = QPixmap.fromImage(qimage)
              self._full_result_pixmap = pixmap # Store original for resize
              self._thumbnail_loaded = False # Displaying full image clears thumb state
              self.result_image_label.setToolTip(f"Result ({pixmap.width()}x{pixmap.height()})")
              self._scale_and_set_pixmap() # Scale initially
              self.result_image_label.setText("") # Clear any placeholder text
              log_info(f"Instance {self.instance_id}: Full result image displayed.")
              return True # Indicate success

         except Exception as e:
              log_error(f"Instance {self.instance_id}: Failed to display result image: {e}", exc_info=True)
              self.result_image_label.setText("Display Error")
              self._full_result_pixmap = None
              self._thumbnail_loaded = False
              return False # Indicate failure



    def _scale_and_set_pixmap(self):
         """Scales the stored _full_result_pixmap to fit the label."""
         if self._thumbnail_loaded:
                #log_debug(f"Instance {self.instance_id}: Thumbnail loaded, skipping full pixmap scaling.")
                return # Don't scale thumbnails on resize         
         if not self._full_result_pixmap or self._full_result_pixmap.isNull():
              # self.result_image_label.setText("No Img") # Keep existing text if no pixmap
              return

         label_size = self.result_image_label.size()
         if label_size.width() <= 1 or label_size.height() <= 1: # Check for valid size
             #log_debug(f"Instance {self.instance_id}: Invalid label size for scaling, using original.")
             self.result_image_label.setPixmap(self._full_result_pixmap)
             return

         scaled_pixmap = self._full_result_pixmap.scaled(label_size,
                                                        Qt.AspectRatioMode.KeepAspectRatio,
                                                        Qt.TransformationMode.SmoothTransformation)
         current_pm = self.result_image_label.pixmap()
         # Avoid flicker if pixmap is effectively the same
         if not current_pm or scaled_pixmap.cacheKey() != current_pm.cacheKey():
              self.result_image_label.setPixmap(scaled_pixmap)

    def resizeEvent(self, event):
        """Handle resizing of the widget to rescale the image."""
        super().resizeEvent(event)
        # Throttle resize events slightly? Maybe not necessary.
        self._scale_and_set_pixmap()



    def _auto_save_result(self,
                          image_bytes: bytes,
                          image_mime: Optional[str],
                          text_result: str,
                          unresolved_prompt: str,
                          resolved_prompt: str,
                          resolved_values_by_name: Dict[str, List[str]], # Keep param
                          image_filename_context: Optional[str] = None,
                          filename_wildcard_values: Optional[Dict[str, str]] = None):
        instance_log_prefix = f"Instance {self.instance_id}"
        log_info(f"{instance_log_prefix}: --- _auto_save_result CALLED ---")
        log_info(f"[{instance_log_prefix} AUTOSAVE ENTRY] received resolved_values_by_name map: {resolved_values_by_name}") # Log received map
        try:
            ext = mimetypes.guess_extension(image_mime) if image_mime else ".png"
            if not ext or ext == ".jpe": ext = ".jpg"
            if ext == ".jpeg": ext = ".jpg"

            model_name = self.model_combo.itemData(self.model_combo.currentIndex()) or "unknown_model"
            api_key_name = self._current_api_key_name or "unknown_key"
            log_info(f"[{instance_log_prefix} AUTOSAVE CONTEXT PREP] Using map: {resolved_values_by_name}") # Log map used for context
            context_data = {
                "timestamp": time.time(), "model_name": model_name, "api_key_name": api_key_name,
                "instance_id": self.instance_id, "unresolved_prompt": unresolved_prompt,
                "resolved_prompt": resolved_prompt, "image_filename": image_filename_context,
                "resolved_wildcards_by_name": resolved_values_by_name, # Pass received map
                **(filename_wildcard_values or {})
            }
            log_debug(f"{instance_log_prefix}: Context prepared: {{k:(v[:50]+'...' if isinstance(v, str) and len(v)>50 else v) for k,v in context_data.items()}}")

            active_pattern_name = self.settings_service.get_setting(constants.ACTIVE_FILENAME_PATTERN_NAME_KEY, constants.DEFAULT_FILENAME_PATTERN_NAME)
            saved_patterns = self.settings_service.get_saved_filename_patterns()
            pattern = saved_patterns.get(active_pattern_name, constants.DEFAULT_FILENAME_PATTERN)
            log_debug(f"{instance_log_prefix}: Filename pattern: '{pattern}'")
            if self._custom_save_path and self._custom_save_path.is_dir(): save_dir = self._custom_save_path
            else: save_dir = constants.DATA_DIR / "output"
            log_debug(f"{instance_log_prefix}: Save directory: {save_dir}")

            if not hasattr(self, 'filename_generator') or self.filename_generator is None: log_error(f"{instance_log_prefix}: FilenameGen missing."); self._update_status_label("Save Error: FN Gen miss."); QTimer.singleShot(3000, lambda: self._update_status_label("Ready.") if not self._is_running else None); return
            log_info(f"[{instance_log_prefix} PRE-FILENAME GEN] Passing context with map: {context_data.get('resolved_wildcards_by_name')}") # Log map passed to generator
            image_filename_path: Path = self.filename_generator.generate_filename(pattern, context_data, save_dir, ext)
            text_filename_path = image_filename_path.with_suffix(".txt")
            save_dir.mkdir(parents=True, exist_ok=True)

            if ImageProcessor.save_image(image_bytes, image_filename_path):
                log_info(f"{instance_log_prefix}: Image saved: {image_filename_path.name}")
                if self.settings_service.get_setting(constants.EMBED_METADATA_ENABLED, constants.DEFAULT_EMBED_METADATA_ENABLED):
                    log_debug(f"{instance_log_prefix}: Embedding metadata..."); embed_success = ImageProcessor.embed_prompts_in_image(image_filename_path, unresolved_prompt, resolved_prompt)
                    if not embed_success: log_warning(f"{instance_log_prefix}: Metadata embed failed: {image_filename_path.name}")
                else: log_debug(f"{instance_log_prefix}: Embed Metadata disabled.")
            else: log_error(f"{instance_log_prefix}: Image save failed: {image_filename_path.name}"); self._update_status_label("Image Save Failed."); QTimer.singleShot(3000, lambda: self._update_status_label("Ready.") if not self._is_running else None)

            if self.settings_service.get_setting(constants.SAVE_TEXT_FILE_ENABLED, constants.DEFAULT_SAVE_TEXT_FILE_ENABLED):
                log_debug(f"{instance_log_prefix}: Saving text file...");
                try:
                    text_filename_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(text_filename_path, "w", encoding="utf-8") as f:
                        f.write(f"--- Instance ID: {self.instance_id} ---\n"); f.write(f"--- API Key Name: {api_key_name} ---\n"); f.write(f"--- Model: {model_name} ---\n\n")
                        f.write(f"--- Unresolved Prompt ---\n{unresolved_prompt}\n\n"); f.write(f"--- Resolved Prompt ---\n{resolved_prompt}\n\n")
                        f.write(f"--- Generated Text ---\n{text_result if text_result else '[No text generated]'}")
                    log_info(f"{instance_log_prefix}: Text saved: {text_filename_path.name}")
                except OSError as e: log_error(f"{instance_log_prefix}: Text save failed {text_filename_path.name}: {e}"); self._update_status_label("Text Save Failed."); QTimer.singleShot(3000, lambda: self._update_status_label("Ready.") if not self._is_running else None)
            else: log_debug(f"{instance_log_prefix}: Save Text File disabled.")

        except Exception as e: log_error(f"{instance_log_prefix}: Auto-save error: {e}", exc_info=True); self._update_status_label("Save Error."); QTimer.singleShot(3000, lambda: self._update_status_label("Ready.") if not self._is_running else None)
        finally: log_info(f"{instance_log_prefix}: --- _auto_save_result COMPLETE ---")


    def closeEvent(self, event):
        """Handle close event for the instance widget. Handled by parent deletion."""
        # In MultiMode, individual instance widgets are deleted by the MultiModeWidget
        # when they are removed from the layout, or when MultiModeWidget itself is deleted
        # (e.g., when switching modes or closing the main app).
        # The cleanup logic for the thread is handled in _remove_self.
        # If MultiModeWidget directly calls deleteLater() on this widget,
        # _remove_self should be called *before* deleteLater().
        # Add a check here just in case, though the MultiModeWidget should manage it.
        log_debug(f"Instance {self.instance_id}: closeEvent received.")
        if self._is_running or self._continuous_loop_active:
            log_warning(f"Instance {self.instance_id}: closeEvent while running/looping. Requesting stop.")
            # This might not be ideal timing in a GUI close event.
            # Relying on MultiModeWidget's shutdown_mode and _remove_instance being called first is preferred.
            self.stop_generation() # Request stop

        # It's generally safe to accept the close event here, as the actual
        # deletion and thread cleanup are handled by the parent's logic
        # which should call _remove_self and then deleteLater.
        event.accept()