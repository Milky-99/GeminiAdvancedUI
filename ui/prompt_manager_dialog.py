import os
import traceback
from pathlib import Path
from typing import Dict, Optional, List, Tuple

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, QTextEdit,
    QPushButton, QLineEdit, QLabel, QDialogButtonBox, QMessageBox, QScrollArea, QWidget,
    QFileDialog, QGroupBox, QApplication, QSizePolicy, QFrame, QSplitter
)
from PyQt6.QtGui import QPixmap, QMouseEvent, QPainter, QColor, QPen, QImage, QFontMetrics  # Added QImage
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QTimer, pyqtSlot, QObject, QEvent

# --- Project Imports ---
from core.prompt_service import PromptService
from core.wildcard_resolver import WildcardResolver # For wildcard names
from core.settings_service import SettingsService # <<< --- ADD THIS LINE --- <<<
from utils.logger import log_debug, log_error, log_warning, log_info
from utils.helpers import show_error_message, show_info_message, get_themed_icon
from utils import constants
from core.image_processor import ImageProcessor # For thumbnail loading

# =============================================================================
# Custom Widget for a Single Prompt Entry
# =============================================================================
class PromptEntryWidget(QFrame):
    """Widget representing a single prompt entry with thumbnail, name, and text."""
    # Signals
    change_occurred = pyqtSignal(str) # slot_key - when name/text/thumb changes
    clicked = pyqtSignal(str)         # slot_key - when the widget is clicked for selection
    request_thumbnail = pyqtSignal(str) # slot_key - when thumbnail is clicked

    THUMBNAIL_SIZE = QSize(128, 128)
    SELECTED_BORDER_COLOR = QColor(0, 120, 215) # Example selection color
    NEW_WIDGET_HEIGHT = 280
    # Example: Set thumbnail label height based on new widget height
    NEW_THUMB_HEIGHT = NEW_WIDGET_HEIGHT - 20 # e.g., 260
    NEW_THUMB_WIDTH = NEW_THUMB_HEIGHT       # Keep it square



    def __init__(self, slot_key: str, name: str, text: str, relative_thumb_filename: Optional[str], assets_dir: Path, parent=None):
            super().__init__(parent)
            self.slot_key = slot_key
            self._relative_thumb_filename = relative_thumb_filename
            self._assets_dir = assets_dir
            self._selected = False
            self._pixmap: Optional[QPixmap] = None

            # --- Style & Sizing ---
            self.setObjectName(f"promptEntry_{slot_key}")
            self.setProperty("selected", False)
            self.setMinimumWidth(450) # Keep or adjust minimum width
            self.setFixedHeight(self.NEW_WIDGET_HEIGHT) # <-- Use new height
            self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)

            # Main Layout (Horizontal: Thumb | Vertical: Name+Text)
            layout = QHBoxLayout(self)
            layout.setContentsMargins(5, 5, 5, 5)
            layout.setSpacing(10)

            # Thumbnail Label (Left)
            self.thumbnail_label = QLabel()
            self.thumbnail_label.setObjectName(f"promptThumb_{slot_key}")
            self.thumbnail_label.setFixedSize(QSize(self.NEW_THUMB_WIDTH, self.NEW_THUMB_HEIGHT)) # <-- Use new size
            self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.thumbnail_label.setStyleSheet("QLabel { border: 1px solid grey; background-color: #e0e0e0; color: grey; }")
            self.thumbnail_label.setToolTip("Click to select a thumbnail image")
            self.thumbnail_label.mousePressEvent = self._on_thumbnail_click
            layout.addWidget(self.thumbnail_label)

            # Right Panel (Name + Text)
            right_panel_layout = QVBoxLayout()
            right_panel_layout.setSpacing(3)

            # --- ADD THIS BLOCK ---
            # Slot Key Label and Name Editor in an HBox
            name_layout = QHBoxLayout()
            name_layout.setSpacing(5)
            self.slot_label = QLabel(f"<b>{slot_key}</b>") # Display slot key
            self.slot_label.setObjectName(f"slotLabel_{slot_key}")
            font = self.slot_label.font()
            font.setPointSize(font.pointSize() - 1) # Make slot label slightly smaller
            self.slot_label.setFont(font)
            self.slot_label.setStyleSheet("color: grey;") # Style to distinguish
            # Optionally calculate a fixed width for the slot label based on font size
            # fm = QFontMetrics(font)
            # self.slot_label.setFixedWidth(fm.horizontalAdvance("slot_XX") + 5) # Adjust based on max expected slot key
            name_layout.addWidget(self.slot_label)

            self.name_edit = QLineEdit(name)
            self.name_edit.setObjectName(f"promptName_{slot_key}")
            self.name_edit.setPlaceholderText("Prompt Name")
            self.name_edit.textChanged.connect(self._on_change)
            name_layout.addWidget(self.name_edit, 1) # Name edit takes remaining space

            right_panel_layout.addLayout(name_layout) # Add the new HBox here
            # --- END ADD BLOCK ---


            # Text Editor
            self.text_edit = QTextEdit(text)
            self.text_edit.setObjectName(f"promptText_{slot_key}")
            self.text_edit.setPlaceholderText("Prompt Text...")
            self.text_edit.setAcceptRichText(False)
            self.text_edit.installEventFilter(self)
            self.text_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.text_edit.textChanged.connect(self._on_change)
            right_panel_layout.addWidget(self.text_edit, 1) # Add stretch

            layout.addLayout(right_panel_layout, 1)

            self.load_thumbnail() # This will now scale to the larger label size




   
    def get_data(self) -> Tuple[str, str, Optional[str]]:
        """Returns the current name, text, and relative thumbnail filename."""
        return (
            self.name_edit.text().strip(),
            self.text_edit.toPlainText().strip(),
            self._relative_thumb_filename # Return the relative name
        )

    def get_text_editor(self) -> QTextEdit:
        """Returns the QTextEdit widget for external manipulation (like wildcard insertion)."""
        return self.text_edit

    def set_relative_thumbnail_filename(self, filename: Optional[str]):
        """Sets the relative thumbnail filename and reloads the thumbnail."""
        if self._relative_thumb_filename != filename:
            self._relative_thumb_filename = filename
            self.load_thumbnail() # Reload based on the new filename
            self.change_occurred.emit(self.slot_key) # Signal change


    def load_thumbnail(self):
        """Loads and displays the thumbnail using the relative filename and assets_dir."""
        self._pixmap = None # Clear previous pixmap
        if self._relative_thumb_filename and self._assets_dir:
            thumb_file = self._assets_dir / self._relative_thumb_filename
            if thumb_file.is_file():
                try:
                    # Use processor to handle loading/resizing safely
                    # Load directly from the saved thumbnail file
                    img_bytes = thumb_file.read_bytes()
                    if img_bytes:
                         qimage = QImage.fromData(img_bytes)
                         if not qimage.isNull():
                              self._pixmap = QPixmap.fromImage(qimage)
                    if self._pixmap:
                        # Scale to fit the label
                        target_size = self.thumbnail_label.size()
                        if target_size.width() <= 1 or target_size.height() <= 1:
                            target_size = self.THUMBNAIL_SIZE # Fallback size
                        self.thumbnail_label.setPixmap(self._pixmap.scaled(
                            target_size, # Scale to label size
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation
                        ))
                        self.thumbnail_label.setToolTip(f"Thumbnail: {self._relative_thumb_filename}\nClick to change.")
                        return # Success
                    else:
                         log_warning(f"Failed to load QPixmap for {self.slot_key} from {thumb_file}")

                except Exception as e:
                    log_error(f"Error loading thumbnail for {self.slot_key} from {thumb_file}: {e}", exc_info=True)
            else:
                 log_warning(f"Thumbnail file for {self.slot_key} not found: {thumb_file}")
                 # Optionally reset the path if invalid?
                 # self._relative_thumb_filename = None
                 # self.change_occurred.emit(self.slot_key)

        # Fallback / No thumbnail
        self.thumbnail_label.clear()
        self.thumbnail_label.setText("No\nThumb")
        self.thumbnail_label.setToolTip("Click to select a thumbnail image")



    def set_selected(self, selected: bool):
        """Sets the visual selection state using a QSS property."""
        if self._selected != selected:
            self._selected = selected
            self.setProperty("selected", selected) # Set the custom property
            # Force a style re-computation
            self.style().polish(self)
            self.update() # Request redraw if needed after style change

    def eventFilter(self, source: QObject, event: QEvent) -> bool:
        """Filters wheel events on the text edit to prevent content scrolling."""
        # Check if the event source is the text_edit within this widget
        if source == self.text_edit and event.type() == QEvent.Type.Wheel:
            # Ignore the wheel event on the text edit
            event.ignore() # Mark as ignored so it might propagate
            return True # Indicate we've handled (ignored) it here
        # For all other events or sources, use default processing
        return super().eventFilter(source, event)


    def mousePressEvent(self, event: QMouseEvent):
        """Emit clicked signal when the widget area is clicked."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.slot_key)
            # Important: Accept the event so it doesn't propagate further if needed
            event.accept()
        # Allow context menu events etc. by calling base implementation
        super().mousePressEvent(event)

    def _on_thumbnail_click(self, event: QMouseEvent):
        """Emit signal to request thumbnail change."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.request_thumbnail.emit(self.slot_key)
            event.accept()

    def _on_change(self):
        """Emit signal when name or text changes."""
        self.change_occurred.emit(self.slot_key)

# =============================================================================
# Redesigned Prompt Manager Dialog
# =============================================================================
class PromptManagerDialog(QDialog):
    """Dialog for managing saved prompts with improved UI and delayed saving."""

    Accepted = QDialog.DialogCode.Accepted # Re-use standard code

    def __init__(self,
                 prompt_service: PromptService,
                 wildcard_resolver: WildcardResolver, # Needed for wildcard list
                 settings_service: SettingsService, # Needed for context/loading
                 current_mode: str,                 # Added
                 multi_mode_instance_data: Optional[List[Tuple[int, str, str, str]]], # Added
                 parent=None):
        super().__init__(parent)
        self.prompt_service = prompt_service
        self.wildcard_resolver = wildcard_resolver
        self.settings_service = settings_service
        self.current_mode = current_mode
        self.multi_mode_instance_data = multi_mode_instance_data or []

        self._prompt_widgets: Dict[str, PromptEntryWidget] = {} # slot_key -> widget
        self._selected_slot_key: Optional[str] = None
        self._is_dirty = False # Track unsaved changes
        self._selected_prompt_to_load: Optional[str] = None
        self._selected_target: Optional[str] = None # Stores "single" or "multi_{id}"
        # +++ START ADD +++
        # Define the assets directory for this dialog instance
        self._assets_dir = constants.PROMPTS_ASSETS_DIR
        # +++ END ADD +++

        self.setWindowTitle("Prompt Manager")
        self.setMinimumSize(900, 700) # Larger default size

        self._setup_ui()
        self._connect_signals()
        self._load_prompts()

        # Configure UI based on mode
        if self.current_mode == "Multi":
            self._setup_multi_mode_target_selector()
        else:
            if hasattr(self, 'target_group'):
                self.target_group.setVisible(False)
        self._update_action_buttons() # Initial state

    def _setup_ui(self):
        self.setObjectName("promptManagerDialog")
        # --- Main Vertical Layout ---
        main_layout = QVBoxLayout(self)
        main_layout.setObjectName("mainLayoutPromptManager")
        main_layout.setSpacing(10) # Add some spacing between main sections

        # --- Wildcard Helper (Full Width Top Section) ---
        wildcard_group = QGroupBox("Insert Wildcard")
        wildcard_group.setObjectName("wildcardGroup")
        wildcard_group_layout = QVBoxLayout(wildcard_group)
        # Let this scroll area take necessary height but don't fix it absolutely
        self.wildcard_scroll_area = QScrollArea()
        self.wildcard_scroll_area.setObjectName("wildcardScrollArea")
        self.wildcard_scroll_area.setWidgetResizable(True)
        self.wildcard_scroll_area.setFixedHeight(80) # Keep fixed height for helper
        self.wildcard_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.wildcard_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        wildcard_widget = QWidget()
        wildcard_widget.setObjectName("wildcardContainer")
        self.wildcard_button_layout = QHBoxLayout(wildcard_widget)
        self.wildcard_button_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._setup_wildcard_buttons() # Populate the wildcard buttons

        self.wildcard_scroll_area.setWidget(wildcard_widget)
        wildcard_group_layout.addWidget(self.wildcard_scroll_area)
        # Add wildcard group first to the main vertical layout
        main_layout.addWidget(wildcard_group) # Give it minimal vertical stretch

        # --- Content Splitter (Prompts | Actions/Target) ---
        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        content_splitter.setObjectName("contentSplitterPromptManager")

        # --- Left Side: Scrollable Prompt Entries ---
        left_pane_widget = QWidget() # Container for left side
        left_pane_widget.setObjectName("promptListPane")
        left_pane_layout = QVBoxLayout(left_pane_widget)
        left_pane_layout.setContentsMargins(0,0,0,0)
        left_pane_layout.setSpacing(0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("promptScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Set a minimum width for usability
        # self.scroll_area.setMinimumWidth(450) # Minimum width is now controlled by splitter

        self.scroll_widget = QWidget() # Container widget inside scroll area
        self.scroll_widget.setObjectName("promptScrollContainer")
        self.prompts_layout = QVBoxLayout(self.scroll_widget) # Layout for PromptEntryWidgets
        self.prompts_layout.setObjectName("promptsEntryLayout")
        self.prompts_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.prompts_layout.setSpacing(10) # Spacing between entries

        # Placeholder when empty
        self.placeholder_label = QLabel("Click 'Add New Prompt' to begin.")
        self.placeholder_label.setObjectName("promptPlaceholderLabel")
        self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_label.setMinimumHeight(100)
        self.prompts_layout.addWidget(self.placeholder_label)
        self.prompts_layout.addStretch(1) # Push entries up

        self.scroll_area.setWidget(self.scroll_widget)
        left_pane_layout.addWidget(self.scroll_area) # Add scroll area to the left pane layout
        content_splitter.addWidget(left_pane_widget) # Add left pane to splitter

        # --- Right Side: Buttons & Target List ---
        right_panel_widget = QWidget() # Container widget for the right panel
        right_panel_widget.setObjectName("rightPanelWidgetPromptManager")
        right_panel_layout = QVBoxLayout(right_panel_widget) # Layout for the right panel widget
        right_panel_layout.setObjectName("rightPanelLayoutPromptManager")
        right_panel_layout.setContentsMargins(5, 5, 5, 5) # Give right panel some margin
        right_panel_layout.setSpacing(10)
        # Set a maximum width for the right panel to keep it narrow
        # right_panel_widget.setMaximumWidth(300) # Max width handled by splitter now

        # --- Action Buttons ---
        # (Keep button creation the same)
        self.add_button = QPushButton(get_themed_icon("add.png"), " Add New Prompt")
        self.add_button.setObjectName("addButtonPromptManager")
        self.add_button.setIconSize(QSize(16,16))
        self.delete_button = QPushButton(get_themed_icon("delete.png"), " Delete Selected")
        self.delete_button.setObjectName("deleteButtonPromptManager")
        self.delete_button.setIconSize(QSize(16,16))
        
        self.load_button = QPushButton(get_themed_icon("load.png"), " Load Selected Prompt")
        self.load_button.setObjectName("loadButtonPromptManager")
        self.load_button.setIconSize(QSize(16,16))

        # Add action buttons
        right_panel_layout.addWidget(self.add_button)
        right_panel_layout.addWidget(self.delete_button)
        right_panel_layout.addSpacing(20) # Space before Load button
        right_panel_layout.addWidget(self.load_button)

        # --- Target Selection (for Multi-Mode / Load) ---
        # (Keep target group creation the same)
        self.target_group = QGroupBox("Load Target")
        self.target_group.setObjectName("targetGroupPromptManager")
        self.target_group.setVisible(False) # Hidden by default
        target_layout = QVBoxLayout(self.target_group)
        target_layout.setObjectName("targetLayoutPromptManager")
        target_info_label = QLabel("Select instance to load into:")
        target_info_label.setObjectName("targetInfoLabelPromptManager")
        target_layout.addWidget(target_info_label)
        self.target_list_widget = QListWidget()
        self.target_list_widget.setObjectName("targetListWidgetPromptManager")
        self.target_list_widget.setToolTip("Double-click or select and press 'Load...'")
        self.target_list_widget.setMaximumHeight(150) # Keep constrained height
        target_layout.addWidget(self.target_list_widget)
        # Add Target group
        right_panel_layout.addWidget(self.target_group)

        # Add stretch to push buttons up
        right_panel_layout.addStretch(1)

        content_splitter.addWidget(right_panel_widget) # Add right panel to splitter

        # --- Set Splitter Sizes ---
        # Give more space to the left (prompts) than the right
        content_splitter.setSizes([600, 250]) # Adjust these initial sizes as needed

        # Add the splitter to the main vertical layout, giving it stretch
        main_layout.addWidget(content_splitter, 1) # Stretch factor 1 (takes available vertical space)

        # --- Dialog Close Button ---
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.button_box.setObjectName("dialogButtonBoxPromptManager")
        close_button = self.button_box.button(QDialogButtonBox.StandardButton.Close)
        if close_button: close_button.setObjectName("closeButtonPromptManager")
        main_layout.addWidget(self.button_box) # Add close button at the bottom


    def _setup_wildcard_buttons(self):
        """Populates the horizontal scroll area with wildcard buttons."""
        # Clear existing buttons if any
        while self.wildcard_button_layout.count():
            item = self.wildcard_button_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        try:
            if constants.WILDCARDS_DIR.is_dir():
                wildcard_files = sorted(constants.WILDCARDS_DIR.glob("*.json"))
                if not wildcard_files:
                     self.wildcard_button_layout.addWidget(QLabel("No wildcards found."))
                else:
                    for w_file in wildcard_files:
                        wc_name = w_file.stem # Get name without extension
                        button = QPushButton(wc_name)
                        button.setObjectName(f"wildcardBtn_{wc_name}")
                        button.setToolTip(f"Insert [{wc_name}]")
                        button.setFlat(True) # Make them look less bulky
                        # Use lambda to capture the correct name for the slot
                        button.clicked.connect(lambda checked=False, name=wc_name: self._insert_wildcard(name))
                        self.wildcard_button_layout.addWidget(button)
            else:
                 self.wildcard_button_layout.addWidget(QLabel("Wildcards dir not found."))
        except Exception as e:
            log_error(f"Error loading wildcards for helper: {e}", exc_info=True)
            self.wildcard_button_layout.addWidget(QLabel("Error loading wildcards."))
        self.wildcard_button_layout.addStretch(1)

    def _insert_wildcard(self, wildcard_name: str):
        """Inserts the selected wildcard into the currently selected prompt's text edit."""
        if self._selected_slot_key and self._selected_slot_key in self._prompt_widgets:
            editor = self._prompt_widgets[self._selected_slot_key].get_text_editor()
            editor.insertPlainText(f"[{wildcard_name}]")
            editor.setFocus()
        else:
            show_info_message(self, "No Prompt Selected", "Please select a prompt entry first to insert the wildcard.")

    def _setup_multi_mode_target_selector(self):
        """Populates the target list widget for Multi-Mode loading."""
        # This logic is largely copied from ImageSelectorMetaViewerDialog
        self.target_list_widget.clear()
        has_loadable_target = False

        if not self.multi_mode_instance_data:
            item = QListWidgetItem("No instances available.")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.target_list_widget.addItem(item)
        else:
            for instance_id, key_name, prompt_start, status in self.multi_mode_instance_data:
                prompt_display = prompt_start[:40] + "..." if len(prompt_start) > 40 else prompt_start
                display_text = f"#{instance_id} [{key_name}] ({status})" #- '{prompt_display}'"
                item = QListWidgetItem(display_text)
                item.setData(Qt.ItemDataRole.UserRole, f"multi_{instance_id}")

                if status != "Idle":
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                    item.setForeground(Qt.GlobalColor.gray)
                    item.setToolTip(f"Instance #{instance_id} is {status.lower()} and cannot be loaded into.")
                else:
                    item.setToolTip(f"Load prompt into Instance #{instance_id}")
                    has_loadable_target = True

                self.target_list_widget.addItem(item)

        self.target_group.setVisible(True)
        self._update_action_buttons() # Update load button state

    def _connect_signals(self):
        self.add_button.clicked.connect(self._add_prompt)
        self.delete_button.clicked.connect(self._delete_prompt)
        self.load_button.clicked.connect(self._initiate_load_prompt)
        self.button_box.rejected.connect(self.reject) # Close button

        # Connect double-click on target list (optional convenience)
        if hasattr(self, 'target_list_widget'):
            self.target_list_widget.itemDoubleClicked.connect(self._handle_target_double_click)

    def _load_prompts(self):
        """Loads prompts from service and creates/updates widgets."""
        log_debug("Loading prompts into manager dialog.")
        # Clear existing widgets
        while self.prompts_layout.count() > 1: # Keep placeholder + stretch
            item = self.prompts_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._prompt_widgets.clear()
        self._selected_slot_key = None
        self._is_dirty = False # Reset dirty flag on full reload

        prompts_data = self.prompt_service.get_all_prompts_full()
        sorted_slots = sorted(prompts_data.keys(), key=lambda k: int(k.split('_')[-1]) if k.startswith("slot_") else float('inf'))

        if not sorted_slots:
            self.placeholder_label.show()
        else:
            self.placeholder_label.hide()
            for slot_key in sorted_slots:
                data = prompts_data[slot_key]
                self._create_and_add_prompt_widget(
                    slot_key,
                    data.get("name", "Error"),
                    data.get("text", ""),
                    data.get("thumbnail_path")
                )

        self._update_action_buttons()


    def _create_and_add_prompt_widget(self, slot_key: str, name: str, text: str, relative_thumb_filename: Optional[str]):
        """Creates a PromptEntryWidget, connects its signals, and adds it to the layout."""
        widget = PromptEntryWidget(
            slot_key,
            name,
            text,
            relative_thumb_filename,
            constants.PROMPTS_ASSETS_DIR, # Pass the assets directory path
            self.scroll_widget
        )
        widget.change_occurred.connect(self._handle_prompt_change)
        widget.clicked.connect(self._handle_prompt_click)
        widget.request_thumbnail.connect(self._handle_thumbnail_request)

        # Insert before the stretch item
        self.prompts_layout.insertWidget(self.prompts_layout.count() - 1, widget)
        self._prompt_widgets[slot_key] = widget
        return widget




    # --- Signal Handlers for PromptEntryWidget ---
    @pyqtSlot(str)
    def _handle_prompt_change(self, slot_key: str):
        """Marks the dialog as dirty when a prompt entry changes."""
        if not self._is_dirty:
            log_debug(f"Change detected in {slot_key}. Marking dialog as dirty.")
            self._is_dirty = True
            self._update_action_buttons() # Enable Save button

    @pyqtSlot(str)
    def _handle_prompt_click(self, slot_key: str):
        """Handles selection of a prompt entry."""
        log_debug(f"Prompt entry clicked: {slot_key}")
        if self._selected_slot_key == slot_key:
            return # No change

        # Deselect previous
        if self._selected_slot_key and self._selected_slot_key in self._prompt_widgets:
            self._prompt_widgets[self._selected_slot_key].set_selected(False)

        # Select new
        self._selected_slot_key = slot_key
        if slot_key in self._prompt_widgets:
            self._prompt_widgets[slot_key].set_selected(True)
            # Scroll to ensure the selected item is visible
            QTimer.singleShot(0, lambda w=self._prompt_widgets[slot_key]: self.scroll_area.ensureWidgetVisible(w, yMargin=10))

        self._update_action_buttons() # Update Delete/Load button states






    @pyqtSlot(str)
    def _handle_thumbnail_request(self, slot_key: str):
        """Opens a file dialog, creates/saves a new thumbnail, deletes the old one, and updates the widget."""
        log_debug(f"Thumbnail change requested for {slot_key}")
        if slot_key not in self._prompt_widgets: return

        widget = self._prompt_widgets[slot_key]
        # Use get_data to get the current name and text from the widget
        current_name, current_text, relative_thumb_filename = widget.get_data() # Get relative filename

        # --- Get last used directory from SettingsService ---
        start_dir = self.settings_service.get_setting("last_thumbnail_browse_dir", str(Path.home()))
        log_debug(f"Opening thumbnail browser in: {start_dir}")
        # --- End Get last used directory ---

        file_filter = "Images (*.png *.jpg *.jpeg *.webp)"
        filepath_tuple = QFileDialog.getOpenFileName(self, f"Select Thumbnail Image for {current_name} ({slot_key})", start_dir, file_filter)
        selected_original_path_str = filepath_tuple[0]

        if selected_original_path_str:
            selected_original_path = Path(selected_original_path_str)
            # --- Save directory for next time ---
            self.settings_service.set_setting("last_thumbnail_browse_dir", str(selected_original_path.parent))
            # --- End Save directory ---

            new_thumb_filename = f"{slot_key}.png" # Standardized filename
            new_thumb_full_path = constants.PROMPTS_ASSETS_DIR / new_thumb_filename

            # --- Delete Old Thumbnail File (if it exists) ---
            if relative_thumb_filename:
                thumb_file_path = constants.PROMPTS_ASSETS_DIR / relative_thumb_filename
                if thumb_file_path.is_file():
                    try:
                        thumb_file_path.unlink()
                        log_info(f"Deleted old thumbnail file: {thumb_file_path}")
                    except OSError as e:
                        log_error(f"Error deleting old thumbnail file {thumb_file_path}: {e}")
                        # Show error but continue deleting prompt data? Maybe.
                        show_error_message(self, "Thumbnail Error", f"Could not delete the old thumbnail file:\n{thumb_file_path}\n{e}")
                else:
                    log_debug(f"Old thumbnail file path found in data ('{relative_thumb_filename}'), but file doesn't exist at {thumb_file_path}, skipping deletion.")

            # --- Create and Save New Thumbnail ---
            log_info(f"Creating thumbnail for {slot_key} from {selected_original_path.name} -> {new_thumb_full_path}")
            try:
                constants.PROMPTS_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
                # Generate thumbnail bytes (use desired larger size like 256x256, consistent with PromptService)
                thumb_bytes = ImageProcessor.create_thumbnail_bytes(selected_original_path, size=(256, 256))

                if thumb_bytes:
                    new_thumb_full_path.write_bytes(thumb_bytes)
                    log_info(f"Successfully saved new thumbnail file: {new_thumb_full_path}")

                    # --- ADD THIS LINE: Update PromptService memory with the new thumbnail path ---
                    # Use the current name and text from the widget, as these might have been edited.
                    self.prompt_service.update_prompt_data_in_memory(slot_key, current_name, current_text, new_thumb_filename)
                    # --- END ADD ---

                    # Now update the widget itself (this also emits change_occurred)
                    widget.set_relative_thumbnail_filename(new_thumb_filename)

                else:
                    log_error(f"Failed to create thumbnail bytes from {selected_original_path.name}")
                    show_error_message(self, "Thumbnail Error", f"Could not process the selected image:\n{selected_original_path.name}")
                    # If thumbnail creation failed, update memory to reflect no thumbnail
                    self.prompt_service.update_prompt_data_in_memory(slot_key, current_name, current_text, None)
                    widget.set_relative_thumbnail_filename(None)


            except Exception as e:
                log_error(f"Error creating/saving thumbnail {new_thumb_filename}: {e}", exc_info=True)
                show_error_message(self, "Thumbnail Error", f"Could not create or save the thumbnail:\n{e}")
                # If an error occurred, update memory to reflect no thumbnail
                self.prompt_service.update_prompt_data_in_memory(slot_key, current_name, current_text, None)
                widget.set_relative_thumbnail_filename(None)

        else:
            log_debug(f"Thumbnail selection cancelled for {slot_key}.")





    def _add_prompt(self):
        """Adds a new prompt entry."""
        log_info("Adding new prompt.")
        new_name = "New Prompt"
        new_text = ""

        # Add to service (only adds to memory now)
        # --- FIX: Correct method name from add_prompt to add_prompt_to_memory ---
        new_slot = self.prompt_service.add_prompt_to_memory(new_name, new_text)
        # --- END FIX ---

        if new_slot:
            # If placeholder was visible, hide it
            if self.placeholder_label.isVisible():
                self.placeholder_label.hide()

            # Create and add widget
            new_widget = self._create_and_add_prompt_widget(new_slot, new_name, new_text, None)

            # Select the new widget automatically
            self._handle_prompt_click(new_slot) # This handles highlighting and button updates
            new_widget.name_edit.setFocus()
            new_widget.name_edit.selectAll()

            # Mark as dirty because a new (unsaved) prompt was added
            if not self._is_dirty:
                self._is_dirty = True
                self._update_action_buttons()
        else:
            show_error_message(self, "Add Error", "Failed to add new prompt slot. Max slots may be reached or name invalid.")
        self._update_action_buttons()

    @pyqtSlot()
    def _delete_prompt(self):
        """Deletes the selected prompt widget, its data in memory, and its associated thumbnail file."""
        if not self._selected_slot_key or self._selected_slot_key not in self._prompt_widgets:
            return

        widget_to_delete = self._prompt_widgets[self._selected_slot_key]
        prompt_name, _, relative_thumb_filename = widget_to_delete.get_data() # Get relative filename

        reply = QMessageBox.question(self, "Confirm Delete",
                                     f"Are you sure you want to delete the prompt\n'{prompt_name}' ({self._selected_slot_key})?\n\nThis action only takes effect after clicking 'Save All Changes'.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            log_info(f"Marking prompt {self._selected_slot_key} for deletion.")

            # --- Delete Associated Thumbnail File ---
            if relative_thumb_filename:
                thumb_file_path = constants.PROMPTS_ASSETS_DIR / relative_thumb_filename
                if thumb_file_path.is_file():
                    try:
                        thumb_file_path.unlink()
                        log_info(f"Deleted associated thumbnail file: {thumb_file_path}")
                    except OSError as e:
                        log_error(f"Error deleting thumbnail file {thumb_file_path}: {e}")
                        # Show error but continue deleting prompt data? Maybe.
                        show_error_message(self, "Delete Error", f"Could not delete thumbnail file:\n{thumb_file_path}\n{e}")
                else:
                    log_warning(f"Thumbnail file '{relative_thumb_filename}' for {self._selected_slot_key} not found at {thumb_file_path}, skipping deletion.")
            else:
                log_debug(f"No associated thumbnail file to delete for {self._selected_slot_key}.")

            # --- Remove from service (memory only) ---
            if self.prompt_service.remove_prompt(self._selected_slot_key):
                 # Remove widget
                 widget_to_delete.deleteLater()
                 del self._prompt_widgets[self._selected_slot_key]
                 self._selected_slot_key = None

                 # Show placeholder if needed
                 if not self._prompt_widgets:
                     self.placeholder_label.show()

                 self._is_dirty = True # Mark as dirty
                 self._update_action_buttons()
            else:
                 # Should not happen if key exists, but handle anyway
                 log_error(f"Failed to remove prompt {self._selected_slot_key} from service memory.")
                 show_error_message(self, "Delete Error", "An internal error occurred trying to remove the prompt from memory.")







    @pyqtSlot()
    def _initiate_load_prompt(self):
        """Handles the 'Load Prompt' button click."""
        if not self._selected_slot_key or self._selected_slot_key not in self._prompt_widgets:
            show_info_message(self, "Load Error", "Please select a prompt to load first.")
            return

        # Get selected prompt text
        selected_widget = self._prompt_widgets[self._selected_slot_key]
        self._selected_prompt_to_load = selected_widget.get_data()[1] # Get text

        # Determine target
        if self.current_mode == "Multi":
            selected_item = self.target_list_widget.currentItem()
            if not selected_item or not (selected_item.flags() & Qt.ItemFlag.ItemIsEnabled):
                show_error_message(self, "No Target Selected", "Please select an IDLE instance from the 'Load Target' list.")
                return
            self._selected_target = selected_item.data(Qt.ItemDataRole.UserRole) # e.g., "multi_3"
        else:
            self._selected_target = "single" # Target for single mode

        log_info(f"Prompt Manager: Marking prompt from '{self._selected_slot_key}' for loading into target: {self._selected_target}")
        super().accept() # Close dialog with Accepted state

    @pyqtSlot(QListWidgetItem)
    def _handle_target_double_click(self, item: QListWidgetItem):
        """Handles double-clicking on a target instance."""
        if not item or not (item.flags() & Qt.ItemFlag.ItemIsEnabled): return
        # Trigger the load action as if the button was clicked
        self._initiate_load_prompt()


    def get_load_data(self) -> Tuple[Optional[str], Optional[str]]:
        """Returns the prompt text and target identifier selected for loading."""
        # Ensure data is only returned if dialog was accepted properly
        # This check might be redundant if called only after accept()
        if self.result() == QDialog.DialogCode.Accepted:
             return self._selected_prompt_to_load, self._selected_target
        return None, None

    def _update_action_buttons(self):
        """Enable/disable right-side buttons based on state."""
        has_selection = self._selected_slot_key is not None
        self.delete_button.setEnabled(has_selection)

        # Load button logic
        can_load = has_selection
        if self.current_mode == "Multi":
            # Check if any loadable target exists
            has_loadable_target = False
            for i in range(self.target_list_widget.count()):
                item = self.target_list_widget.item(i)
                if item and (item.flags() & Qt.ItemFlag.ItemIsEnabled):
                    has_loadable_target = True
                    break
            if not has_loadable_target:
                can_load = False # Disable load if no target available
        self.load_button.setEnabled(can_load)

        # Update wildcard insert buttons (enable only if a prompt is selected)
        for i in range(self.wildcard_button_layout.count()):
             item = self.wildcard_button_layout.itemAt(i)
             widget = item.widget()
             if isinstance(widget, QPushButton):
                  widget.setEnabled(has_selection)


    def reject(self):
        """Saves changes automatically and closes the dialog."""
        log_info("Prompt Manager close requested. Saving changes automatically...")

        # Always attempt to save changes if dialog is closed, regardless of _is_dirty flag
        # First, update the in-memory data in PromptService from the widgets
        errors = []
        for slot_key, widget in self._prompt_widgets.items():
            name, text, thumb_path = widget.get_data()
            # Also handle the case where the widget was marked for deletion in _delete_prompt
            # Only attempt to update in-memory if the slot key still exists in the service's internal dict
            # The remove_prompt method removes from _prompts dictionary
            if slot_key in self.prompt_service._prompts: # Check internal dict
                 if not self.prompt_service.update_prompt_data_in_memory(slot_key, name, text, thumb_path):
                    errors.append(f"Failed to update data for {slot_key} ('{name}') in memory before saving.")
            # If the slot_key is NOT in self.prompt_service._prompts, it means it was marked for deletion,
            # so we don't try to update it in memory before saving.


        if errors:
            log_error(f"Errors occurred updating prompt data in memory before closing: {errors}")
            # As requested, no popups for these errors - just log them.

        # Then, save the entire state from PromptService to the file
        # This calls the public save_all_prompts method which also emits the signal
        try:
            # The save_all_prompts method should handle saving the *current state* of self.prompt_service._prompts
            # Which includes additions, updates, and deletions performed in memory.
            if self.prompt_service.save_all_prompts():
                self._is_dirty = False # Reset dirty flag on successful save
                log_info("Prompt changes saved successfully on close.")
                # No success message popup.
            else:
                log_error("Failed to write changes to the prompts file on close.")
                # No failure message popup.
        except AttributeError:
             log_critical("PromptService.save_all_prompts is missing! Prompt changes were NOT saved.", exc_info=True)
             # Fatal error for save, but still close the dialog as requested.
        except Exception as e:
             log_critical(f"Unexpected error during prompt save on close: {e}", exc_info=True)

        # --- ADD THIS BLOCK ---
        # Disconnect the signal from PromptService to prevent crashes
        try:
            if hasattr(self.prompt_service, 'prompts_updated') and hasattr(self, '_handle_external_prompt_update'):
                # We need the specific bound method reference to disconnect
                # This assumes the connection was made using a direct method reference
                # like: self.prompt_service.prompts_updated.connect(self._handle_external_prompt_update)
                self.prompt_service.prompts_updated.disconnect(self._handle_external_prompt_update)
                log_debug("Disconnected prompt_service.prompts_updated from PromptManagerDialog on close.")
            # If the connection was made via lambda or partial, disconnecting might be harder
            # or require storing the connection object, but direct method connection is best practice here.
        except (TypeError, RuntimeError) as e:
            log_warning(f"Error disconnecting prompt service signal from PromptManagerDialog: {e}")
        # --- END ADD BLOCK ---

        log_debug("Prompt Manager finished save attempt, closing dialog.")
        super().reject() # Always close after attempting save


    def closeEvent(self, event):
         """Ensures reject logic is called when closing via window [X]."""
         # The reject method now handles saving automatically without prompting
         self.reject()
         # Check if reject() prevented closing (by returning without calling super().reject())
         # In this case, reject() *always* calls super().reject() after saving,
         # so we always accept the close event here.
         event.accept()


    def closeEvent(self, event):
         """Ensures reject logic is called when closing via window [X]."""
         # The reject method now handles saving automatically without prompting
         self.reject()
         # Always accept the close event after calling reject, as reject handles the user decision
         event.accept()
             
              


    @pyqtSlot()
    def _handle_external_prompt_update(self):
        """Handles prompt updates triggered externally (e.g., save from mode widget)."""
        log_info("PromptManagerDialog received external prompt update signal.")

        # As per user request, do NOT prompt about discarding changes or show any message.
        # Just silently reload the prompts from the service.
        # This updates the dialog's view to match the saved state if an external save occurred.

        log_info("Silently reloading prompts in manager due to external update.")
        # Store current selection to try and restore it after reload
        current_key_to_restore = self._selected_slot_key

        # Reload the prompts list from the service (which reads the updated file)
        # This will clear and recreate all widgets based on the new data
        self._load_prompts() # This method correctly clears _is_dirty and updates the UI

        # Try to restore selection if the slot key still exists after reload
        if current_key_to_restore and current_key_to_restore in self._prompt_widgets:
             log_debug(f"Attempting to restore selection to {current_key_to_restore}")
             # Use QTimer to ensure selection happens after layout updates are processed
             # Pass the key to the selection handler
             QTimer.singleShot(0, lambda key=current_key_to_restore: self._handle_prompt_click(key))
        elif self._prompt_widgets:
             # If the previously selected slot is gone or no previous selection,
             # select the first item if the list is not empty.
             first_key = next(iter(self._prompt_widgets.keys()), None)
             if first_key:
                 log_debug("Previous selection lost or none selected, selecting first item.")
                 QTimer.singleShot(0, lambda key=first_key: self._handle_prompt_click(key))
        else:
             # The prompt list is now empty
             self._selected_slot_key = None
             self._update_action_buttons() # Ensure buttons are correctly disabled if the list is empty

        # No need to emit patterns_updated or mark as dirty here - this signal indicates a change has
        # *already* been saved externally, and _load_prompts resets the dirty state internally.