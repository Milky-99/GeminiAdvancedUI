from pathlib import Path
from typing import Optional, List, Tuple

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QPushButton,
    QDialogButtonBox, QMessageBox, QGroupBox, QFileDialog, QWidget, QSizePolicy, QListWidget, QListWidgetItem 
)
from PyQt6.QtGui import QPixmap, QDragEnterEvent, QDropEvent, QImageReader
from PyQt6.QtCore import Qt, QSize, pyqtSlot, QUrl, pyqtSignal 

# --- Project Imports ---
from core.image_processor import ImageProcessor
from utils.logger import log_debug, log_info, log_error, log_warning
from utils.helpers import show_info_message, show_error_message



class ImageDropLabel(QLabel):
    """QLabel subclass primarily for displaying the image preview."""
    # Removed drag/drop event handling and image_dropped signal

    def __init__(self,
                 settings_service, # Keep args for potential future use if needed
                 current_mode: str,
                 multi_mode_instance_data: Optional[List[Tuple[int, str, str, str]]] = None,
                 parent=None):
        super().__init__(parent)
        # Store context if needed by the label itself (currently not used here)
        self.settings_service = settings_service
        self.current_mode = current_mode
        self.multi_mode_instance_data = multi_mode_instance_data

        # --- Basic Setup ---
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("QLabel { background-color: transparent; border: none; color: grey; }") # Default look
        self.setText("Drag image file here or Browse") # Initial text
        # Ensure it can grow
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(QSize(200, 150)) # Maintain a minimum size


class ImageSelectorMetaViewerDialog(QDialog):
    """Dialog to select an image (drag/drop/browse) and view/load its metadata."""

    Accepted = QDialog.DialogCode.Accepted # Re-use standard codes

    def __init__(self,
                 settings_service,
                 current_mode: str, # Added
                 multi_mode_instance_data: Optional[List[Tuple[int, str, str, str]]], # Added
                 parent=None):
        super().__init__(parent)
        self.settings_service = settings_service
        self.current_mode = current_mode # Store current mode
        self.multi_mode_instance_data = multi_mode_instance_data or [] # Store instance data

        self._current_image_path: Optional[Path] = None
        self._unresolved_prompt: Optional[str] = None
        self._resolved_prompt: Optional[str] = None
        self._selected_prompt_to_load: Optional[str] = None
        self._selected_target: Optional[str] = None # Stores "single" or "multi_{id}"
        self._full_result_pixmap: Optional[QPixmap] = None

        self.setWindowTitle("Select Image & View Metadata")
        self.setMinimumWidth(650) # Adjusted minimum width
        self.setMinimumHeight(700) # Adjusted minimum height

        # --- Enable Drops for the Dialog ---
        self.setAcceptDrops(True)
        self.setProperty("dragActive", "inactive") # inactive, valid, invalid

        self._setup_ui() # UI setup remains the same call
        self._connect_signals() # Signal connections remain the same call
        self._set_details_enabled(False) # Initial state

        # Configure UI based on mode (now uses stored attributes)
        if self.current_mode == "Multi":
            self._setup_multi_mode_target_selector()
        else:
            # Ensure target group is hidden if created but not Multi-Mode
            if hasattr(self, 'target_group'):
                self.target_group.setVisible(False)
                
 
    def _setup_ui(self):
        self.setObjectName("imageSelectorMetaViewerDialog")
        main_layout = QVBoxLayout(self)
        main_layout.setObjectName("mainLayoutSelectorMetaViewer")

        # --- Image Selection Area ---
        selection_group = QGroupBox("Select Image")
        selection_group.setObjectName("selectionGroupSelectorMetaViewer")
        selection_layout = QVBoxLayout(selection_group)
        selection_layout.setObjectName("selectionLayoutSelectorMetaViewer")

        self.image_drop_label = ImageDropLabel(self.settings_service, self.current_mode, self.multi_mode_instance_data, self)
        self.image_drop_label.setObjectName("imageDropLabelSelectorMetaViewer")
        # Let the label expand vertically
        self.image_drop_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        selection_layout.addWidget(self.image_drop_label, 1) # Give label vertical stretch within its group

        browse_button_layout = QHBoxLayout()
        browse_button_layout.setObjectName("browseButtonLayoutSelectorMetaViewer")
        browse_button_layout.addStretch(1)
        self.browse_button = QPushButton("Browse for Image...")
        self.browse_button.setObjectName("browseButtonSelectorMetaViewer")
        browse_button_layout.addWidget(self.browse_button)
        browse_button_layout.addStretch(1)
        selection_layout.addLayout(browse_button_layout)

        # *** Add stretch factor here to make selection group larger ***
        main_layout.addWidget(selection_group, 1) # Give selection group vertical stretch

        # --- Metadata Display ---
        metadata_group = QGroupBox("Embedded Prompts")
        metadata_group.setObjectName("metadataGroupSelectorMetaViewer")
        metadata_layout = QVBoxLayout(metadata_group)
        metadata_layout.setObjectName("metadataLayoutSelectorMetaViewer")

        # Unresolved Prompt
        unresolved_group = QGroupBox("Unresolved Prompt (with wildcards)")
        unresolved_group.setObjectName("unresolvedGroupSelectorMetaViewer")
        unresolved_layout = QVBoxLayout(unresolved_group)
        unresolved_layout.setObjectName("unresolvedLayoutSelectorMetaViewer")
        self.unresolved_edit = QTextEdit()
        self.unresolved_edit.setObjectName("unresolvedTextEditSelectorMetaViewer")
        self.unresolved_edit.setReadOnly(True)
        self.unresolved_edit.setMinimumHeight(60)
        self.load_unresolved_button = QPushButton("Load Unresolved Prompt")
        self.load_unresolved_button.setObjectName("loadUnresolvedButtonSelectorMetaViewer")
        unresolved_layout.addWidget(self.unresolved_edit)
        unresolved_layout.addWidget(self.load_unresolved_button, 0, Qt.AlignmentFlag.AlignRight)
        metadata_layout.addWidget(unresolved_group)

        # Resolved Prompt
        resolved_group = QGroupBox("Resolved Prompt (used for generation)")
        resolved_group.setObjectName("resolvedGroupSelectorMetaViewer")
        resolved_layout = QVBoxLayout(resolved_group)
        resolved_layout.setObjectName("resolvedLayoutSelectorMetaViewer")
        self.resolved_edit = QTextEdit()
        self.resolved_edit.setObjectName("resolvedTextEditSelectorMetaViewer")
        self.resolved_edit.setReadOnly(True)
        self.resolved_edit.setMinimumHeight(60)
        self.load_resolved_button = QPushButton("Load Resolved Prompt")
        self.load_resolved_button.setObjectName("loadResolvedButtonSelectorMetaViewer")
        resolved_layout.addWidget(self.resolved_edit)
        resolved_layout.addWidget(self.load_resolved_button, 0, Qt.AlignmentFlag.AlignRight)
        metadata_layout.addWidget(resolved_group)

        main_layout.addWidget(metadata_group) # Reduced stretch factor for metadata

        # --- Target Selection (for Multi-Mode) ---
        self.target_group = QGroupBox("Load Target (Multi-Mode)")
        self.target_group.setObjectName("targetGroupSelectorMetaViewer")
        self.target_group.setVisible(False) # Hidden by default
        target_layout = QVBoxLayout(self.target_group)
        target_layout.setObjectName("targetLayoutSelectorMetaViewer")

        target_info_label = QLabel("Select an IDLE instance to load the prompt into:")
        target_info_label.setObjectName("targetInfoLabelSelectorMetaViewer")
        target_layout.addWidget(target_info_label)

        self.target_list_widget = QListWidget()
        self.target_list_widget.setObjectName("targetListWidgetSelectorMetaViewer")
        self.target_list_widget.setToolTip("Double-click or select and press 'Load...'")
        self.target_list_widget.setMaximumHeight(150)
        target_layout.addWidget(self.target_list_widget)

        main_layout.addWidget(self.target_group) # Add below metadata group


        # --- Dialog Buttons ---
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.button_box.setObjectName("dialogButtonBoxSelectorMetaViewer")
        main_layout.addWidget(self.button_box)

 
 

    def _setup_multi_mode_target_selector(self):
        """Populates the target list widget for Multi-Mode."""
        self.target_list_widget.clear()
        has_loadable_target = False

        if not self.multi_mode_instance_data:
            item = QListWidgetItem("No instances available in Multi-Mode.")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled) # Disable selection
            self.target_list_widget.addItem(item)
        else:
            for instance_id, key_name, prompt_start, status in self.multi_mode_instance_data:
                # Truncate prompt for display
                prompt_display = prompt_start[:40] + "..." if len(prompt_start) > 40 else prompt_start
                # Format display string
                display_text = f"#{instance_id} [{key_name}] ({status}) - '{prompt_display}'"
                item = QListWidgetItem(display_text)
                # Store target identifier "multi_{id}" in UserRole data
                item.setData(Qt.ItemDataRole.UserRole, f"multi_{instance_id}")

                # Disable loading into running/looping instances
                if status != "Idle":
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled) # Make non-selectable
                    item.setForeground(Qt.GlobalColor.gray) # Visually indicate disabled
                    item.setToolTip(f"Instance #{instance_id} is currently {status.lower()} and cannot be loaded into.")
                else:
                    item.setToolTip(f"Load prompt into Instance #{instance_id} ({key_name})")
                    has_loadable_target = True # Found at least one valid target

                self.target_list_widget.addItem(item)

        # Show the target selection group box
        self.target_group.setVisible(True)
        # Enable/Disable Load buttons based on whether any target is loadable
        # Note: _set_details_enabled will also check if prompts exist
        # We only need to ensure that if multi-mode has targets, the buttons *can* be enabled
        if not has_loadable_target and self.multi_mode_instance_data:
            # If instances exist but none are Idle, ensure buttons stay disabled
            self.load_unresolved_button.setEnabled(False)
            self.load_resolved_button.setEnabled(False)
            log_warning("Multi-Mode target selection: No idle instances available to load into.")


    def _connect_signals(self):
        # self.image_drop_label.image_dropped.connect(self._process_image_path) # REMOVE THIS LINE
        self.browse_button.clicked.connect(self._browse_for_image)
        self.load_unresolved_button.clicked.connect(self._load_unresolved)
        self.load_resolved_button.clicked.connect(self._load_resolved)
        self.button_box.rejected.connect(self.reject)
        # Add connection for target list double-click if desired
        if hasattr(self, 'target_list_widget'):
             self.target_list_widget.itemDoubleClicked.connect(self._handle_target_double_click)


    # --- Drag and Drop Event Handling for the Dialog ---

    def dragEnterEvent(self, event: QDragEnterEvent):
        mime_data = event.mimeData()
        if mime_data.hasUrls():
            urls: List[QUrl] = mime_data.urls()
            if urls and urls[0].isLocalFile():
                file_path = Path(urls[0].toLocalFile())
                # Simple check for common image extensions
                try:
                    supported_formats = [fmt.data().decode().lower() for fmt in QImageReader.supportedImageFormats()]
                    if file_path.suffix.lower().strip('.') in supported_formats:
                        event.acceptProposedAction()
                        self.setProperty("dragActive", "valid") # Set property for QSS
                        log_debug("Drag Enter: Valid image file")
                    else:
                        event.ignore()
                        self.setProperty("dragActive", "invalid") # Set property for QSS
                        log_debug(f"Drag Enter: Invalid file type ({file_path.suffix})")
                except Exception as e:
                    log_error(f"Error checking image format support: {e}")
                    event.ignore()
                    self.setProperty("dragActive", "invalid")
            else: # Not a local file URL
                event.ignore()
                self.setProperty("dragActive", "invalid")
        else: # Doesn't contain URL
            event.ignore()
            self.setProperty("dragActive", "invalid")

        # Force style update based on property change
        self.style().polish(self)
        self.update() # Ensure repaint

    def dragLeaveEvent(self, event):
        log_debug("Drag Leave")
        self.setProperty("dragActive", "inactive") # Reset property
        self.style().polish(self)
        self.update()

    def dropEvent(self, event: QDropEvent):
        log_debug("Drop Event")
        self.setProperty("dragActive", "inactive") # Reset property on drop
        self.style().polish(self)
        self.update()

        mime_data = event.mimeData()
        if mime_data.hasUrls():
            urls: List[QUrl] = mime_data.urls()
            if urls and urls[0].isLocalFile():
                file_path = Path(urls[0].toLocalFile())
                try:
                     supported_formats = [fmt.data().decode().lower() for fmt in QImageReader.supportedImageFormats()]
                     if file_path.suffix.lower().strip('.') in supported_formats:
                         # Call processing function directly
                         self._process_image_path(file_path)
                         event.acceptProposedAction()
                         log_info(f"Image dropped and accepted: {file_path}")
                         return # Success
                     else:
                          log_warning(f"Dropped file is not a supported image type: {file_path.name}")
                          show_warning_message(self, "Invalid File", "The dropped file is not a supported image format.")
                          event.ignore()
                except Exception as e:
                     log_error(f"Error processing dropped image format: {e}")
                     show_error_message(self, "Drop Error", f"Could not process the dropped image:\n{e}")
                     event.ignore()

            else: # Not a local file URL
                event.ignore()
        else: # Doesn't contain URL
            event.ignore()

    @pyqtSlot(QListWidgetItem)
    def _handle_target_double_click(self, item: QListWidgetItem):
        """Handles double-clicking on a target instance."""
        if not item or not (item.flags() & Qt.ItemFlag.ItemIsEnabled):
            return # Ignore clicks on disabled items

        # Determine which prompt to load (e.g., prefer resolved if available)
        prompt_to_load = self._resolved_prompt or self._unresolved_prompt
        if not prompt_to_load:
            show_info_message(self, "No Prompt", "No prompt data available in the image to load.")
            return

        # Load the preferred prompt
        self._selected_target = item.data(Qt.ItemDataRole.UserRole)
        self._selected_prompt_to_load = prompt_to_load
        log_info(f"Double-click: Marking prompt for loading into target: {self._selected_target}")
        super().accept()



    def _set_details_enabled(self, enabled: bool):
        """Enable/disable the metadata sections and load buttons."""
        self.unresolved_edit.setEnabled(enabled)
        self.resolved_edit.setEnabled(enabled)

        # Determine if buttons should be potentially enabled
        can_enable_buttons = enabled and (bool(self._unresolved_prompt) or bool(self._resolved_prompt))
        has_loadable_target = True # Assume true for Single mode or if Multi has idle instances

        if self.current_mode == "Multi":
            # Check if any item in the target list is enabled (i.e., Idle)
            has_loadable_target = False
            for i in range(self.target_list_widget.count()):
                item = self.target_list_widget.item(i)
                if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                    has_loadable_target = True
                    break

        # Final enablement check
        self.load_unresolved_button.setEnabled(can_enable_buttons and has_loadable_target and bool(self._unresolved_prompt))
        self.load_resolved_button.setEnabled(can_enable_buttons and has_loadable_target and bool(self._resolved_prompt))

        if not enabled:
            self.unresolved_edit.setPlainText("[Select an image first]")
            self.resolved_edit.setPlainText("[Select an image first]")
        elif not (bool(self._unresolved_prompt) or bool(self._resolved_prompt)):
             # If enabled but no prompts found
             self.unresolved_edit.setPlainText("[Not Found]")
             self.resolved_edit.setPlainText("[Not Found]")

        # Update tooltips based on target availability
        if self.current_mode == "Multi" and not has_loadable_target and self.multi_mode_instance_data:
             tooltip = "Cannot load: No idle instances available."
             self.load_unresolved_button.setToolTip(tooltip)
             self.load_resolved_button.setToolTip(tooltip)
        else:
             self.load_unresolved_button.setToolTip("Load the Unresolved Prompt into the selected target.")
             self.load_resolved_button.setToolTip("Load the Resolved Prompt into the selected target.")

    def _browse_for_image(self):
        """Opens a file dialog to select an image."""
        file_filter = "Images (*.png *.jpg *.jpeg *.webp)" # Add more supported types if needed
        # Use last opened directory from settings
        last_dir = self.settings_service.get_setting("last_image_dir", str(Path.home()))
        filepath_tuple = QFileDialog.getOpenFileName(self, "Select Image for Metadata", last_dir, file_filter)
        filepath_str = filepath_tuple[0]

        if filepath_str:
            image_path = Path(filepath_str)
            # Save the directory for next time
            self.settings_service.set_setting("last_image_dir", str(image_path.parent))
            self._process_image_path(image_path)
        else:
            log_debug("Image selection via browse cancelled.")

    @pyqtSlot(Path)
    def _process_image_path(self, image_path: Path):
        """Processes the selected image path: extracts metadata and updates UI."""
        log_info(f"Processing image for metadata: {image_path}")
        self._current_image_path = image_path
        self._unresolved_prompt = None
        self._resolved_prompt = None
        self._selected_prompt_to_load = None
        self._full_result_pixmap = None

        # Reset UI state
        self._set_details_enabled(False)
        self.image_drop_label.setText("Processing...")
        self.image_drop_label.setPixmap(QPixmap()) # Clear previous image
        self.unresolved_edit.clear()
        self.resolved_edit.clear()

        # Extract Metadata
        try:
            self._unresolved_prompt, self._resolved_prompt = ImageProcessor.extract_prompts_from_image(image_path)
            log_info(f"Metadata extracted: Unresolved={bool(self._unresolved_prompt)}, Resolved={bool(self._resolved_prompt)}")

            if self._unresolved_prompt is None and self._resolved_prompt is None:
                show_info_message(self, "Metadata Not Found", f"No embedded prompt metadata found in\n{image_path.name}.")
                self.unresolved_edit.setPlainText("[Not Found]")
                self.resolved_edit.setPlainText("[Not Found]")
            else:
                self.unresolved_edit.setPlainText(self._unresolved_prompt or "[Not Found]")
                self.resolved_edit.setPlainText(self._resolved_prompt or "[Not Found]")
                self._set_details_enabled(True) # Enable sections and potentially buttons

        except Exception as e:
            log_error(f"Error extracting metadata from {image_path}: {e}", exc_info=True)
            show_error_message(self, "Metadata Error", f"Could not extract metadata from image:\n{e}")
            self.unresolved_edit.setPlainText("[Error during extraction]")
            self.resolved_edit.setPlainText("[Error during extraction]")
            self._set_details_enabled(False) # Keep disabled on error

        # Update Preview
        try:
            self._full_result_pixmap = QPixmap(str(self._current_image_path))
            if self._full_result_pixmap.isNull():
                self.image_drop_label.setText("Preview N/A")
                self._full_result_pixmap = None
            else:
                 self.image_drop_label.setToolTip(f"Selected: {self._current_image_path.name}\n({self._full_result_pixmap.width()}x{self._full_result_pixmap.height()})")
                 self._scale_and_set_pixmap() # Display scaled pixmap
        except Exception as e:
            log_error(f"Could not load image preview for {self._current_image_path}: {e}")
            self.image_drop_label.setText("Preview Error")
            self._full_result_pixmap = None

    def _scale_and_set_pixmap(self):
        """Scales the stored _full_result_pixmap to fit the label."""
        if not self._full_result_pixmap or self._full_result_pixmap.isNull():
            self.image_drop_label.setText("Preview N/A") # Set text if no valid pixmap
            return

        label_size = self.image_drop_label.size()
        if label_size.width() <= 1 or label_size.height() <= 1:
            log_warning("Cannot scale image, invalid label size.")
            self.image_drop_label.setPixmap(self._full_result_pixmap) # Set unscaled
            return

        scaled_pixmap = self._full_result_pixmap.scaled(label_size,
                                            Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation)
        current_pm = self.image_drop_label.pixmap()
        # Avoid redundant sets if the scaled size hasn't visually changed
        if not current_pm or scaled_pixmap.cacheKey() != current_pm.cacheKey():
            self.image_drop_label.setPixmap(scaled_pixmap)
            self.image_drop_label.setText("") # Clear text when showing pixmap


    def resizeEvent(self, event):
         """Rescale preview on resize."""
         super().resizeEvent(event)
         self._scale_and_set_pixmap()

    def _load_unresolved(self):
        """Sets the unresolved prompt and target, then accepts the dialog."""
        if not self._unresolved_prompt:
            show_info_message(self, "No Prompt", "Unresolved prompt metadata was not found or couldn't be loaded.")
            return

        if self.current_mode == "Multi":
            selected_item = self.target_list_widget.currentItem()
            if not selected_item or not (selected_item.flags() & Qt.ItemFlag.ItemIsEnabled):
                show_error_message(self, "No Target Selected", "Please select an IDLE instance from the 'Load Target' list.")
                return
            self._selected_target = selected_item.data(Qt.ItemDataRole.UserRole) # e.g., "multi_3"
        else:
            self._selected_target = "single" # Target for single mode

        self._selected_prompt_to_load = self._unresolved_prompt
        log_info(f"Marking unresolved prompt for loading into target: {self._selected_target}")
        super().accept()
        
        

    def _load_resolved(self):
        """Sets the resolved prompt and target, then accepts the dialog."""
        if not self._resolved_prompt:
            show_info_message(self, "No Prompt", "Resolved prompt metadata was not found or couldn't be loaded.")
            return

        if self.current_mode == "Multi":
            selected_item = self.target_list_widget.currentItem()
            if not selected_item or not (selected_item.flags() & Qt.ItemFlag.ItemIsEnabled):
                show_error_message(self, "No Target Selected", "Please select an IDLE instance from the 'Load Target' list.")
                return
            self._selected_target = selected_item.data(Qt.ItemDataRole.UserRole) # e.g., "multi_3"
        else:
            self._selected_target = "single" # Target for single mode

        self._selected_prompt_to_load = self._resolved_prompt
        log_info(f"Marking resolved prompt for loading into target: {self._selected_target}")
        super().accept()
        
        
        
    def get_load_data(self) -> Tuple[Optional[str], Optional[str]]:
        """Returns the prompt text and target identifier selected for loading."""
        return self._selected_prompt_to_load, self._selected_target
        
        
        
    def reject(self):
        """Closes the dialog without loading."""
        log_debug("Image Selector & Metadata Viewer closed.")
        super().reject()

