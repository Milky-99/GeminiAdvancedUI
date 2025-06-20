# -*- coding: utf-8 -*-
import json
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLineEdit, QLabel, QDialogButtonBox, QMessageBox, QInputDialog, QTextEdit, QFileDialog, QRadioButton, QButtonGroup, QGroupBox
)
from PyQt6.QtCore import Qt
from typing import Optional, List, Tuple
from pathlib import Path
# --- Project Imports ---
from core.api_key_service import ApiKeyService
from core.settings_service import SettingsService # Needed to update last used key if deleted
from utils.logger import log_debug, log_warning, log_error, log_info
from utils.helpers import show_error_message, show_info_message, show_warning_message
from utils import constants

class ApiKeyManagerDialog(QDialog):
    """Dialog for managing API keys."""

    def __init__(self, api_key_service: ApiKeyService, settings_service: SettingsService, parent=None):
        super().__init__(parent)
        self.api_key_service = api_key_service
        self.settings_service = settings_service # To update last used key

        self.setWindowTitle("API Key Manager")
        self.setMinimumWidth(500)

        self._setup_ui()
        self._connect_signals()
        self._load_keys()


    def _setup_ui(self):
        self.setObjectName("apiKeyManagerDialog") # Name the dialog itself
        layout = QVBoxLayout(self)
        layout.setObjectName("mainLayoutApiKeyManager")

        # config_stored_keys_label = QLabel("Stored API Keys:")
        layout.addWidget(QLabel("Stored API Keys:"))
        self.key_list_widget = QListWidget()
        self.key_list_widget.setObjectName("apiKeyListWidget")
        self.key_list_widget.setToolTip("Select a key to view/edit/remove.")
        layout.addWidget(self.key_list_widget)

        # --- Action Buttons ---
        button_layout = QHBoxLayout()
        button_layout.setObjectName("actionButtonLayoutApiKeyManager")
        self.add_button = QPushButton("Add New Key...")
        self.add_button.setObjectName("addKeyButtonApiKeyManager")
        self.import_button = QPushButton("Import Keys...") 
        self.import_button.setObjectName("importKeysButtonApiKeyManager")
        self.import_button.setToolTip("Import multiple keys from text or JSON file.")
        self.rename_button = QPushButton("Rename Selected...")
        self.rename_button.setObjectName("renameKeyButtonApiKeyManager")
        self.delete_button = QPushButton("Delete Selected")
        self.delete_button.setObjectName("deleteKeyButtonApiKeyManager")
        self.view_button = QPushButton("View Selected Key...")
        self.view_button.setObjectName("viewKeyButtonApiKeyManager")

        button_layout.addWidget(self.add_button)
        button_layout.addWidget(self.import_button)
        button_layout.addWidget(self.rename_button)
        button_layout.addWidget(self.view_button)
        button_layout.addWidget(self.delete_button)
        layout.addLayout(button_layout)

        # --- Dialog Buttons ---
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.button_box.setObjectName("dialogButtonBoxApiKeyManager")
        layout.addWidget(self.button_box)

        # Initial state
        self.rename_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.view_button.setEnabled(False)



    def _connect_signals(self):
        self.key_list_widget.currentItemChanged.connect(self._on_key_selection_changed)
        self.add_button.clicked.connect(self._add_key)
        self.import_button.clicked.connect(self._open_batch_import_dialog) # +++ ADD THIS LINE +++
        self.rename_button.clicked.connect(self._rename_key)
        self.delete_button.clicked.connect(self._delete_key)
        self.view_button.clicked.connect(self._view_key)
        self.button_box.rejected.connect(self.reject) # Close button maps to reject

    def _load_keys(self):
        """Loads key names into the list widget."""
        self.key_list_widget.clear()
        key_names = self.api_key_service.get_key_names()
        if key_names:
            self.key_list_widget.addItems(key_names)
        else:
            self.key_list_widget.addItem("No API keys stored.")
            self.key_list_widget.setEnabled(False) # Disable list if empty

        # Reset button states
        self._on_key_selection_changed(None)


    def _open_batch_import_dialog(self):
        """Opens the dialog for batch importing keys."""
        dialog = BatchImportDialog(self)
        if dialog.exec():
            imported_data = dialog.get_imported_data()
            if imported_data:
                self._process_batch_import(imported_data)
        else:
            log_debug("Batch import dialog cancelled.")

    def _find_next_numeric_name(self, existing_names: set) -> str:
        """Finds the lowest unused positive integer as a string for a key name."""
        i = 1
        while True:
            name = str(i)
            if name not in existing_names:
                return name
            i += 1

    def _process_batch_import(self, imported_keys: List[Tuple[Optional[str], str]]):
        """Processes a list of keys from the batch import dialog."""
        added_count = 0
        skipped_count = 0
        failed_count = 0
        current_key_names = set(self.api_key_service.get_key_names())
        names_added_in_batch = set() # Track names used within this batch

        log_info(f"Processing batch import of {len(imported_keys)} potential keys...")

        for name, value in imported_keys:
            final_name = name
            is_pasted = (name is None) # Check if it came from the paste option

            # Validate key value
            if not value or value == constants.DEFAULT_API_KEY_PLACEHOLDER:
                log_warning(f"Skipping import: Invalid API key value provided ('{value}').")
                failed_count += 1
                continue

            # Determine name if pasted or empty JSON name
            if is_pasted or not final_name:
                # Combine existing keys and keys just added in this batch for uniqueness check
                names_to_check = current_key_names.union(names_added_in_batch)
                final_name = self._find_next_numeric_name(names_to_check)
                log_debug(f"Generated numeric name '{final_name}' for pasted/unnamed key.")

            # Check for duplicate name (against service keys AND this batch)
            if final_name in current_key_names or final_name in names_added_in_batch:
                log_warning(f"Skipping import: API key name '{final_name}' already exists.")
                skipped_count += 1
                continue

            # Validate generated/provided name
            if not final_name or final_name == constants.DEFAULT_API_KEY_PLACEHOLDER:
                 log_warning(f"Skipping import: Invalid API key name generated or provided ('{final_name}').")
                 failed_count += 1
                 continue

            # Attempt to add the key
            if self.api_key_service.add_or_update_key(final_name, value):
                added_count += 1
                names_added_in_batch.add(final_name) # Track successful addition
                log_debug(f"Successfully imported key '{final_name}'.")
            else:
                log_error(f"Failed to save imported API key '{final_name}'.")
                failed_count += 1

        # Refresh the list widget
        self._load_keys()

        # Show summary message
        summary_message = f"Batch Import Complete:\n\n- Added: {added_count}\n- Skipped (Duplicate Name): {skipped_count}\n- Failed (Invalid/Save Error): {failed_count}"
        log_info(summary_message.replace("\n", " "))
        show_info_message(self, "Batch Import Summary", summary_message)


    def _on_key_selection_changed(self, current_item: QListWidgetItem):
        """Updates button states based on selection."""
        enabled = current_item is not None and current_item.text() != "No API keys stored."
        self.rename_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)
        self.view_button.setEnabled(enabled)

    def _add_key(self):
        """Handles adding a new API key via input dialogs."""
        name, ok1 = QInputDialog.getText(self, "Add API Key", "Enter a unique name for this key:")
        if ok1 and name:
            if name == constants.DEFAULT_API_KEY_PLACEHOLDER:
                 show_error_message(self, "Invalid Name", f"Cannot use the placeholder name '{name}'.")
                 return
            if name in self.api_key_service.get_key_names():
                show_error_message(self, "Name Exists", f"An API key with the name '{name}' already exists.")
                return

            value, ok2 = QInputDialog.getText(self, "Add API Key", f"Enter the API key value for '{name}':", QLineEdit.EchoMode.Password)
            if ok2 and value:
                if self.api_key_service.add_or_update_key(name, value):
                    self._load_keys() # Refresh list
                else:
                    show_error_message(self, "Save Error", "Failed to save the new API key.")
        elif ok1 and not name:
             show_error_message(self, "Input Error", "API key name cannot be empty.")


    def _rename_key(self):
        """Handles renaming the selected API key."""
        current_item = self.key_list_widget.currentItem()
        if not current_item: return
        old_name = current_item.text()

        new_name, ok = QInputDialog.getText(self, "Rename API Key", f"Enter the new name for '{old_name}':", QLineEdit.EchoMode.Normal, old_name)

        if ok and new_name and new_name != old_name:
            if new_name == constants.DEFAULT_API_KEY_PLACEHOLDER:
                 show_error_message(self, "Invalid Name", f"Cannot use the placeholder name '{new_name}'.")
                 return
            if new_name in self.api_key_service.get_key_names():
                show_error_message(self, "Name Exists", f"An API key with the name '{new_name}' already exists.")
                return

            key_value = self.api_key_service.get_key_value(old_name)
            if key_value is None:
                 show_error_message(self, "Rename Error", f"Could not retrieve value for key '{old_name}' to rename.")
                 return

            # Add under new name, then remove old name
            if self.api_key_service.add_or_update_key(new_name, key_value):
                 if self.api_key_service.remove_key(old_name):
                      # Update last used key if it was the renamed one
                      if self.settings_service.get_setting("last_used_api_key_name") == old_name:
                          self.settings_service.set_setting("last_used_api_key_name", new_name)
                      self._load_keys() # Refresh list
                 else:
                      # Failed to remove old key, attempt to revert by removing new key
                      log_error(f"Failed to remove old key '{old_name}' after rename. Attempting rollback.")
                      self.api_key_service.remove_key(new_name)
                      show_error_message(self, "Rename Error", f"Failed to remove the old key '{old_name}' during rename.")
                      self._load_keys()
            else:
                show_error_message(self, "Rename Error", f"Failed to save the key under the new name '{new_name}'.")
        elif ok and not new_name:
             show_error_message(self, "Input Error", "New API key name cannot be empty.")

    def _delete_key(self):
        """Handles deleting the selected API key."""
        current_item = self.key_list_widget.currentItem()
        if not current_item: return
        name_to_delete = current_item.text()

        reply = QMessageBox.question(self, "Confirm Delete",
                                     f"Are you sure you want to delete the API key named '{name_to_delete}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            if self.api_key_service.remove_key(name_to_delete):
                # Clear last used key if it was the deleted one
                if self.settings_service.get_setting("last_used_api_key_name") == name_to_delete:
                    self.settings_service.set_setting("last_used_api_key_name", None)
                self._load_keys() # Refresh list
            else:
                show_error_message(self, "Delete Error", f"Failed to delete the API key '{name_to_delete}'.")

    def _view_key(self):
        """Temporarily displays the selected API key value."""
        current_item = self.key_list_widget.currentItem()
        if not current_item: return
        name = current_item.text()
        value = self.api_key_service.get_key_value(name)

        if value is not None:
            # Use a QMessageBox for temporary display
            QMessageBox.information(self, f"API Key Value - {name}",
                                    f"The value for key '{name}' is:\n\n{value}\n\n(Close this window to hide)",
                                    QMessageBox.StandardButton.Ok)
        else:
            show_error_message(self, "View Error", f"Could not retrieve or decrypt the value for API key '{name}'.")


    def reject(self):
         """Ensures dialog closes properly."""
         log_debug("API Key Manager closed.")
         super().reject()
         
         
         
# --- Batch Import Dialog ---

# --- Batch Import Dialog ---
class BatchImportDialog(QDialog):
    """Dialog for importing API keys in batch."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import API Keys")
        self.setMinimumWidth(450)
        self._selected_file_path: Optional[Path] = None # Changed from _selected_json_path
        self._imported_data: List[Tuple[Optional[str], str]] = [] # Stores (name, value) or (None, value)

        layout = QVBoxLayout(self)

        # --- Import Method Selection ---
        method_group = QGroupBox("Import Method")
        method_layout = QVBoxLayout(method_group)
        self.paste_radio = QRadioButton("Paste Keys (one per line, names generated)")
        # --- CHANGE: Updated Radio Button Label ---
        self.file_radio = QRadioButton("Import from File (.json or .txt)")
        # --- END CHANGE ---
        self.method_button_group = QButtonGroup(self)
        self.method_button_group.addButton(self.paste_radio, 1)
        self.method_button_group.addButton(self.file_radio, 2) # Changed from json_radio
        self.paste_radio.setChecked(True) # Default to paste
        method_layout.addWidget(self.paste_radio)
        method_layout.addWidget(self.file_radio) # Changed from json_radio
        layout.addWidget(method_group)

        # --- Paste Area ---
        self.paste_group = QGroupBox("Paste API Keys")
        paste_layout = QVBoxLayout(self.paste_group)
        self.paste_label = QLabel("Paste one API key per line below:")
        self.paste_edit = QTextEdit()
        self.paste_edit.setPlaceholderText("API_KEY_1\nAPI_KEY_2\n...")
        self.paste_edit.setMinimumHeight(150)
        paste_layout.addWidget(self.paste_label)
        paste_layout.addWidget(self.paste_edit)
        layout.addWidget(self.paste_group)

        # --- File Import Area (Renamed) ---
        self.file_group = QGroupBox("Import File") # Renamed from json_group
        file_layout = QHBoxLayout(self.file_group) # Renamed from json_layout
        self.file_path_label = QLabel("No file selected.") # Renamed from json_path_label
        self.file_browse_button = QPushButton("Browse...") # Renamed from json_browse_button
        file_layout.addWidget(QLabel("File:"))
        file_layout.addWidget(self.file_path_label, 1) # Renamed
        file_layout.addWidget(self.file_browse_button) # Renamed
        self.file_group.setVisible(False) # Hidden initially (Renamed)
        layout.addWidget(self.file_group) # Renamed

        # --- Format Info ---
        format_label = QLabel(
            "<b>JSON Format:</b> {\"key_name_1\": \"api_key_value_1\", ...}\n"
            "<b>TXT Format:</b> One API key value per line.\n" # Added TXT format info
            "Keys with names that already exist will be skipped (JSON only)."
        )
        format_label.setWordWrap(True)
        layout.addWidget(format_label)

        # --- Dialog Buttons ---
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(self.button_box)

        # --- Connections ---
        self.method_button_group.buttonClicked.connect(self._toggle_views)
        self.file_browse_button.clicked.connect(self._browse_file) # Changed from _browse_json
        self.button_box.accepted.connect(self.process_and_accept)
        self.button_box.rejected.connect(self.reject)

        self._toggle_views() # Initial UI state


    def _toggle_views(self):
        """Show/hide input areas based on radio selection."""
        is_paste = self.paste_radio.isChecked()
        self.paste_group.setVisible(is_paste)
        self.file_group.setVisible(not is_paste) # Changed from json_group

    def _browse_file(self): # Renamed from _browse_json
        """Open file dialog to select JSON or TXT file."""
        # --- CHANGE: Updated file filter ---
        file_filter = "Importable Files (*.json *.txt);;JSON Files (*.json);;Text Files (*.txt)"
        # --- END CHANGE ---
        filepath_tuple = QFileDialog.getOpenFileName(self, "Select API Key File", "", file_filter)
        filepath_str = filepath_tuple[0]
        if filepath_str:
            self._selected_file_path = Path(filepath_str) # Changed from _selected_json_path
            self.file_path_label.setText(self._selected_file_path.name) # Changed from json_path_label
            self.file_path_label.setToolTip(str(self._selected_file_path)) # Changed from json_path_label
        else:
            self._selected_file_path = None # Changed from _selected_json_path
            self.file_path_label.setText("No file selected.") # Changed from json_path_label
            self.file_path_label.setToolTip("") # Changed from json_path_label
            
            
            
   
    def process_and_accept(self):
        """Parse input based on method and file type, then accept the dialog if successful."""
        self._imported_data = [] # Clear previous data
        try:
            if self.paste_radio.isChecked():
                # --- Paste Logic (Unchanged) ---
                pasted_text = self.paste_edit.toPlainText().strip()
                if not pasted_text:
                    show_warning_message(self, "Input Empty", "Please paste at least one API key.")
                    return
                lines = [line.strip() for line in pasted_text.splitlines() if line.strip()]
                if not lines:
                     show_warning_message(self, "Input Empty", "No valid API keys found in the pasted text.")
                     return
                self._imported_data = [(None, key_value) for key_value in lines]
                log_info(f"Prepared {len(self._imported_data)} keys from pasted text for import.")
                # --- End Paste Logic ---

            else: # File Import Logic
                if not self._selected_file_path or not self._selected_file_path.is_file():
                    show_warning_message(self, "No File", "Please select a valid file.")
                    return

                file_extension = self._selected_file_path.suffix.lower()

                # --- Process based on file extension ---
                if file_extension == ".json":
                    try:
                        with self._selected_file_path.open('r', encoding='utf-8') as f:
                            data = json.load(f)
                        if not isinstance(data, dict):
                            raise ValueError("JSON root must be an object (dictionary).")

                        for name, value in data.items():
                            if isinstance(name, str) and isinstance(value, str) and name.strip() and value.strip():
                                self._imported_data.append((name.strip(), value.strip()))
                            else:
                                log_warning(f"Skipping invalid entry in JSON: Name='{name}' (Type: {type(name)}), Value Type: {type(value)}")
                        if not self._imported_data:
                            show_warning_message(self, "Import Failed", "JSON file contained no valid name/key pairs.")
                            return
                        log_info(f"Prepared {len(self._imported_data)} keys from JSON file '{self._selected_file_path.name}' for import.")

                    except json.JSONDecodeError as e:
                        show_error_message(self, "JSON Error", f"Error decoding JSON file:\n{e}")
                        return
                    except OSError as e:
                        show_error_message(self, "File Error", f"Error reading JSON file:\n{e}")
                        return
                    except ValueError as e:
                        show_error_message(self, "JSON Format Error", str(e))
                        return

                elif file_extension == ".txt":
                    try:
                        with self._selected_file_path.open('r', encoding='utf-8') as f:
                            lines = [line.strip() for line in f if line.strip()]
                        if not lines:
                            show_warning_message(self, "Import Failed", "TXT file was empty or contained no valid keys.")
                            return
                        # Store as (None, value) tuples for TXT mode (like paste)
                        self._imported_data = [(None, key_value) for key_value in lines]
                        log_info(f"Prepared {len(self._imported_data)} keys from TXT file '{self._selected_file_path.name}' for import.")
                    except OSError as e:
                         show_error_message(self, "File Error", f"Error reading TXT file:\n{e}")
                         return

                else:
                    # Should not happen due to file filter, but handle anyway
                    show_error_message(self, "Unsupported File", f"Unsupported file type: {file_extension}. Please select a .json or .txt file.")
                    return
                # --- End Process based on file extension ---

        except Exception as e:
            log_error(f"Unexpected error during batch import preparation: {e}", exc_info=True)
            show_error_message(self, "Import Error", f"An unexpected error occurred:\n{e}")
            return

        # If we reached here, parsing was successful
        super().accept() # Accept the dialog   
   
   
   
   
   
   
    def get_imported_data(self) -> List[Tuple[Optional[str], str]]:
        """Returns the list of (name, value) or (None, value) tuples."""
        return self._imported_data

# --- End Batch Import Dialog ---         