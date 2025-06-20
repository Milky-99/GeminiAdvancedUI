# -*- coding: utf-8 -*-

from typing import Optional, Dict, List, Tuple

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QComboBox, QDialogButtonBox, QGroupBox,
    QLabel, QSizePolicy
)
from PyQt6.QtCore import Qt

# --- Project Imports ---
from utils.logger import log_debug, log_warning, log_error

# Attempt to import SDK types, handle gracefully if unavailable
try:
    from google.genai import types as google_types
    SDK_TYPES_AVAILABLE = True

    _STANDARD_CATEGORIES = {
        google_types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        google_types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        google_types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        google_types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT
    }
    AVAILABLE_HARM_CATEGORIES: List[Tuple[str, google_types.HarmCategory]] = [
        (cat.name.replace("HARM_CATEGORY_", "").replace("_", " ").title(), cat)
        for cat in google_types.HarmCategory
        # Only include the standard 4 categories
        if cat in _STANDARD_CATEGORIES
    ]

    AVAILABLE_THRESHOLDS: List[Tuple[str, google_types.HarmBlockThreshold]] = [
        # (Threshold definitions remain the same)
        ("API Default (Unspecified)", google_types.HarmBlockThreshold.HARM_BLOCK_THRESHOLD_UNSPECIFIED),
        ("Block None (Allow All)", google_types.HarmBlockThreshold.BLOCK_NONE),
        ("Block Only High", google_types.HarmBlockThreshold.BLOCK_ONLY_HIGH),
        ("Block Medium & Above", google_types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE),
        ("Block Low & Above (Max Safety)", google_types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE),
    ]

except ImportError:
    # (Keep the except block as is)
    SDK_TYPES_AVAILABLE = False
    log_error("Google GenAI SDK Types not found. Safety Settings Dialog will be disabled or limited.")
    # Define dummy types/lists if SDK is not found
    class DummyHarmCategory: HARM_CATEGORY_UNSPECIFIED = None
    class DummyHarmThreshold: HARM_BLOCK_THRESHOLD_UNSPECIFIED = None
    google_types = type('DummyTypes', (), {'HarmCategory': DummyHarmCategory, 'HarmBlockThreshold': DummyHarmThreshold})()
    AVAILABLE_HARM_CATEGORIES = []
    AVAILABLE_THRESHOLDS = [("API Default (Unspecified)", None)]

class SafetySettingsDialog(QDialog):
    """Dialog for configuring content safety settings."""

    def __init__(self,
                 current_settings: Optional[Dict[google_types.HarmCategory, google_types.HarmBlockThreshold]],
                 parent=None):
        super().__init__(parent)
        self._initial_settings = current_settings if current_settings else {}
        self._category_combos: Dict[google_types.HarmCategory, QComboBox] = {}

        self.setWindowTitle("Configure Safety Settings")
        self.setMinimumWidth(450)
        self.setModal(True)

        self._setup_ui()
        self._load_settings()
        self._connect_signals()

        if not SDK_TYPES_AVAILABLE or not AVAILABLE_HARM_CATEGORIES:
            self.setEnabled(False)
            self.setWindowTitle("Configure Safety Settings (Disabled - SDK Missing)")

    def _setup_ui(self):
        self.setObjectName("safetySettingsDialog") # Name the dialog itself
        main_layout = QVBoxLayout(self)
        main_layout.setObjectName("mainLayoutSafetySettings")

        info_label = QLabel(
            "Configure the threshold for blocking potentially harmful content.\n"
            "'API Default' uses Google's standard safety settings."
        )
        info_label.setObjectName("infoLabelSafetySettings")
        info_label.setWordWrap(True)
        main_layout.addWidget(info_label)

        settings_group = QGroupBox("Harm Category Thresholds")
        settings_group.setObjectName("settingsGroupSafetySettings")
        form_layout = QFormLayout(settings_group)
        form_layout.setObjectName("formLayoutSafetySettings")
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Create a combo box for each harm category
        if SDK_TYPES_AVAILABLE:
             for display_name, category_enum in AVAILABLE_HARM_CATEGORIES:
                 # Sanitize the category name for use in an object name
                 safe_category_name = category_enum.name.replace("HARM_CATEGORY_", "").lower()

                 category_label = QLabel(f"{display_name}:") # Create label explicitly if needed for styling
                 category_label.setObjectName(f"label_{safe_category_name}")

                 combo = QComboBox()
                 combo.setObjectName(f"combo_{safe_category_name}") # e.g., combo_hate_speech
                 combo.setToolTip(f"Set blocking threshold for {display_name} content.")
                 for thresh_name, thresh_enum in AVAILABLE_THRESHOLDS:
                     combo.addItem(thresh_name, thresh_enum) # Store enum value as data

                 self._category_combos[category_enum] = combo
                 # form_layout.addRow(f"{display_name}:", combo) # Original
                 form_layout.addRow(category_label, combo) # Use label widget

        else:
             # Show disabled message if SDK types not available
             disabled_label = QLabel("Safety settings unavailable (google-genai library missing).")
             disabled_label.setObjectName("disabledLabelSafetySettings")
             disabled_label.setStyleSheet("color: grey;")
             form_layout.addRow(disabled_label)


        main_layout.addWidget(settings_group)

        # --- Dialog Buttons ---
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel |
            QDialogButtonBox.StandardButton.Reset # Add Reset button
        )
        self.button_box.setObjectName("buttonBoxSafetySettings")
        main_layout.addWidget(self.button_box)

        # Add object names to standard buttons (optional, but allows styling)
        ok_button = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button: ok_button.setObjectName("okButtonSafetySettings")
        cancel_button = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_button: cancel_button.setObjectName("cancelButtonSafetySettings")
        reset_button = self.button_box.button(QDialogButtonBox.StandardButton.Reset)
        if reset_button: reset_button.setObjectName("resetButtonSafetySettings")

    def _load_settings(self):
        """Load the current settings into the combo boxes."""
        if not SDK_TYPES_AVAILABLE:
            return

        log_debug(f"Loading safety settings into dialog: {self._initial_settings}")
        for category_enum, combo in self._category_combos.items():
            current_threshold = self._initial_settings.get(category_enum, google_types.HarmBlockThreshold.HARM_BLOCK_THRESHOLD_UNSPECIFIED) # Default to Unspecified
            index = combo.findData(current_threshold)
            if index != -1:
                combo.setCurrentIndex(index)
            else:
                # If the stored value isn't in our list (shouldn't happen), default to Unspecified
                log_warning(f"Could not find threshold '{current_threshold}' in combo box for category '{category_enum}'. Defaulting.")
                index_unspecified = combo.findData(google_types.HarmBlockThreshold.HARM_BLOCK_THRESHOLD_UNSPECIFIED)
                combo.setCurrentIndex(index_unspecified if index_unspecified != -1 else 0)

    def _connect_signals(self):
        """Connect signals."""
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        reset_button = self.button_box.button(QDialogButtonBox.StandardButton.Reset)
        if reset_button:
            reset_button.clicked.connect(self._reset_settings)

    def _reset_settings(self):
        """Resets all combo boxes to the API Default (Unspecified)."""
        if not SDK_TYPES_AVAILABLE:
            return
        log_debug("Resetting safety settings to API defaults.")
        default_enum = google_types.HarmBlockThreshold.HARM_BLOCK_THRESHOLD_UNSPECIFIED
        for combo in self._category_combos.values():
            index = combo.findData(default_enum)
            if index != -1:
                combo.setCurrentIndex(index)

    def get_selected_settings(self) -> Optional[Dict[google_types.HarmCategory, google_types.HarmBlockThreshold]]:
        """Returns the configured settings as a dictionary."""
        if not SDK_TYPES_AVAILABLE:
            return None

        selected: Dict[google_types.HarmCategory, google_types.HarmBlockThreshold] = {}
        for category_enum, combo in self._category_combos.items():
            threshold_enum = combo.currentData() # Get the stored enum value
            # Only include settings that are *not* unspecified (API default)
            if threshold_enum != google_types.HarmBlockThreshold.HARM_BLOCK_THRESHOLD_UNSPECIFIED:
                 if isinstance(threshold_enum, google_types.HarmBlockThreshold): # Basic type check
                     selected[category_enum] = threshold_enum
                 else:
                      log_error(f"Invalid data type found in safety combo box for {category_enum}: {type(threshold_enum)}")

        return selected if selected else None # Return None if all are unspecified

    def accept(self):
        """Closes the dialog returning Accepted code."""
        log_debug(f"Safety settings dialog accepted. Settings: {self.get_selected_settings()}")
        super().accept()

    def reject(self):
        """Closes the dialog returning Rejected code."""
        log_debug("Safety settings dialog cancelled.")
        super().reject()