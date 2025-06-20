
import json 
import re 
from functools import partial
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, QTextEdit,
    QPushButton, QLabel, QDialogButtonBox, QMessageBox, QSplitter, QWidget, QSizePolicy, QFileDialog, QInputDialog, QApplication, QFrame, QStyle, QStyledItemDelegate, QLineEdit, QStyleOptionViewItem 
)
from PyQt6.QtGui import QColor, QPalette, QAction, QMouseEvent, QPainter, QFontMetrics, QPen
from PyQt6.QtCore import Qt, QDir, pyqtSlot, QTimer, QRect, QPoint, QSize
# --- End Add this line ---
# --- Project Imports ---
from utils import constants # To get WILDCARDS_DIR
from utils.logger import log_debug, log_error, log_warning, log_info
from utils.helpers import show_error_message, show_info_message, get_themed_icon
# We need the resolver to clear its cache when a file is saved
from core.wildcard_resolver import WildcardResolver
from typing import Dict, List, Optional, Tuple, Any


class WildcardItemDelegate(QStyledItemDelegate):
    """Delegate for painting wildcard list items efficiently with score-based background tinting."""
    # Define score thresholds (copied from old widget)
    POSITIVE_THRESHOLD = 3
    NEGATIVE_THRESHOLD = -2
    MAX_INTENSITY_SCORE = 10 # Keep for potential future use, though QSS overrides

    PADDING_VERTICAL = 5
    PADDING_HORIZONTAL = 8
    SCORE_AREA_WIDTH = 50 # Approximate width for S:, B:, Avg: columns
    SPACING = 10 # Space between elements


    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        painter.save()

        # --- Data Retrieval ---
        entry_data = index.data(Qt.ItemDataRole.UserRole)
        score_state = index.data(Qt.ItemDataRole.UserRole + 1) # Get score state string
        if not isinstance(entry_data, dict) or not isinstance(score_state, str):
            # Fallback to default painting if data is missing or invalid
            log_warning(f"WildcardItemDelegate: Invalid data for index {index.row()}. Using default paint.")
            super().paint(painter, option, index)
            painter.restore()
            return

        value_text = entry_data.get("value", "N/A")
        success_text = f"S: {entry_data.get('success', 0)}"
        blocked_text = f"B: {entry_data.get('blocked', 0)}"
        average = entry_data.get("average", 0)
        average_text = f"Avg: {average}"

        # --- Theme-Aware Color Calculation ---
        base_color = option.palette.color(QPalette.ColorRole.Base) # Theme's base background
        text_color = option.palette.color(QPalette.ColorRole.Text) # Theme's base text color
        highlight_color = option.palette.color(QPalette.ColorRole.Highlight) # Theme's selection color
        highlighted_text_color = option.palette.color(QPalette.ColorRole.HighlightedText) # Theme's selected text color
        separator_color = option.palette.color(QPalette.ColorRole.Mid) # Theme's separator color
        is_dark_theme = base_color.lightness() < 128 # Simple check for dark theme

        # --- Define Theme-Specific Tints ---
        # <<< INCREASE THIS VALUE FOR STRONGER SHADING >>>
        blend_alpha = 110 # How strong the tint is (0-255). Was 60. Try 100-130?
        # <<< END CHANGE >>>

        if is_dark_theme:
            positive_tint = QColor(80, 150, 80, blend_alpha) # Darker, less saturated green for dark themes
            negative_tint = QColor(150, 80, 80, blend_alpha) # Darker, less saturated red for dark themes
        else: # Light Theme
            positive_tint = QColor(180, 230, 180, blend_alpha) # Lighter green for light themes
            negative_tint = QColor(240, 180, 180, blend_alpha) # Lighter red for light themes
        # --- End Theme-Specific Tints ---

        # --- Determine Background Color based on Score State ---
        # (Logic remains the same, drawing handled below)
        # --- End Background Calculation ---


        # --- Selection Handling & Background Drawing ---
        if option.state & QStyle.StateFlag.State_Selected:
            # Draw selection background FIRST
            painter.fillRect(option.rect, highlight_color)
            # Set pen color for selected text
            current_pen_color = highlighted_text_color
        else:
            # Draw the calculated background color if not selected
            painter.fillRect(option.rect, base_color) # Draw base first
            # Overlay tint if needed
            if score_state == "positive":
                painter.fillRect(option.rect, positive_tint)
            elif score_state == "negative":
                painter.fillRect(option.rect, negative_tint)

            # Set pen color for normal text
            current_pen_color = text_color
        # --- End Selection Handling ---


        # --- Geometry Calculations (same as before) ---
        content_rect = option.rect.adjusted(self.PADDING_HORIZONTAL, self.PADDING_VERTICAL, -self.PADDING_HORIZONTAL, -self.PADDING_VERTICAL)
        fm = painter.fontMetrics()
        # Ensure minimum width accommodates text and potential sort indicator
        score_min_width = fm.horizontalAdvance("Avg: -999") + fm.horizontalAdvance(" ▼") + 5 # Increased estimate
        avg_width = max(fm.horizontalAdvance(average_text) + 5, score_min_width)
        blocked_width = max(fm.horizontalAdvance(blocked_text) + 5, score_min_width // 2)
        success_width = max(fm.horizontalAdvance(success_text) + 5, score_min_width // 2)

        avg_rect = QRect(content_rect.right() - avg_width, content_rect.top(), avg_width, content_rect.height())
        blocked_rect = QRect(avg_rect.left() - blocked_width - self.SPACING, content_rect.top(), blocked_width, content_rect.height())
        success_rect = QRect(blocked_rect.left() - success_width - self.SPACING, content_rect.top(), success_width, content_rect.height())
        value_rect = QRect(content_rect.left(), content_rect.top(), success_rect.left() - content_rect.left() - self.SPACING, content_rect.height())


        # --- Draw Separators ---
        pen = QPen(separator_color, 0) # 0 width cosmetic pen
        painter.setPen(pen)
        painter.drawLine(success_rect.left() - self.SPACING // 2, content_rect.top()+2, success_rect.left() - self.SPACING // 2, content_rect.bottom()-2)
        painter.drawLine(blocked_rect.left() - self.SPACING // 2, content_rect.top()+2, blocked_rect.left() - self.SPACING // 2, content_rect.bottom()-2)
        painter.drawLine(avg_rect.left() - self.SPACING // 2, content_rect.top()+2, avg_rect.left() - self.SPACING // 2, content_rect.bottom()-2)

        # --- Text Painting ---
        # Set the determined pen color (selected or normal/adjusted)
        painter.setPen(current_pen_color)

        # Adjust paint rectangles slightly for better centering/padding
        value_paint_rect = value_rect.adjusted(0, 0, -self.SPACING // 2, 0)
        success_paint_rect = success_rect.adjusted(self.SPACING // 2, 0, -self.SPACING // 2, 0)
        blocked_paint_rect = blocked_rect.adjusted(self.SPACING // 2, 0, -self.SPACING // 2, 0)
        avg_paint_rect = avg_rect.adjusted(self.SPACING // 2, 0, 0, 0)


        # Elide text if too long for the value area
        elided_value_text = fm.elidedText(value_text, Qt.TextElideMode.ElideRight, value_paint_rect.width())
        painter.drawText(value_paint_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine, elided_value_text)

        # Draw scores aligned right within their columns
        painter.drawText(success_paint_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, success_text)
        painter.drawText(blocked_paint_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, blocked_text)

        # Draw Average, bold and right aligned
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(avg_paint_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, average_text)

        painter.restore()


    def sizeHint(self, option: 'QStyleOptionViewItem', index) -> QSize:
        # Provide a fixed size hint for simplicity and consistency
        # Use font metrics to get a reasonable base height
        fm = option.fontMetrics if hasattr(option, 'fontMetrics') else QFontMetrics(QApplication.font())
        # Suggest a height equivalent to about 1.5 lines of text + padding
        height = int(fm.height() * 1.5) + 2 * self.PADDING_VERTICAL
        # Width is determined by the view, provide a sensible default height
        return QSize(200, height)


   
class WildcardManagerDialog(QDialog):
    """Dialog for viewing, editing, adding, deleting wildcard files and their values."""

    def __init__(self, wildcard_resolver: WildcardResolver, parent=None):
        super().__init__(parent)
        self.wildcard_resolver = wildcard_resolver
        self._wildcards_dir = constants.WILDCARDS_DIR # Get dir from constants
        self._selected_file_path: Optional[Path] = None
        self._current_file_data: List[Dict[str, Any]] = [] # Holds data for the selected file
        self._content_modified = False # Flag to track unsaved changes

        self._sort_column = "average" # Initial sort column ('value', 'success', 'blocked', 'average')
        self._sort_order = Qt.SortOrder.DescendingOrder

        self.setWindowTitle("Wildcard Manager")
        self.setMinimumSize(800, 600) # Increased size

        self._setup_ui() # The list widget is created inside here

        # --- Add these lines AFTER self._setup_ui() ---
        # Set the custom delegate for the entry list
        self.item_delegate = WildcardItemDelegate(self.entry_list_widget)
        self.entry_list_widget.setItemDelegate(self.item_delegate)
        # --- End of added lines ---




        self._connect_signals()
        self._load_wildcard_files() # Initial load
        self._update_action_buttons() # Initial button state

        # Check if WILDCARDS_DIR exists (keep this check)
        if not self._wildcards_dir.is_dir():
            log_error(f"Wildcards directory not found or is not a directory: {self._wildcards_dir}")
            show_error_message(self, "Directory Not Found",
                               f"The wildcards directory ('{self._wildcards_dir}') does not exist.\nPlease create it to manage wildcards.")
            # Disable UI elements if directory is missing
            self.wildcard_list_widget.setEnabled(False)
            self.add_file_button.setEnabled(False) # Disable add file too
            self._set_details_enabled(False)
            self.delete_file_button.setEnabled(False)

    def _setup_ui(self):
        self.setObjectName("wildcardManagerDialog")
        main_layout = QVBoxLayout(self)
        main_layout.setObjectName("mainLayoutWildcardManager")

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("splitterWildcardManager")

        # --- Left Pane: List of Wildcard Files ---
        left_pane = QWidget()
        left_pane.setObjectName("leftPaneWildcardManager")
        left_layout = QVBoxLayout(left_pane)
        left_layout.setObjectName("leftLayoutWildcardManager")

        left_layout.addWidget(QLabel("Wildcard Files (.json):"))
        self.wildcard_list_widget = QListWidget()
        self.wildcard_list_widget.setObjectName("wildcardListWidget")
        self.wildcard_list_widget.setToolTip("Select a wildcard file to view/edit.")
        left_layout.addWidget(self.wildcard_list_widget)

        # File Action Buttons
        file_button_layout = QHBoxLayout()
        file_button_layout.setObjectName("fileButtonLayoutWildcardManager")
        self.add_file_button = QPushButton(get_themed_icon("add.png"), " Add File")
        self.add_file_button.setObjectName("addFileButtonWildcardManager")
        self.add_file_button.setToolTip("Create a new empty wildcard file.")
        self.delete_file_button = QPushButton(get_themed_icon("delete.png"), " Delete File")
        self.delete_file_button.setObjectName("deleteFileButtonWildcardManager")
        self.delete_file_button.setToolTip("Delete the selected wildcard file.")
        file_button_layout.addWidget(self.add_file_button)
        file_button_layout.addWidget(self.delete_file_button)
        left_layout.addLayout(file_button_layout)

        splitter.addWidget(left_pane)

        # --- Right Pane: File Content Editor ---
        right_pane = QWidget()
        right_pane.setObjectName("rightPaneWildcardManager")
        right_layout = QVBoxLayout(right_pane)
        right_layout.setObjectName("rightLayoutWildcardManager")

        self.selected_file_label = QLabel("Select a file to view entries")
        self.selected_file_label.setObjectName("selectedFileLabelWildcardManager")
        right_layout.addWidget(self.selected_file_label)


        # --- Add Sortable Header ---
        header_frame = QFrame()
        header_frame.setObjectName("entryListHeaderFrame")
        header_frame.setFrameShape(QFrame.Shape.StyledPanel) # Optional styling
        header_frame.setFrameShadow(QFrame.Shadow.Sunken)    # Optional styling
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(8, 3, 8, 3) # Keep adjusted margins
        header_layout.setSpacing(8)                  # Keep adjusted spacing

        self.header_value_label = QLabel("<b>Wildcard Value</b>")
        self.header_value_label.setObjectName("headerValueLabel")
        self.header_value_label.setToolTip("Click to sort by Value")
        self.header_value_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.header_value_label.mousePressEvent = partial(self._handle_sort_request, "value") # Use partial

        # Calculate a more robust minimum width for score labels
        fm = self.fontMetrics()
        # Calculate based on "Avg: -99" and add padding for indicator + spacing
        score_min_width = fm.horizontalAdvance("Avg: -99") + fm.horizontalAdvance("▼") + 15 # Generous padding

        self.header_success_label = QLabel("<b>S</b>")
        self.header_success_label.setObjectName("headerSuccessLabel")
        self.header_success_label.setToolTip("Success Count (Click to sort)")
        # Give S/B a slightly larger minimum width than before
        self.header_success_label.setMinimumWidth(score_min_width // 2 + 5)
        # --- Align S/B Right ---
        self.header_success_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.header_success_label.mousePressEvent = partial(self._handle_sort_request, "success")

        self.header_blocked_label = QLabel("<b>B</b>")
        self.header_blocked_label.setObjectName("headerBlockedLabel")
        self.header_blocked_label.setToolTip("Blocked Count (Click to sort)")
        self.header_blocked_label.setMinimumWidth(score_min_width // 2 + 5)
        # --- Align S/B Right ---
        self.header_blocked_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.header_blocked_label.mousePressEvent = partial(self._handle_sort_request, "blocked")

        self.header_average_label = QLabel("<b>Avg</b>")
        self.header_average_label.setObjectName("headerAverageLabel")
        self.header_average_label.setToolTip("Average Score (Click to sort)")
        self.header_average_label.setMinimumWidth(score_min_width) # Use calculated min width
        self.header_average_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter) # Keep right aligned
        self.header_average_label.mousePressEvent = partial(self._handle_sort_request, "average")

        # Function to create a vertical separator
        def create_separator():
            separator = QFrame()
            separator.setFrameShape(QFrame.Shape.VLine)
            separator.setFrameShadow(QFrame.Shadow.Sunken)
            return separator

        # Add labels and separators to the layout
        header_layout.addWidget(self.header_value_label, 1) # Value takes most space
        header_layout.addWidget(create_separator())
        header_layout.addWidget(self.header_success_label)
        # header_layout.addWidget(create_separator()) # Optional separator between S/B
        header_layout.addWidget(self.header_blocked_label)
        header_layout.addWidget(create_separator())
        header_layout.addWidget(self.header_average_label)

        right_layout.addWidget(header_frame) # Add header frame BEFORE the list widget
        # --- End Add Sortable Header ---

        # Entry List
        self.entry_list_widget = QListWidget()
        self.entry_list_widget.setObjectName("entryListWidgetWildcardManager")
        self.entry_list_widget.setToolTip("Wildcard values and their scores. Select entries to delete.")
        # Allow selecting multiple entries for deletion
        self.entry_list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        # self.entry_list_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus) # Allow focus for selection
        right_layout.addWidget(self.entry_list_widget, 1) # Give list stretch

        # Value Action Buttons
        value_button_layout = QHBoxLayout()
        value_button_layout.setObjectName("valueButtonLayoutWildcardManager")
        self.add_value_button = QPushButton(get_themed_icon("add.png"), " Add Value")
        self.add_value_button.setObjectName("addValueButtonWildcardManager")
        self.add_value_button.setToolTip("Add a new value entry to the current file.")
        self.delete_value_button = QPushButton(get_themed_icon("delete.png"), " Delete Selected Value(s)")
        self.delete_value_button.setObjectName("deleteValueButtonWildcardManager")
        self.delete_value_button.setToolTip("Delete the selected value entries from the current file.")
        self.import_values_button = QPushButton(get_themed_icon("import.png"), " Import Values from TXT...")
        self.import_values_button.setObjectName("importValuesButtonWildcardManager")
        self.import_values_button.setToolTip("Import values (one per line) from a text file into the current wildcard file.")

        value_button_layout.addWidget(self.add_value_button)
        value_button_layout.addWidget(self.delete_value_button)
        value_button_layout.addStretch(1)
        value_button_layout.addWidget(self.import_values_button)
        right_layout.addLayout(value_button_layout)

        # Save Button
        save_layout = QHBoxLayout()
        save_layout.setObjectName("saveLayoutWildcardManager")
        save_layout.addStretch(1)
        self.save_button = QPushButton(get_themed_icon("save.png"), " Save Changes")
        self.save_button.setObjectName("saveButtonWildcardManager")
        self.save_button.setToolTip("Save changes made to the current file.")
        self.save_button.setEnabled(False) # Disabled initially
        save_layout.addWidget(self.save_button)
        right_layout.addLayout(save_layout)

        splitter.addWidget(right_pane)

        splitter.setSizes([250, 550]) # Adjust initial size ratio

        main_layout.addWidget(splitter)

        # --- Dialog Buttons ---
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.button_box.setObjectName("buttonBoxWildcardManager")
        close_button = self.button_box.button(QDialogButtonBox.StandardButton.Close)
        if close_button: close_button.setObjectName("closeButtonWildcardManager")
        main_layout.addWidget(self.button_box)

        # Initial state
        self._set_details_enabled(False)


    def _set_details_enabled(self, enabled: bool):
        """Enable/disable the right pane controls."""
        self.entry_list_widget.setEnabled(enabled)
        # Buttons enabled state depends on more factors, handled in _update_action_buttons
        if not enabled:
            self.selected_file_label.setText("Select a file")
            self.entry_list_widget.clear()
            self._current_file_data = []
            self._content_modified = False
        # Update button states whenever details are enabled/disabled
        self._update_action_buttons()

    def _update_action_buttons(self):
        """Updates the enabled state of all action buttons based on current state."""
        file_selected = self._selected_file_path is not None
        entries_selected = bool(self.entry_list_widget.selectedItems())

        # File actions
        # Can always add if dir exists
        self.add_file_button.setEnabled(self._wildcards_dir.is_dir())
        self.delete_file_button.setEnabled(file_selected)

        # Value actions
        self.add_value_button.setEnabled(file_selected)
        self.delete_value_button.setEnabled(file_selected and entries_selected)
        self.import_values_button.setEnabled(file_selected)

        # Save action
        self.save_button.setEnabled(file_selected and self._content_modified)

    def _connect_signals(self):
        self.wildcard_list_widget.currentItemChanged.connect(self._on_file_selection_changed)
        self.entry_list_widget.itemSelectionChanged.connect(self._update_action_buttons) # Update delete button state on selection change
        self.save_button.clicked.connect(self._save_changes)
        self.button_box.rejected.connect(self.reject) # Close button

        # Connect new buttons
        self.add_file_button.clicked.connect(self._add_file)
        self.delete_file_button.clicked.connect(self._delete_file)
        self.add_value_button.clicked.connect(self._add_value)
        self.delete_value_button.clicked.connect(self._delete_values)
        self.import_values_button.clicked.connect(self._import_values)
        self.entry_list_widget.itemDoubleClicked.connect(self._handle_double_click)

    def _load_wildcard_files(self):
        """Scans the wildcard directory and populates the file list."""
        current_selection = self.wildcard_list_widget.currentItem()
        current_path = current_selection.data(Qt.ItemDataRole.UserRole) if current_selection else None

        self.wildcard_list_widget.clear()
        # self._selected_file_path = None # Keep path until selection explicitly changes
        # self._set_details_enabled(False) # Don't disable details yet

        if not self._wildcards_dir.is_dir():
            log_warning(f"Cannot load wildcards, directory not found: {self._wildcards_dir}")
            self.wildcard_list_widget.addItem("Wildcard directory missing!")
            self.wildcard_list_widget.setEnabled(False)
            self._set_details_enabled(False)
            return

        found_files = False
        item_to_reselect = None
        for file_path in sorted(self._wildcards_dir.glob("*.json")):
            if file_path.is_file():
                item = QListWidgetItem(file_path.name)
                item.setData(Qt.ItemDataRole.UserRole, file_path)
                self.wildcard_list_widget.addItem(item)
                if file_path == current_path:
                    item_to_reselect = item
                found_files = True

        if not found_files:
            self.wildcard_list_widget.addItem("No .json files found.")
            self.wildcard_list_widget.setEnabled(False)
            self._set_details_enabled(False) # Disable details if no files
        else:
            self.wildcard_list_widget.setEnabled(True)
            # Reselect previously selected item if it still exists
            if item_to_reselect:
                # Use QTimer to ensure selection happens after list is fully populated
                QTimer.singleShot(0, lambda: self.wildcard_list_widget.setCurrentItem(item_to_reselect))
            elif self.wildcard_list_widget.count() > 0:
                # If previous selection gone, select first item
                # QTimer.singleShot(0, lambda: self.wildcard_list_widget.setCurrentRow(0))
                pass # Let selection change handle loading if nothing reselected
            else:
                 # List is empty after filtering? Disable details.
                 self._set_details_enabled(False)


        self._update_action_buttons()

    def _prompt_save_if_modified(self) -> bool:
        """Checks if content is modified and prompts user to save. Returns True if okay to proceed, False if cancelled."""
        if not self._content_modified or not self._selected_file_path:
            return True # No changes or no file selected, okay to proceed

        reply = QMessageBox.question(self, "Unsaved Changes",
                                     f"You have unsaved changes in '{self._selected_file_path.name}'.\nSave changes before continuing?",
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                     QMessageBox.StandardButton.Save)

        if reply == QMessageBox.StandardButton.Save:
            if self._save_changes():
                return True # Save successful, proceed
            else:
                return False # Save failed, cancel operation
        elif reply == QMessageBox.StandardButton.Discard:
            self._content_modified = False # Mark as not modified
            return True # Discarded changes, proceed
        else: # Cancel
            return False # Cancel the operation

    def _on_file_selection_changed(self, current_item: QListWidgetItem, previous_item: QListWidgetItem):
        """Loads the content of the selected wildcard JSON file and displays entries."""
        if not self._prompt_save_if_modified():
            # User cancelled save/discard, revert selection
            self.wildcard_list_widget.blockSignals(True)
            # Check if previous_item exists before setting it
            if previous_item:
                self.wildcard_list_widget.setCurrentItem(previous_item)
            else:
                # If there was no previous item (e.g., first selection), clear selection
                self.wildcard_list_widget.setCurrentRow(-1)
            self.wildcard_list_widget.blockSignals(False)
            return

        # Proceed with loading the new file (or clearing if selection is None)
        self._selected_file_path = None
        self._current_file_data = []
        self.entry_list_widget.clear() # Clear list visually immediately
        self._content_modified = False # Reset modified flag for the new file

        if not current_item:
            self._set_details_enabled(False) # Disables entry list, buttons etc.
            self.selected_file_label.setText("Select a file")
            self._update_action_buttons() # Update button states (will disable most)
            self._update_header_indicators() # Clear sort indicators
            return

        file_path = current_item.data(Qt.ItemDataRole.UserRole)
        if isinstance(file_path, Path) and file_path.is_file() and file_path.suffix.lower() == ".json":
            self._selected_file_path = file_path
            self.selected_file_label.setText(f"Entries in: <b>{file_path.name}</b>")
            self._set_details_enabled(True) # Enable list view etc.

            # Load the JSON data directly from the file
            try:
                with file_path.open('r', encoding='utf-8') as f:
                    # Check if file is empty before loading
                    content = f.read()
                    if not content.strip():
                         data = [] # Treat empty file as empty list
                    else:
                         data = json.loads(content) # Load non-empty content

                if not isinstance(data, list):
                    raise ValueError("JSON root is not a list.")

                # Validate entries and calculate average score
                valid_entries = []
                for entry in data:
                    if isinstance(entry, dict) and "value" in entry:
                        entry.setdefault("success", 0)
                        entry.setdefault("blocked", 0)
                        entry["average"] = entry.get("success", 0) - entry.get("blocked", 0)
                        valid_entries.append(entry)
                    else:
                        log_warning(f"Invalid entry structure in {file_path.name}. Skipping: {entry}")

                self._current_file_data = valid_entries # Store loaded data
                self._sort_column = "average" # Reset to default sort on file change
                self._sort_order = Qt.SortOrder.DescendingOrder
                self._sort_and_repopulate_entries() # Populate UI list sorted
                self._content_modified = False # Just loaded, no modifications yet

            except (json.JSONDecodeError, ValueError, OSError) as e:
                log_error(f"Error loading or parsing wildcard file {file_path.name}: {e}")
                show_error_message(self, "Load Error", f"Failed to load {file_path.name}:\n{e}")
                self._current_file_data = []
                self._sort_and_repopulate_entries() # Clear the list on error (will handle empty list)
                self._set_details_enabled(False) # Disable details pane on error

        else:
            log_warning(f"Invalid item data or file type selected: {file_path}")
            self._current_file_data = [] # Ensure data is cleared
            self._sort_and_repopulate_entries() # Clear list display
            self._set_details_enabled(False)
            self.selected_file_label.setText("Invalid selection")

        self._update_action_buttons() # Update button states after loading or error


    def _populate_entry_list(self):
        """Populates the entry list widget using data roles."""
        self.entry_list_widget.clear()
        if not self._current_file_data:
            self._update_header_indicators() # Clear headers if list is empty
            return

        # Data is assumed to be pre-sorted by _sort_and_repopulate_entries
        for entry_data in self._current_file_data:
            # Create a standard QListWidgetItem
            list_item = QListWidgetItem() # Text set by delegate paint

            # Store the entire entry dictionary in the UserRole for the delegate
            list_item.setData(Qt.ItemDataRole.UserRole, entry_data)

            # --- Determine score state and store in custom data role ---
            average = entry_data.get("average", 0)
            score_state = "neutral"
            if average >= WildcardItemDelegate.POSITIVE_THRESHOLD:
                score_state = "positive"
            elif average <= WildcardItemDelegate.NEGATIVE_THRESHOLD:
                score_state = "negative"

            # Use UserRole + 1 (or another custom role ID) to store the state string
            list_item.setData(Qt.ItemDataRole.UserRole + 1, score_state)
            # --- End storing score state ---

            # Optional: Set a basic tooltip directly on the item
            tooltip_text = f"Value: {entry_data.get('value', 'N/A')}\nSuccess: {entry_data.get('success', 0)}, Blocked: {entry_data.get('blocked', 0)}, Avg: {average}"
            list_item.setToolTip(tooltip_text)

            self.entry_list_widget.addItem(list_item)

        self.entry_list_widget.scrollToTop()
        self._update_header_indicators() # Update arrows after populating
        self._update_action_buttons()


    def _update_header_indicators(self):
        """Updates the header labels to show sort indicators (e.g., ▲/▼)."""
        headers = {
            "value": self.header_value_label,
            "success": self.header_success_label,
            "blocked": self.header_blocked_label,
            "average": self.header_average_label,
        }
        # Reset all headers first
        for name, label in headers.items():
             # Remove existing indicator if present
             text = label.text().replace(" ▲", "").replace(" ▼", "")
             label.setText(f"<b>{text}</b>") # Keep bold

        # Add indicator to the current sort column
        if self._sort_column in headers:
            label = headers[self._sort_column]
            text = label.text().replace("<b>","").replace("</b>","") # Get base text
            indicator = ""
            if self._sort_order == Qt.SortOrder.AscendingOrder:
                indicator = " ▲"
            elif self._sort_order == Qt.SortOrder.DescendingOrder:
                indicator = " ▼"
            # Else: Default order (Average Descending) might not show indicator, or show default indicator
            # For simplicity, we'll show indicator for Asc/Desc only.
            label.setText(f"<b>{text}{indicator}</b>")


    def _handle_sort_request(self, column_name: str, event: QMouseEvent):
        """Handles clicks on the header labels to change sorting."""
        if event.button() != Qt.MouseButton.LeftButton:
            return # Only sort on left click

        log_debug(f"Sort requested for column: {column_name}")

        # Determine new sort order
        if self._sort_column == column_name:
            # Cycle through orders: Desc -> Asc -> Default (Avg Desc)
            if self._sort_order == Qt.SortOrder.DescendingOrder:
                self._sort_order = Qt.SortOrder.AscendingOrder
            elif self._sort_order == Qt.SortOrder.AscendingOrder:
                # Cycle back to default (Average Descending)
                self._sort_column = "average"
                self._sort_order = Qt.SortOrder.DescendingOrder
            # else: # Should not happen if default is set correctly
            #     self._sort_order = Qt.SortOrder.DescendingOrder
        else:
            # New column clicked, start with default descending (or ascending for text?)
            self._sort_column = column_name
            # Default to Ascending for text column, Descending for numeric
            if column_name == "value":
                self._sort_order = Qt.SortOrder.AscendingOrder
            else:
                self._sort_order = Qt.SortOrder.DescendingOrder

        self._sort_and_repopulate_entries()

    def _sort_and_repopulate_entries(self):
        """Sorts _current_file_data based on current state and repopulates the list."""
        log_debug(f"Sorting by column '{self._sort_column}', order: {self._sort_order}")

        reverse_order = (self._sort_order == Qt.SortOrder.DescendingOrder)
        sort_key = self._sort_column

        # Define the key function for sorting
        def get_sort_key(entry):
            val = entry.get(sort_key, 0 if sort_key != "value" else "") # Default for scores/average is 0, default for value is ""
            if sort_key == "value":
                return str(val).lower() # Case-insensitive string sort
            else:
                # Handle potential non-numeric values gracefully for numeric sorts
                try:
                    return int(val)
                except (ValueError, TypeError):
                    return 0 # Treat errors as 0 for sorting

        try:
            # Sort the internal data list in place
            self._current_file_data.sort(key=get_sort_key, reverse=reverse_order)
        except Exception as e:
            log_error(f"Error during sorting: {e}", exc_info=True)
            # Handle error, maybe revert to default sort?
            show_error_message(self, "Sort Error", f"Could not sort data:\n{e}")
            # Revert to default sort?
            # self._sort_column = "average"
            # self._sort_order = Qt.SortOrder.DescendingOrder
            # self._current_file_data.sort(key=lambda x: x.get('average', 0), reverse=True)

        # Repopulate the list widget with the sorted data
        self._populate_entry_list()




    def _save_wildcard_file(self, file_path: Path, data: List[Dict[str, Any]]) -> bool:
        """Saves the data list to the specified JSON file path."""
        try:
            # Ensure parent directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with file_path.open('w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            log_info(f"Successfully saved wildcard file: {file_path.name}")
            return True
        except OSError as e:
            log_error(f"Error writing wildcard file {file_path}: {e}")
            show_error_message(self, "Save Error", f"Could not write to file:\n{file_path}\n{e}")
            return False
        except Exception as e:
            log_error(f"Unexpected error saving wildcard file {file_path}: {e}", exc_info=True)
            show_error_message(self, "Save Error", f"An unexpected error occurred during save:\n{e}")
            return False

    @pyqtSlot()
    def _save_changes(self) -> bool:
        """Saves the current _current_file_data to the selected file."""
        if not self._selected_file_path:
            log_warning("Save changes called but no file selected.")
            return False
        if not self._content_modified:
            log_debug(f"No changes to save for {self._selected_file_path.name}")
            return True # No changes, considered successful

        log_info(f"Saving changes to {self._selected_file_path.name}...")
        if self._save_wildcard_file(self._selected_file_path, self._current_file_data):
            self._content_modified = False
            self._update_action_buttons() # Disable save button
            # Clear the resolver's cache for this specific wildcard file
            wildcard_name = self._selected_file_path.stem
            self.wildcard_resolver.clear_specific_cache(wildcard_name)
            show_info_message(self, "Save Successful", f"Changes saved to\n{self._selected_file_path.name}")
            return True
        else:
            # Error message shown by _save_wildcard_file
            return False

    @pyqtSlot()
    def _add_file(self):
        """Adds a new, empty wildcard JSON file."""
        if not self._prompt_save_if_modified(): return

        new_name, ok = QInputDialog.getText(self, "Add Wildcard File", "Enter name for new wildcard file (no extension):")
        if ok and new_name:
            new_name = new_name.strip().replace(" ", "_") # Basic sanitization
            if not new_name:
                show_error_message(self, "Invalid Name", "Filename cannot be empty.")
                return
            if re.search(r'[<>:"/\\|?*\x00-\x1f]', new_name): # Basic check
                show_error_message(self, "Invalid Name", "Filename contains invalid characters.")
                return

            new_filepath = self._wildcards_dir / f"{new_name}.json"
            if new_filepath.exists():
                show_error_message(self, "File Exists", f"A file named '{new_filepath.name}' already exists.")
                return

            try:
                self._wildcards_dir.mkdir(parents=True, exist_ok=True)
                # Create an empty JSON list file
                if self._save_wildcard_file(new_filepath, []):
                    log_info(f"Created new wildcard file: {new_filepath.name}")
                    self._load_wildcard_files() # Refresh list
                    # Find and select the new item
                    for i in range(self.wildcard_list_widget.count()):
                        item = self.wildcard_list_widget.item(i)
                        if item.data(Qt.ItemDataRole.UserRole) == new_filepath:
                            self.wildcard_list_widget.setCurrentItem(item)
                            break
                # Error message handled by _save_wildcard_file
            except Exception as e:
                log_error(f"Error creating new wildcard file {new_name}: {e}", exc_info=True)
                show_error_message(self, "Create Error", f"Failed to create file:\n{e}")
        elif ok: # User pressed OK but entered no name
             show_error_message(self, "Input Error", "Filename cannot be empty.")

    @pyqtSlot()
    def _delete_file(self):
        """Deletes the selected wildcard file."""
        if not self._selected_file_path: return

        reply = QMessageBox.question(self, "Confirm Delete",
                                     f"Are you sure you want to permanently delete the file\n'{self._selected_file_path.name}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            try:
                self._selected_file_path.unlink()
                log_info(f"Deleted wildcard file: {self._selected_file_path.name}")
                # Clear the resolver cache for the deleted file
                wildcard_name = self._selected_file_path.stem
                self.wildcard_resolver.clear_specific_cache(wildcard_name)
                # Clear selection and details
                self._selected_file_path = None
                self._current_file_data = []
                self._content_modified = False
                self._set_details_enabled(False)
                self._load_wildcard_files() # Refresh file list
            except OSError as e:
                log_error(f"Error deleting file {self._selected_file_path.name}: {e}")
                show_error_message(self, "Delete Error", f"Could not delete file:\n{e}")
            except Exception as e:
                log_error(f"Unexpected error deleting file {self._selected_file_path.name}: {e}", exc_info=True)
                show_error_message(self, "Delete Error", f"An unexpected error occurred:\n{e}")

    @pyqtSlot()
    def _add_value(self):
        """Adds a new value entry to the current file's data."""
        if not self._selected_file_path: return

        new_value, ok = QInputDialog.getText(self, "Add Wildcard Value", "Enter the new wildcard value:", QLineEdit.EchoMode.Normal)
        if ok and new_value:
            # Check for duplicates
            existing_values = {entry.get("value", "").lower() for entry in self._current_file_data}
            if new_value.strip().lower() in existing_values:
                show_info_message(self, "Duplicate Value", f"The value '{new_value}' already exists in this file.")
                return

            # Add new entry to the in-memory list
            new_entry = {"value": new_value.strip(), "success": 0, "blocked": 0, "average": 0}
            self._current_file_data.append(new_entry)
            self._populate_entry_list() # Refresh UI list
            self._content_modified = True
            self._update_action_buttons()
            log_info(f"Added new value '{new_value}' to memory for {self._selected_file_path.name}.")
            # Scroll to the bottom to see the new entry? Maybe sort instead.
            # self.entry_list_widget.scrollToBottom()
        elif ok:
            show_error_message(self, "Input Error", "Wildcard value cannot be empty.")


    @pyqtSlot()
    def _delete_values(self):
        """Deletes selected value entries from the current file's data."""
        if not self._selected_file_path: return
        selected_items = self.entry_list_widget.selectedItems()
        if not selected_items: return

        reply = QMessageBox.question(self, "Confirm Delete",
                                     f"Are you sure you want to delete {len(selected_items)} selected value(s)?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            values_to_delete = set()
            indices_to_delete = [] # Store indices for removal from list widget

            # First pass: Get data from selected items
            for item in selected_items:
                item_data = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(item_data, dict) and "value" in item_data:
                    values_to_delete.add(item_data["value"])
                # Get row index for later removal
                indices_to_delete.append(self.entry_list_widget.row(item))


            if not values_to_delete: return # Should not happen

            original_count = len(self._current_file_data)
            # Filter out the entries to delete from the internal data list
            self._current_file_data = [
                entry for entry in self._current_file_data
                if entry.get("value") not in values_to_delete
            ]
            deleted_count = original_count - len(self._current_file_data)

            if deleted_count > 0:
                # Remove items from the QListWidget itself (efficiently)
                # Sort indices in reverse order to avoid shifting issues during removal
                indices_to_delete.sort(reverse=True)
                self.entry_list_widget.blockSignals(True) # Block signals during batch removal
                for index in indices_to_delete:
                    self.entry_list_widget.takeItem(index)
                self.entry_list_widget.blockSignals(False)

                # No need to call _populate_entry_list again, just update state
                self._content_modified = True
                self._update_action_buttons()
                log_info(f"Deleted {deleted_count} value(s) from memory for {self._selected_file_path.name}.")
            else:
                log_warning("Delete value requested, but no matching entries found in internal data.")


    @pyqtSlot(QListWidgetItem)
    def _handle_double_click(self, item: QListWidgetItem):
        """Handles double-clicking on a wildcard entry to edit its value."""
        if not self._selected_file_path: return
        if not item: return

        original_data = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(original_data, dict): return

        original_value = original_data.get("value", "")

        new_value, ok = QInputDialog.getText(self, "Edit Wildcard Value", "Enter the new value:",
                                             QLineEdit.EchoMode.Normal, original_value)

        if ok and new_value:
            new_value = new_value.strip()
            if not new_value:
                show_error_message(self, "Input Error", "Value cannot be empty.")
                return
            if new_value == original_value:
                return # No change

            # Check for duplicates (excluding the original value itself)
            existing_values = {
                entry.get("value", "").lower()
                for entry in self._current_file_data
                if entry.get("value", "") != original_value # Exclude self
            }
            if new_value.lower() in existing_values:
                show_info_message(self, "Duplicate Value", f"The value '{new_value}' already exists in this file.")
                return

            # Find the index in the underlying data list
            found_index = -1
            for i, entry in enumerate(self._current_file_data):
                if entry.get("value") == original_value:
                    found_index = i
                    break

            if found_index != -1:
                log_info(f"Updating value '{original_value}' to '{new_value}' and resetting scores.")
                # Update the entry in the internal data list
                self._current_file_data[found_index]["value"] = new_value
                self._current_file_data[found_index]["success"] = 0
                self._current_file_data[found_index]["blocked"] = 0
                self._current_file_data[found_index]["average"] = 0

                # Update the data stored in the QListWidgetItem
                updated_data = self._current_file_data[found_index].copy() # Get updated dict
                item.setData(Qt.ItemDataRole.UserRole, updated_data)

                # Determine new score state and update data role for QSS/delegate
                score_state = "neutral" # Resetting scores means neutral state
                item.setData(Qt.ItemDataRole.UserRole + 1, score_state)

                # Mark as modified
                self._content_modified = True
                self._update_action_buttons()

                # Trigger a repaint of the specific item
                list_view_index = self.entry_list_widget.indexFromItem(item)
                if list_view_index.isValid():
                    self.entry_list_widget.update(list_view_index)

                # Optional: Re-sort if the value change affects sort order
                if self._sort_column == "value":
                    self._sort_and_repopulate_entries()

            else:
                log_error(f"Could not find internal data for original value '{original_value}' during edit.")

        elif ok: # User pressed OK but entered empty string
            show_error_message(self, "Input Error", "Value cannot be empty.")

    @pyqtSlot()
    def _import_values(self):
        """Imports values from a text file into the current wildcard file, checking for duplicates within the import file."""
        if not self._selected_file_path:
            show_error_message(self, "Import Error", "Please select a wildcard file first.")
            return

        file_filter = "Text Files (*.txt);;All Files (*)"
        filepath_tuple = QFileDialog.getOpenFileName(self, f"Import Values into {self._selected_file_path.name}", str(self._wildcards_dir), file_filter)
        import_filepath_str = filepath_tuple[0]

        if not import_filepath_str:
            return # User cancelled

        import_filepath = Path(import_filepath_str)
        added_count = 0
        skipped_existing_count = 0
        skipped_import_dup_count = 0 # New counter for duplicates within the file
        failed_count = 0
        # Check against values already in the file
        existing_values_in_file = {entry.get("value", "").lower() for entry in self._current_file_data}
        # Track values encountered *during this import*
        values_in_this_import = set()
        new_entries_to_add = [] # Collect new entries before modifying _current_file_data

        try:
            with import_filepath.open('r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    value = line.strip()
                    if not value: continue # Skip empty lines

                    value_lower = value.lower() # Use lowercase for checks

                    # Check 1: Duplicate within the import file itself?
                    if value_lower in values_in_this_import:
                        log_debug(f"Skipping duplicate value from import file (Line {line_num}): '{value}'")
                        skipped_import_dup_count += 1
                        continue

                    # Check 2: Duplicate already exists in the target wildcard file?
                    if value_lower in existing_values_in_file:
                        log_debug(f"Skipping value already present in '{self._selected_file_path.name}': '{value}'")
                        skipped_existing_count += 1
                        continue

                    # If not a duplicate, prepare new entry and track it
                    new_entry = {"value": value, "success": 0, "blocked": 0, "average": 0}
                    new_entries_to_add.append(new_entry)
                    values_in_this_import.add(value_lower) # Track added value
                    added_count += 1

            if added_count > 0:
                # Add collected new entries to the main data list
                self._current_file_data.extend(new_entries_to_add)
                # Repopulate the list widget (will also sort based on current settings)
                self._sort_and_repopulate_entries()
                self._content_modified = True
                self._update_action_buttons()
                log_info(f"Imported {added_count} new values into memory for {self._selected_file_path.name}.")

            # Always show summary message
            summary_message = (
                f"Imported from {import_filepath.name}:\n\n"
                f"- Added: {added_count}\n"
                f"- Skipped (Already in File): {skipped_existing_count}\n"
                f"- Skipped (Duplicate in Import): {skipped_import_dup_count}\n"
                f"- Failed (Read Errors): {failed_count}"
            )
            show_info_message(self, "Import Complete", summary_message)


        except OSError as e:
            log_error(f"Error reading import file {import_filepath.name}: {e}")
            show_error_message(self, "Import Error", f"Could not read file:\n{import_filepath.name}\n{e}")
            failed_count = -1 # Indicate general read failure
        except Exception as e:
            log_error(f"Unexpected error importing from {import_filepath.name}: {e}", exc_info=True)
            show_error_message(self, "Import Error", f"An unexpected error occurred:\n{e}")
            failed_count = -1








    # --- Close / Reject Logic ---
    def reject(self):
        """Handles closing the dialog, checking for unsaved changes."""
        if not self._prompt_save_if_modified():
            return # User cancelled closing

        log_debug("Wildcard Manager closed.")
        super().reject()

    def closeEvent(self, event):
        """Ensure reject logic is called when closing via window [X]."""
        self.reject()
        if not self.isVisible(): # Check if reject() successfully closed the dialog
            event.accept()
        else:
            event.ignore() # Reject was cancelled
