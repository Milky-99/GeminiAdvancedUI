# --- START OF FILE ui/filename_pattern_manager_dialog.py ---

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, QTextEdit,
    QPushButton, QLineEdit, QLabel, QDialogButtonBox, QMessageBox, QSplitter, QWidget, QGroupBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer

from core.settings_service import SettingsService
from utils.logger import log_debug, log_error, log_warning, log_info
from utils.helpers import show_error_message
from utils import constants # For default name/pattern
from .components.filename_settings_widget import FilenameSettingsWidget # Reuse this for editor

class FilenamePatternManagerDialog(QDialog):
    """Dialog for managing saved filename patterns."""

    # Signal to indicate patterns might have changed (optional, useful for SettingsDialog)
    patterns_updated = pyqtSignal()

    def __init__(self, settings_service: SettingsService, parent=None):
        super().__init__(parent)
        self.settings_service = settings_service
        self._selected_pattern_name: str | None = None

        self.setWindowTitle("Filename Pattern Manager")
        self.setMinimumSize(700, 500) # Make it reasonably large

        self._setup_ui()
        self._connect_signals()
        self._load_patterns()


    def _setup_ui(self):
        self.setObjectName("filenamePatternManagerDialog") # Name the dialog
        main_layout = QVBoxLayout(self)
        main_layout.setObjectName("mainLayoutFilenameManager")
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("splitterFilenameManager")

        # --- Left Pane: List ---
        left_pane = QWidget()
        left_pane.setObjectName("leftPaneFilenameManager")
        left_layout = QVBoxLayout(left_pane)
        left_layout.setObjectName("leftLayoutFilenameManager")
        # left_pane_label = QLabel("Saved Patterns:")
        left_layout.addWidget(QLabel("Saved Patterns:"))
        self.pattern_list_widget = QListWidget()
        self.pattern_list_widget.setObjectName("patternListWidgetFilenameManager")
        self.pattern_list_widget.setToolTip("Select a pattern to view/edit/delete.")
        left_layout.addWidget(self.pattern_list_widget)

        list_button_layout = QHBoxLayout()
        list_button_layout.setObjectName("listButtonLayoutFilenameManager")
        self.add_button = QPushButton("Add New")
        self.add_button.setObjectName("addButtonFilenameManager")
        self.delete_button = QPushButton("Delete Selected")
        self.delete_button.setObjectName("deleteButtonFilenameManager")
        list_button_layout.addWidget(self.add_button)
        list_button_layout.addWidget(self.delete_button)
        left_layout.addLayout(list_button_layout)
        splitter.addWidget(left_pane)

        # --- Right Pane: Details ---
        right_pane = QWidget()
        right_pane.setObjectName("rightPaneFilenameManager")
        right_layout = QVBoxLayout(right_pane)
        right_layout.setObjectName("rightLayoutFilenameManager")
        # right_pane_label = QLabel("Pattern Details:")
        right_layout.addWidget(QLabel("Pattern Details:"))

        # name_label = QLabel("Name:")
        right_layout.addWidget(QLabel("Name:"))
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("nameEditFilenameManager")
        self.name_edit.setPlaceholderText("Enter a unique name for the pattern.")
        right_layout.addWidget(self.name_edit)

        # Use FilenameSettingsWidget for the pattern editor and help
        self.pattern_editor_widget = FilenameSettingsWidget(constants.DEFAULT_FILENAME_PATTERN)
        # Name the nested widget itself for potential container styling
        self.pattern_editor_widget.setObjectName("patternEditorWidgetFilenameManager")
        # Access internal elements of FilenameSettingsWidget IF necessary for extreme styling
        # Example: self.pattern_editor_widget.findChild(QLineEdit, "pattern_edit").setStyleSheet(...)
        # Or rely on QSS selectors like: FilenamePatternManagerDialog FilenameSettingsWidget QLineEdit { ... }

        # We only need the editor part, hide the group box title if desired
        pattern_editor_group_box = self.pattern_editor_widget.findChild(QGroupBox)
        if pattern_editor_group_box:
             pattern_editor_group_box.setObjectName("patternEditorGroupBoxFilenameManager") # Name the groupbox inside
             pattern_editor_group_box.setTitle("Pattern String & Placeholders") # Change title

        # Remove the outer layout margin from the widget if it has one
        pattern_editor_layout = self.pattern_editor_widget.layout()
        if pattern_editor_layout:
            pattern_editor_layout.setObjectName("patternEditorLayoutFilenameManager")
            pattern_editor_layout.setContentsMargins(0,0,0,0)

        right_layout.addWidget(self.pattern_editor_widget, 1) # Add stretch

        self.save_button = QPushButton("Save Changes")
        self.save_button.setObjectName("saveButtonFilenameManager")
        right_layout.addWidget(self.save_button, 0, Qt.AlignmentFlag.AlignRight)
        splitter.addWidget(right_pane)

        splitter.setSizes([250, 450]) # Initial size ratio
        main_layout.addWidget(splitter)

        # --- Dialog Buttons ---
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.button_box.setObjectName("dialogButtonBoxFilenameManager")
        main_layout.addWidget(self.button_box)

        # Initial state
        self._set_details_enabled(False)
        self.delete_button.setEnabled(False)





    def _set_details_enabled(self, enabled: bool):
        """Enable/disable the right pane controls."""
        self.name_edit.setEnabled(enabled)
        self.pattern_editor_widget.setEnabled(enabled) # Enable the whole widget
        self.save_button.setEnabled(enabled)
        # Prevent editing/deleting the default pattern
        is_default = (self._selected_pattern_name == constants.DEFAULT_FILENAME_PATTERN_NAME)
        if is_default:
            self.name_edit.setReadOnly(True)
            # Keep pattern editable, but maybe warn user? Or make it read-only too?
            # self.pattern_editor_widget.pattern_edit.setReadOnly(True)
            self.delete_button.setEnabled(False) # Cannot delete default
        else:
             self.name_edit.setReadOnly(False)
             # self.pattern_editor_widget.pattern_edit.setReadOnly(False)
             self.delete_button.setEnabled(enabled) # Enable delete only if enabled AND not default

    def _connect_signals(self):
        self.pattern_list_widget.currentItemChanged.connect(self._on_pattern_selection_changed)
        self.add_button.clicked.connect(self._add_pattern)
        self.delete_button.clicked.connect(self._delete_pattern)
        self.save_button.clicked.connect(self._save_pattern_changes)
        self.button_box.rejected.connect(self.reject) # Close button maps to reject

    def _load_patterns(self):
        """Loads pattern names into the list."""
        self.pattern_list_widget.blockSignals(True)
        self.pattern_list_widget.clear()
        self._selected_pattern_name = None
        self._set_details_enabled(False)
        self.delete_button.setEnabled(False)
        self.name_edit.clear()
        self.pattern_editor_widget.set_pattern("") # Clear pattern editor

        saved_patterns = self.settings_service.get_saved_filename_patterns()

        # Ensure Default is always first? Or sort alphabetically? Let's sort.
        sorted_names = sorted(saved_patterns.keys())

        if saved_patterns:
            for name in sorted_names:
                item = QListWidgetItem(name)
                item.setData(Qt.ItemDataRole.UserRole, name) # Store name
                if name == constants.DEFAULT_FILENAME_PATTERN_NAME:
                     # Optional: Make default bold or visually distinct
                     font = item.font()
                     font.setBold(True)
                     item.setFont(font)
                self.pattern_list_widget.addItem(item)
            self.pattern_list_widget.setEnabled(True)
        else:
            # Should not happen if load_settings ensures default exists
            placeholder_item = QListWidgetItem("No patterns found!")
            placeholder_item.setFlags(placeholder_item.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled)
            self.pattern_list_widget.addItem(placeholder_item)
            self.pattern_list_widget.setEnabled(False)

        self.pattern_list_widget.blockSignals(False)

    def _on_pattern_selection_changed(self, current_item: QListWidgetItem, previous_item: QListWidgetItem):
        """Loads selected pattern details into the right pane."""
        # Add logic for checking unsaved changes later if needed
        if current_item:
            self._selected_pattern_name = current_item.data(Qt.ItemDataRole.UserRole)
            if self._selected_pattern_name:
                saved_patterns = self.settings_service.get_saved_filename_patterns()
                pattern_string = saved_patterns.get(self._selected_pattern_name, "")
                self.name_edit.setText(self._selected_pattern_name)
                self.pattern_editor_widget.set_pattern(pattern_string)
                self._set_details_enabled(True)
            else:
                self._clear_details()
                self._set_details_enabled(False)
        else:
            self._clear_details()
            self._set_details_enabled(False)

    def _clear_details(self):
        self._selected_pattern_name = None
        self.name_edit.clear()
        self.pattern_editor_widget.set_pattern("")
        self._set_details_enabled(False)

    def _add_pattern(self):
        """Adds a new, empty pattern entry and selects it."""
        new_name_base = "New Pattern"
        new_name = new_name_base
        count = 1
        saved_patterns = self.settings_service.get_saved_filename_patterns()
        while new_name in saved_patterns:
            count += 1
            new_name = f"{new_name_base} {count}"

        new_pattern_string = constants.DEFAULT_FILENAME_PATTERN # Start with default

        if self.settings_service.add_or_update_saved_filename_pattern(new_name, new_pattern_string):
            log_debug(f"Pattern '{new_name}' added to service. Refreshing list.")
            self._load_patterns() # Reload the list

            # Find and select the newly added item
            for i in range(self.pattern_list_widget.count()):
                item = self.pattern_list_widget.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == new_name:
                    self.pattern_list_widget.setCurrentItem(item)
                    # Manually trigger selection handler if needed
                    self._on_pattern_selection_changed(item, None)
                    break
            self.name_edit.setFocus()
            self.name_edit.selectAll()
            self.patterns_updated.emit() # Notify that list changed
        else:
            show_error_message(self, "Add Error", "Failed to add the new pattern.")

    def _delete_pattern(self):
        """Deletes the selected pattern."""
        if not self._selected_pattern_name or self._selected_pattern_name == constants.DEFAULT_FILENAME_PATTERN_NAME:
            return # Should be disabled, but double-check

        reply = QMessageBox.question(self, "Confirm Delete",
                                     f"Are you sure you want to delete the pattern '{self._selected_pattern_name}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            if self.settings_service.remove_saved_filename_pattern(self._selected_pattern_name):
                log_info(f"Pattern '{self._selected_pattern_name}' deleted.")
                self._load_patterns() # Refresh list and clear details
                self.patterns_updated.emit() # Notify that list changed
            else:
                show_error_message(self, "Delete Error", f"Failed to delete the pattern '{self._selected_pattern_name}'. It might be the active pattern or the default.")

    def _save_pattern_changes(self):
        """Saves changes made to the name/pattern string."""
        if not self._selected_pattern_name: return

        original_name = self._selected_pattern_name
        new_name = self.name_edit.text().strip()
        new_pattern = self.pattern_editor_widget.get_pattern()

        if not new_name or not new_pattern:
            show_error_message(self, "Input Error", "Pattern name and string cannot be empty.")
            return

        # Prevent renaming default pattern
        if original_name == constants.DEFAULT_FILENAME_PATTERN_NAME and new_name != original_name:
             show_error_message(self, "Edit Error", f"Cannot rename the '{constants.DEFAULT_FILENAME_PATTERN_NAME}' pattern.")
             self.name_edit.setText(original_name) # Revert name edit
             return

        # Check if renaming to an existing name (and it's not the original name)
        saved_patterns = self.settings_service.get_saved_filename_patterns()
        if new_name != original_name and new_name in saved_patterns:
            show_error_message(self, "Name Exists", f"A pattern named '{new_name}' already exists.")
            return

        # If name changed, remove old entry first (unless it's the default)
        if new_name != original_name and original_name != constants.DEFAULT_FILENAME_PATTERN_NAME:
             # We need to save under new name, then delete old. Or just update if name is same.
             # Let SettingsService handle add_or_update logic
             if not self.settings_service.remove_saved_filename_pattern(original_name):
                  log_error(f"Failed to remove old pattern '{original_name}' during rename.")
                  show_error_message(self, "Save Error", "Failed to remove the old pattern during rename.")
                  return # Abort save

        # Add/Update the pattern
        if self.settings_service.add_or_update_saved_filename_pattern(new_name, new_pattern):
            log_info(f"Pattern '{new_name}' saved.")
            self._load_patterns() # Reload list to reflect potential name change/sort order

            # Reselect the (potentially renamed) item
            new_item_to_select = None
            for i in range(self.pattern_list_widget.count()):
                item = self.pattern_list_widget.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == new_name:
                     new_item_to_select = item
                     break
            if new_item_to_select:
                 # Use QTimer to ensure selection happens after list update is fully processed
                 QTimer.singleShot(0, lambda: self.pattern_list_widget.setCurrentItem(new_item_to_select))

            self.patterns_updated.emit() # Notify that list changed
            QMessageBox.information(self, "Save Successful", "Pattern changes saved.")
        else:
            show_error_message(self, "Save Error", "Failed to save changes to the pattern.")
            # Attempt to revert rename if delete succeeded but add failed
            if new_name != original_name and original_name not in self.settings_service.get_saved_filename_patterns():
                 log_warning("Attempting to revert rename after save failure.")
                 # Try adding the original back (pattern might be the old or new one here, tricky)
                 # self.settings_service.add_or_update_saved_filename_pattern(original_name, ???)
                 self._load_patterns() # Reload to show potentially inconsistent state


    def reject(self):
        """Closes the dialog."""
        log_debug("Filename Pattern Manager closed.")
        super().reject()

# --- END OF FILE ui/filename_pattern_manager_dialog.py ---