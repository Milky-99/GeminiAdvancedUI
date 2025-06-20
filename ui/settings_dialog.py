# -*- coding: utf-8 -*-

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QComboBox, QCheckBox, QSpinBox,
    QDialogButtonBox, QGroupBox, QLabel, QPushButton, QHBoxLayout
)
from PyQt6.QtCore import Qt, pyqtSlot

# --- Project Imports ---
from .filename_pattern_manager_dialog import FilenamePatternManagerDialog
from .components.filename_settings_widget import FilenameSettingsWidget
from utils import constants
from core.settings_service import SettingsService
from utils.logger import log_debug, log_info
from utils.helpers import discover_custom_themes
from utils.helpers import discover_custom_themes

class SettingsDialog(QDialog):
    """Dialog for configuring application settings."""

    def __init__(self, settings_service: SettingsService, parent=None):
        super().__init__(parent)
        self.settings_service = settings_service

        self.setWindowTitle("Application Settings")
        self.setMinimumWidth(600)
        self.setMinimumHeight(600)
        self._setup_ui()
        self._load_settings()
        self._connect_signals()


    def _setup_ui(self):
        # --- Base Dialog ---
        self.setObjectName("settingsDialog") # Name the dialog itself
        layout = QVBoxLayout(self)
        layout.setObjectName("mainLayoutSettingsDialog") # Name the main layout

        # --- General Settings ---
        general_group = QGroupBox("General")
        general_group.setObjectName("generalGroupSettingsDialog") # Name the group box
        form_layout = QFormLayout(general_group)
        form_layout.setObjectName("generalFormLayoutSettingsDialog") # Name the form layout

        # Theme
        theme_label = QLabel("Theme:") # Explicit label
        theme_label.setObjectName("themeLabelSettingsDialog") # Name the label
        self.theme_combo = QComboBox()
        self.theme_combo.setObjectName("themeComboSettingsDialog")
        form_layout.addRow(theme_label, self.theme_combo) # Add explicit label and combo

        # Logging Checkbox (implicitly includes label)
        self.logging_checkbox = QCheckBox("Enable Logging")
        self.logging_checkbox.setObjectName("loggingCheckboxSettingsDialog")
        self.logging_checkbox.setToolTip("Enable detailed logging to file and console.")
        form_layout.addRow(self.logging_checkbox) # Checkbox acts as its own label row

        # Auto-Save Checkbox
        self.auto_save_checkbox = QCheckBox("Enable Auto-Save")
        self.auto_save_checkbox.setObjectName("autoSaveCheckboxSettingsDialog")
        self.auto_save_checkbox.setToolTip("Automatically save generated images/text to the 'output' folder.")
        form_layout.addRow(self.auto_save_checkbox)

        # Save Text File Checkbox
        self.save_text_file_checkbox = QCheckBox("Save Text File with Image")
        self.save_text_file_checkbox.setObjectName("saveTextFileCheckboxSettingsDialog")
        self.save_text_file_checkbox.setToolTip("Save a .txt file containing prompts and info alongside the generated image.")
        form_layout.addRow(self.save_text_file_checkbox)

        # Embed Metadata Checkbox
        self.embed_metadata_checkbox = QCheckBox("Embed Prompts in Image Metadata")
        self.embed_metadata_checkbox.setObjectName("embedMetadataCheckboxSettingsDialog")
        self.embed_metadata_checkbox.setToolTip("Embed unresolved and resolved prompts into the image's metadata (PNG/JPEG).")
        form_layout.addRow(self.embed_metadata_checkbox)

        layout.addWidget(general_group)

        # --- Request Settings ---
        request_group = QGroupBox("Request Handling")
        request_group.setObjectName("requestGroupSettingsDialog") # Name the group box
        req_form_layout = QFormLayout(request_group)
        req_form_layout.setObjectName("requestFormLayoutSettingsDialog") # Name the form layout

        # Request Delay
        request_delay_label = QLabel("Delay Between Requests:") # Explicit label
        request_delay_label.setObjectName("requestDelayLabelSettingsDialog") # Name the label
        self.request_delay_spin = QSpinBox()
        self.request_delay_spin.setObjectName("requestDelaySpinSettingsDialog")
        self.request_delay_spin.setRange(0, 60)
        self.request_delay_spin.setSuffix(" s")
        self.request_delay_spin.setToolTip("Delay between consecutive requests (in seconds). Set 0 for no delay.")
        req_form_layout.addRow(request_delay_label, self.request_delay_spin) # Add explicit label and spin

        # Retry Count
        retry_count_label = QLabel("Retry Count:") # Explicit label
        retry_count_label.setObjectName("retryCountLabelSettingsDialog") # Name the label
        self.retry_count_spin = QSpinBox()
        self.retry_count_spin.setObjectName("retryCountSpinSettingsDialog")
        self.retry_count_spin.setRange(0, 10)
        self.retry_count_spin.setToolTip("Number of retries on rate limit or temporary errors.")
        req_form_layout.addRow(retry_count_label, self.retry_count_spin) # Add explicit label and spin

        # Retry Delay
        retry_delay_label = QLabel("Retry Delay:") # Explicit label
        retry_delay_label.setObjectName("retryDelayLabelSettingsDialog") # Name the label
        self.retry_delay_spin = QSpinBox()
        self.retry_delay_spin.setObjectName("retryDelaySpinSettingsDialog")
        self.retry_delay_spin.setRange(1, 300)
        self.retry_delay_spin.setSuffix(" s")
        self.retry_delay_spin.setToolTip("Delay before retrying a failed request (in seconds).")
        req_form_layout.addRow(retry_delay_label, self.retry_delay_spin) # Add explicit label and spin

        layout.addWidget(request_group)

        # --- Filename Settings Group ---
        filename_group = QGroupBox("Filename Pattern")
        filename_group.setObjectName("filenameGroupSettingsDialog") # Name the group box
        filename_layout = QFormLayout(filename_group)
        filename_layout.setObjectName("filenameFormLayoutSettingsDialog") # Name the form layout

        # Pattern Selection ComboBox and Manage Button
        pattern_selection_layout = QHBoxLayout()
        pattern_selection_layout.setObjectName("patternSelectionLayoutSettingsDialog") # Name the HBox layout
        self.pattern_combo = QComboBox()
        self.pattern_combo.setObjectName("patternComboSettingsDialog")
        self.pattern_combo.setToolTip("Select the active filename pattern.")
        pattern_selection_layout.addWidget(self.pattern_combo, 1) # Give combo stretch

        self.manage_patterns_button = QPushButton("Manage...")
        self.manage_patterns_button.setObjectName("managePatternsButtonSettingsDialog")
        self.manage_patterns_button.setToolTip("Add, edit, or remove saved filename patterns.")
        pattern_selection_layout.addWidget(self.manage_patterns_button)

        # Add the combo+button HBox to the FormLayout
        active_pattern_label = QLabel("Active Pattern:") # Explicit label
        active_pattern_label.setObjectName("activePatternLabelSettingsDialog") # Name the label
        filename_layout.addRow(active_pattern_label, pattern_selection_layout) # Add explicit label and HBox

        # Display the actual pattern string
        current_pattern_label = QLabel("Current Pattern:") # Explicit label
        current_pattern_label.setObjectName("currentPatternLabelSettingsDialog") # Name the label
        self.active_pattern_display = QLabel("...")
        self.active_pattern_display.setObjectName("activePatternDisplaySettingsDialog")
        self.active_pattern_display.setWordWrap(True)
        self.active_pattern_display.setStyleSheet("color: grey; font-style: italic;")
        self.active_pattern_display.setToolTip("The actual pattern string for the selected name.")
        filename_layout.addRow(current_pattern_label, self.active_pattern_display) # Add explicit label and display label

        layout.addWidget(filename_group)

        # --- Dialog Buttons ---
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.setObjectName("buttonBoxSettingsDialog") # Name the button box

        # Add object names to standard buttons for potential styling
        ok_button = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button: ok_button.setObjectName("okButtonSettingsDialog")
        cancel_button = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_button: cancel_button.setObjectName("cancelButtonSettingsDialog")

        layout.addWidget(self.button_box)






    def _load_settings(self):
        self.theme_combo.clear()
        built_in_themes = ["Auto", "Light", "Dark"]
        self.theme_combo.addItems(built_in_themes)

        custom_themes = discover_custom_themes()
        if custom_themes:
            self.theme_combo.insertSeparator(len(built_in_themes))
            for theme_name, _ in custom_themes:
                self.theme_combo.addItem(theme_name)

        current_theme_setting = self.settings_service.get_setting("theme", constants.DEFAULT_THEME)
        if not current_theme_setting or self.theme_combo.findText(current_theme_setting) == -1:
            if current_theme_setting:
                 log_warning(f"Saved theme '{current_theme_setting}' is invalid or not found in available themes. Defaulting to Auto.")
            current_theme_setting = constants.DEFAULT_THEME
        self.theme_combo.setCurrentText(current_theme_setting)

        self.logging_checkbox.setChecked(
            self.settings_service.get_setting("logging_enabled", constants.DEFAULT_LOGGING_ENABLED)
        )
        self.auto_save_checkbox.setChecked(
            self.settings_service.get_setting("auto_save_enabled", constants.DEFAULT_AUTO_SAVE_ENABLED)
        )
        self.request_delay_spin.setValue(
            self.settings_service.get_setting("request_delay", constants.DEFAULT_REQUEST_DELAY)
        )
        self.retry_count_spin.setValue(
            self.settings_service.get_setting("retry_count", constants.DEFAULT_RETRY_COUNT)
        )
        self.retry_delay_spin.setValue(
            self.settings_service.get_setting("retry_delay", constants.DEFAULT_RETRY_DELAY)
        )
        self.save_text_file_checkbox.setChecked(
            self.settings_service.get_setting(constants.SAVE_TEXT_FILE_ENABLED, constants.DEFAULT_SAVE_TEXT_FILE_ENABLED)
        )
        self.embed_metadata_checkbox.setChecked(
            self.settings_service.get_setting(constants.EMBED_METADATA_ENABLED, constants.DEFAULT_EMBED_METADATA_ENABLED)
        )
        self._populate_pattern_combo()

    
    
    def _populate_pattern_combo(self):
         """Helper to load patterns into the combo box."""
         log_debug("Populating filename pattern combo box.")
         current_active_name = self.settings_service.get_setting(constants.ACTIVE_FILENAME_PATTERN_NAME_KEY)
         saved_patterns = self.settings_service.get_saved_filename_patterns()

         self.pattern_combo.blockSignals(True)
         self.pattern_combo.clear()

         selected_index = 0 # Default to first item
         sorted_names = sorted(saved_patterns.keys())
         for i, name in enumerate(sorted_names):
             # Store the pattern string as data associated with the name
             self.pattern_combo.addItem(name, saved_patterns[name])
             if name == current_active_name:
                 selected_index = i

         self.pattern_combo.setCurrentIndex(selected_index)
         self.pattern_combo.blockSignals(False)
         # Update display label manually after loading/setting index
         self._update_active_pattern_display()


    @pyqtSlot()
    def _update_active_pattern_display(self):
         """Updates the read-only label showing the current pattern string."""
         pattern_string = self.pattern_combo.currentData()
         if pattern_string:
              self.active_pattern_display.setText(pattern_string)
              self.active_pattern_display.setToolTip(f"Active Pattern: {pattern_string}")
         else:
              self.active_pattern_display.setText("[No pattern selected or loaded]")
              self.active_pattern_display.setToolTip("")
    
    
    def _connect_signals(self):
        """Connect signals."""
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.manage_patterns_button.clicked.connect(self._open_filename_pattern_manager) # Connect manage button
        self.pattern_combo.currentIndexChanged.connect(self._update_active_pattern_display)


    @pyqtSlot()
    def _open_filename_pattern_manager(self):
        """Opens the Filename Pattern Manager dialog."""
        log_info("Opening Filename Pattern Manager dialog.")
        dialog = FilenamePatternManagerDialog(self.settings_service, self)
        # Optional: Connect signal if manager modifies patterns, to refresh combo live
        # dialog.patterns_updated.connect(self._populate_pattern_combo)
        dialog.exec() # Blocks until closed
        log_info("Filename Pattern Manager dialog closed.")
        # Refresh the combo box in case patterns were added/deleted/renamed
        self._populate_pattern_combo()


    def accept(self):
        """Save settings when OK is clicked."""
        log_debug("Saving settings from dialog.")
        self.settings_service.set_setting("theme", self.theme_combo.currentText(), save=False)
        self.settings_service.set_setting("logging_enabled", self.logging_checkbox.isChecked(), save=False)
        self.settings_service.set_setting("auto_save_enabled", self.auto_save_checkbox.isChecked(), save=False)
        self.settings_service.set_setting("request_delay", self.request_delay_spin.value(), save=False)
        self.settings_service.set_setting("retry_count", self.retry_count_spin.value(), save=False)
        self.settings_service.set_setting("retry_delay", self.retry_delay_spin.value(), save=False)
        self.settings_service.set_setting(constants.SAVE_TEXT_FILE_ENABLED, self.save_text_file_checkbox.isChecked(), save=False)
        self.settings_service.set_setting(constants.EMBED_METADATA_ENABLED, self.embed_metadata_checkbox.isChecked(), save=False)
        # Removed the line referencing self.filename_widget

        # Save filename pattern selection
        selected_pattern_name = self.pattern_combo.currentText()
        # Use set_setting to handle updating both name and pattern string keys
        self.settings_service.set_setting(constants.ACTIVE_FILENAME_PATTERN_NAME_KEY, selected_pattern_name, save=False)

        # Perform final save
        self.settings_service._save_settings() # Call private save method once
        super().accept()

    def reject(self):
        """Discard changes when Cancel is clicked."""
        log_debug("Settings dialog cancelled.")
        super().reject()