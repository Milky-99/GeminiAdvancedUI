# --- START OF FILE ui/components/filename_settings_widget.py ---

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QListWidget,
    QListWidgetItem, QGroupBox, QApplication, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from utils import constants
from utils.logger import log_debug, log_info
# --- MODIFIED IMPORT ---
from utils.helpers import HelpDialog # Import the centralized dialog

# Define placeholders - ideally share this with the service or load dynamically
AVAILABLE_PLACEHOLDERS = {
    "{date}": "YYYYMMDD",
    "{time}": "HHMMSS",
    "{datetime}": "YYYYMMDD_HHMMSS",
    "{model}": "Model Name (sanitized)",
    "{key_name}": "API Key Name (sanitized)",
    "{instance_id}": "Multi-mode Instance ID ('NA' if single)",
    "{prompt_hash}": "Short hash of resolved prompt",
    "{unresolved_prompt_hash}": "Short hash of unresolved prompt",
    "{prompt_start:N}": "First N chars of resolved prompt (default 50)",
    "{prompt_end:N}": "Last N chars of resolved prompt (default 50)",
    "{sequence_number}": "Number added for uniqueness (_001, _002, etc.)",
    "{wildcard_value:N}": "The resolved value of the Nth wildcard (1-based index).",
    "{wc_unresolved:name}": "The name of a wildcard file used (e.g., colors).",
    "{wc_resolved:name}": "The resolved value(s) for a wildcard name (e.g., blue_red).",
}

# --- HelpDialog class has been REMOVED from this file ---

class FilenameSettingsWidget(QWidget):
    """Widget for configuring the filename generation pattern."""
    pattern_changed = pyqtSignal(str)

    def __init__(self, initial_pattern: str, parent=None):
        super().__init__(parent)
        self._setup_ui(initial_pattern)
        self._connect_signals()

    def _setup_ui(self, initial_pattern):
        log_debug("--- FilenameSettingsWidget _setup_ui called ---")
        self.setObjectName("filenameSettingsWidget")
        
        main_layout = QVBoxLayout(self)
        main_layout.setObjectName("mainLayoutFilenameSettings")
        main_layout.setContentsMargins(0,0,0,0)

        group = QGroupBox("Filename Pattern")
        group.setObjectName("patternGroupFilenameSettings")
        
        group_layout = QVBoxLayout(group)
        group_layout.setObjectName("patternGroupLayoutFilenameSettings")

        pattern_layout = QHBoxLayout()
        pattern_layout.setObjectName("patternHBoxLayoutFilenameSettings")
        
        pattern_label = QLabel("Pattern:")
        pattern_label.setObjectName("patternLabelFilenameSettings")
        pattern_layout.addWidget(pattern_label)
        
        self.pattern_edit = QLineEdit()
        self.pattern_edit.setObjectName("patternEditFilenameSettings")
        self.pattern_edit.setText(initial_pattern)
        self.pattern_edit.setPlaceholderText("e.g., {date}_{time}_{model}_{prompt_hash}")
        self.pattern_edit.setToolTip("Define the filename structure using placeholders.")
        pattern_layout.addWidget(self.pattern_edit, 1)

        self.reset_button = QPushButton("Reset")
        self.reset_button.setObjectName("resetButtonFilenameSettings")
        self.reset_button.setToolTip("Reset to default pattern.")
        pattern_layout.addWidget(self.reset_button)

        self.help_button = QPushButton("Help")
        self.help_button.setObjectName("helpButtonFilenameSettings")
        self.help_button.setToolTip("Show help for placeholders and syntax.")
        pattern_layout.addWidget(self.help_button)

        group_layout.addLayout(pattern_layout)

        placeholder_label = QLabel("Available Placeholders (Double-click to insert):")
        placeholder_label.setObjectName("placeholderLabelFilenameSettings")
        group_layout.addWidget(placeholder_label)
        
        self.placeholder_list = QListWidget()
        self.placeholder_list.setObjectName("placeholderListFilenameSettings")
        self.placeholder_list.setMaximumHeight(100)
        self.placeholder_list.setToolTip("Double-click a placeholder to add it to the pattern.")
        for tag, desc in AVAILABLE_PLACEHOLDERS.items():
            item = QListWidgetItem(f"{tag} : {desc}")
            item.setData(Qt.ItemDataRole.UserRole, tag)
            self.placeholder_list.addItem(item)
        group_layout.addWidget(self.placeholder_list)

        main_layout.addWidget(group)
        log_debug("--- FilenameSettingsWidget _setup_ui finished ---")

    def _connect_signals(self):
        log_debug("--- FilenameSettingsWidget _connect_signals called ---")
        self.pattern_edit.textChanged.connect(self.pattern_changed)
        self.placeholder_list.itemDoubleClicked.connect(self._insert_placeholder)
        self.reset_button.clicked.connect(self._reset_pattern)
        self.help_button.clicked.connect(self._show_help) 
        log_debug("--- FilenameSettingsWidget _connect_signals finished ---")

    def _insert_placeholder(self, item: QListWidgetItem):
        tag = item.data(Qt.ItemDataRole.UserRole)
        if tag:
            self.pattern_edit.insert(tag)
            log_debug(f"Inserted placeholder: {tag}")

    def _reset_pattern(self):
        """Resets the pattern to a default value."""
        default_pattern = constants.DEFAULT_FILENAME_PATTERN
        self.pattern_edit.setText(default_pattern)
        QApplication.clipboard().setText(default_pattern)
        log_info(f"Filename pattern reset to default: {default_pattern}")

    def get_pattern(self) -> str:
        return self.pattern_edit.text().strip()

    def set_pattern(self, pattern: str):
        self.pattern_edit.setText(pattern)

    @pyqtSlot()
    def _show_help(self):
        """Displays the filename pattern help text using the centralized dialog."""
        log_debug("Showing filename pattern help dialog.")
        # --- USES THE CENTRALIZED DIALOG NOW ---
        help_dialog = HelpDialog("Filename Pattern Help", constants.FILENAME_HELP_TEXT, self)
        help_dialog.exec()

# --- END OF FILE ui/components/filename_settings_widget.py ---