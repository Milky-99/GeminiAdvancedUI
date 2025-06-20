# -*- coding: utf-8 -*-
import json
import os
from pathlib import Path
from typing import Any, Optional, Dict, List, Tuple

from PyQt6.QtWidgets import QMessageBox, QApplication, QDialog, QDialogButtonBox, QTextEdit, QVBoxLayout
from PyQt6.QtGui import QColor, QPalette, QIcon
from PyQt6.QtCore import Qt
from .constants import THEMES_DIR
# Make sure logger is imported here if you use it in the message functions
from .logger import log_error, log_warning, log_info, log_debug
from .constants import APP_DIR


ICON_BASE_DIR = APP_DIR / "icons"
_icon_dir_warning_logged = False


def load_json_file(file_path: Path, default: Any = None) -> Any:
    """Safely loads data from a JSON file. Handles non-existent and empty files."""
    if not file_path.exists():
        log_warning(f"JSON file not found: {file_path}. Returning default.")
        # If the file doesn't exist, try to create it with the default content
        if default is not None:
            log_info(f"Attempting to create default JSON file at: {file_path}")
            if not save_json_file(file_path, default):
                log_error(f"Failed to create default JSON file: {file_path}")
        return default

    try:
        # Check if the file is empty before trying to load
        if file_path.stat().st_size == 0:
            log_warning(f"JSON file is empty: {file_path}. Returning default.")
            # Optionally, write the default value to the empty file to fix it
            if default is not None:
                log_info(f"Attempting to write default content to empty JSON file: {file_path}")
                if not save_json_file(file_path, default):
                     log_error(f"Failed to write default content to empty JSON file: {file_path}")
            return default

        with file_path.open('r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError) as e:
        log_error(f"Error decoding JSON file {file_path}: {e}. File might be corrupted.")
        # Attempt recovery by overwriting with default? Risky without backup.
        # For now, just return default.
        log_warning(f"Returning default value due to JSON decode error for {file_path}.")
        if default is not None:
             log_info(f"Attempting to overwrite corrupted JSON file with default: {file_path}")
             if not save_json_file(file_path, default):
                 log_error(f"Failed to overwrite corrupted JSON file: {file_path}")
        return default
    except OSError as e:
         log_error(f"OS error loading JSON file {file_path}: {e}")
         return default
    except Exception as e: # Catch other potential errors like permission issues during stat()
        log_error(f"Unexpected error accessing or loading JSON file {file_path}: {e}", exc_info=True)
        return default


def save_json_file(file_path: Path, data: Any) -> bool:
    """Safely saves data to a JSON file."""
    try:
        # Ensure parent directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with file_path.open('w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        # log_debug(f"JSON data saved successfully to {file_path}") # Optional: debug log on success
        return True
    except (TypeError, OSError) as e:
        log_error(f"Error saving JSON file {file_path}: {e}")
        return False
    except Exception as e:
        log_error(f"Unexpected error saving JSON file {file_path}: {e}", exc_info=True)
        return False

def show_error_message(parent=None, title="Error", message="An unexpected error occurred."):
    """Displays a standardized error message box."""
    log_error(f"Displaying Error: {title} - {message}") # Use log_error
    msg_box = QMessageBox(parent)
    msg_box.setIcon(QMessageBox.Icon.Critical)
    msg_box.setWindowTitle(title)
    msg_box.setText(message)
    msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
    msg_box.exec()

def show_info_message(parent=None, title="Information", message="Operation successful."):
    """Displays a standardized information message box."""
    log_info(f"Displaying Info: {title} - {message}") # Use log_info
    msg_box = QMessageBox(parent)
    msg_box.setIcon(QMessageBox.Icon.Information)
    msg_box.setWindowTitle(title)
    msg_box.setText(message)
    msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
    msg_box.exec()


def show_warning_message(parent=None, title="Warning", message="Something needs attention."):
    """Displays a standardized warning message box."""
    log_warning(f"Displaying Warning: {title} - {message}") # Use log_warning
    msg_box = QMessageBox(parent)
    msg_box.setIcon(QMessageBox.Icon.Warning) # Use Warning Icon
    msg_box.setWindowTitle(title)
    msg_box.setText(message)
    msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
    msg_box.exec()


def discover_custom_themes() -> List[Tuple[str, Path]]:
    """
    Scans the THEMES_DIR for .qss files.

    Returns:
        A list of tuples: (theme_name, theme_path)
        Returns empty list if directory doesn't exist or no themes found.
    """
    custom_themes = []
    if not THEMES_DIR.is_dir():
        log_warning(f"Custom themes directory not found: {THEMES_DIR}")
        return []

    log_debug(f"Scanning for custom themes in: {THEMES_DIR}")
    for file_path in sorted(THEMES_DIR.glob("*.qss")):
        if file_path.is_file():
            theme_name = file_path.stem # Get filename without extension
            custom_themes.append((theme_name, file_path))
            log_debug(f"Found custom theme: '{theme_name}' at {file_path}")

    return custom_themes



def apply_theme(app: QApplication, theme_name: str):
    """
    Applies a Light, Dark, Auto, or custom QSS theme to the application.

    Args:
        app: The QApplication instance.
        theme_name: The name of the theme ("Auto", "Light", "Dark", or the
                    filename stem of a custom .qss file in THEMES_DIR).
    """
    log_info(f"Attempting to apply theme: {theme_name}")
    app.setStyle("Fusion") # Ensure Fusion base style for consistency

    # --- Base Styles Applied to ALL Themes ---
    base_style = """
        /* Default selected item border for PromptEntryWidget */
        PromptEntryWidget[selected="true"] {
            border: 2px solid palette(highlight);
        }
        /* Basic Tooltip Style (can be overridden by themes) */
        QToolTip {
            border: 1px solid palette(midlight); /* Use midlight for border */
            padding: 3px;
            background-color: palette(tool-tip-base); /* Use theme tooltip base */
            color: palette(tool-tip-text); /* Use theme tooltip text */
            border-radius: 3px; /* Slightly rounded corners */
        }
        /* Basic Scrollbar Styling (can be overridden by themes) */
        QScrollBar:vertical {
            border: none;
            background: palette(window); /* Match window background */
            width: 16px; /* Slightly narrower */
            margin: 0px;
        }
        QScrollBar::handle:vertical {
            background: palette(mid); /* Use mid color */
            min-height: 25px;
            border-radius: 8px; /* More rounded */
            border: 1px solid palette(midlight); /* Subtle border */
        }
        QScrollBar::handle:vertical:hover {
             background: palette(highlight); /* Highlight on hover */
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px; /* Remove arrows */
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: none; /* No background for page areas */
        }
        QComboBox:disabled {
             color: palette(disabled, text); /* Use standard disabled text color */
        }
         QComboBox QAbstractItemView {
             color: palette(text);
             background-color: palette(base);
             selection-background-color: palette(highlight);
             selection-color: palette(highlighted-text); /* Ensure text is readable when selected */
        }

        /* --- REMOVED Wildcard Manager Score State Styling --- */
        /* The delegate now handles background coloring directly */

    """
    # Initialize style_sheet with the base styles
    style_sheet = base_style

    # --- Check for Built-in Themes First ---
    if theme_name in ["Dark", "Light", "Auto"]:
        palette = QPalette()

        if theme_name == "Dark":
            # Define Dark Palette
            palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
            palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
            palette.setColor(QPalette.ColorRole.Base, QColor(42, 42, 42))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor(48, 48, 48)) # Slightly lighter alt base
            palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(53, 53, 53)) # Match window for consistency
            palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)
            palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
            palette.setColor(QPalette.ColorRole.Button, QColor(65, 65, 65)) # Slightly lighter buttons
            palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
            palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
            palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
            palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
            palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white) # White text on blue highlight
            palette.setColor(QPalette.ColorRole.Midlight, QColor(75, 75, 75)) # For borders/separators
            palette.setColor(QPalette.ColorRole.Mid, QColor(90, 90, 90)) # Scrollbar handle

            # Disabled colors for dark mode
            disabled_text_color = QColor(127, 127, 127)
            disabled_highlight_color = QColor(80, 80, 80)
            palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled_text_color)
            palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled_text_color)
            palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled_text_color)
            palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, disabled_highlight_color)
            palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.HighlightedText, disabled_text_color)

            # Placeholder text color for dark mode
            palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(160, 160, 160))

            app.setPalette(palette)

            # Dark theme specific stylesheets (append to base_style)
            # REMOVED score state rules here

        elif theme_name == "Light":
            # Use default Fusion light palette (reset)
            app.setPalette(QApplication.style().standardPalette())
            # No specific additions needed here

        else: # Auto (Default)
            # Use default Fusion palette (tries to match system)
            app.setPalette(QApplication.style().standardPalette())
            # No specific additions needed here

        # Apply the combined stylesheet for built-in themes
        app.setStyleSheet(style_sheet)
        log_info(f"Applied built-in theme: {theme_name}")

    # --- Handle Custom Themes ---
    else:
        theme_path = THEMES_DIR / f"{theme_name}.qss"
        if theme_path.is_file():
            try:
                log_info(f"Loading custom theme from: {theme_path}")
                custom_qss_content = theme_path.read_text(encoding='utf-8')

                # Reset palette to default before applying QSS for a clean slate.
                app.setPalette(QApplication.style().standardPalette())

                # Prepend base style to custom content
                # The base style no longer contains the score rules
                final_style_sheet = base_style + "\n\n/* --- Custom Theme Start --- */\n" + custom_qss_content

                # Apply the combined stylesheet
                app.setStyleSheet(final_style_sheet)
                log_info(f"Successfully applied custom theme: {theme_name}")

            except OSError as e:
                log_error(f"Error reading custom theme file {theme_path}: {e}")
                show_error_message(title="Theme Error", message=f"Could not read theme file:\n{theme_path}\n\n{e}")
                log_warning("Falling back to 'Auto' theme.")
                apply_theme(app, "Auto") # Recursive call for fallback
            except Exception as e:
                log_error(f"Unexpected error applying custom theme {theme_name}: {e}", exc_info=True)
                show_error_message(title="Theme Error", message=f"Failed to apply theme '{theme_name}':\n{e}")
                log_warning("Falling back to 'Auto' theme.")
                apply_theme(app, "Auto") # Recursive call for fallback
        else:
            log_warning(f"Custom theme file not found: {theme_path}. Applying 'Auto' theme.")
            show_warning_message(title="Theme Not Found", message=f"Could not find theme file:\n'{theme_path.name}'\nin {THEMES_DIR}\n\nFalling back to 'Auto' theme.")
            apply_theme(app, "Auto") # Recursive call for fallback




      
            
def get_themed_icon(icon_name: str) -> QIcon:
    """
    Loads an icon, attempting to find a theme-specific version first.
    Falls back to 'default' folder.
    Returns an empty QIcon() if the icon file (or base directory) is not found,
    allowing the application to run without icons.
    """
    global _icon_dir_warning_logged
    app = QApplication.instance()
    if not app:
        # Only log error if app instance is missing, that's unexpected
        log_error("QApplication instance not found in get_themed_icon.")
        return QIcon() # Return empty

    # --- Check Base Icon Directory ---
    if not ICON_BASE_DIR.is_dir():
        if not _icon_dir_warning_logged:
            log_warning(f"Icon base directory not found: {ICON_BASE_DIR}. Icons will not be loaded.")
            _icon_dir_warning_logged = True
        return QIcon() # Return empty if base dir is missing

    # --- Theme Detection (Simple) ---
    try:
        window_color = app.palette().color(QPalette.ColorRole.Window)
        is_dark_theme = window_color.lightness() < 128
    except Exception: # Catch potential errors during palette access
         is_dark_theme = False # Default to light if palette fails
         log_debug("Could not determine theme lightness, defaulting to light for icons.")


    # --- Determine Theme Folder ---
    theme_folder = "dark" if is_dark_theme else "light"
    # Note: Add logic here if you want custom theme names to map to icon folders

    themed_path = ICON_BASE_DIR / theme_folder / icon_name
    default_path = ICON_BASE_DIR / "default" / icon_name

    icon_path_to_load = None

    # --- Find Icon File ---
    if themed_path.is_file():
        icon_path_to_load = themed_path
        # log_debug(f"Theme icon found: {themed_path}") # Optional debug log
    elif default_path.is_file():
        icon_path_to_load = default_path
        # log_debug(f"Default icon found: {default_path}") # Optional debug log
    else:
        # Icon not found in theme OR default - THIS IS OKAY!
        log_debug(f"Icon '{icon_name}' not found in '{theme_folder}' or 'default' folders. Skipping icon.")
        return QIcon() # Return empty icon, no error/warning needed

    # --- Load Icon ---
    try:
        # log_debug(f"Loading icon from: {icon_path_to_load}") # Optional debug log
        return QIcon(str(icon_path_to_load))
    except Exception as e:
        # Log an error if loading fails *even though the file exists*
        log_error(f"Failed to load existing icon file '{icon_path_to_load}': {e}")
        return QIcon() # Return empty on load error
        
        
        
        
        
        
class HelpDialog(QDialog):
    """A reusable dialog for displaying pre-formatted help text."""
    def __init__(self, title: str, text: str, parent=None):
        super().__init__(parent)
        log_debug(f"Creating HelpDialog with title: '{title}'")
        self.setWindowTitle(title)
        self.setMinimumSize(600, 500) # Give it a good default size

        layout = QVBoxLayout(self)
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        # Use setMarkdown to render the simple formatting from the constants file
        text_edit.setMarkdown(text)
        
        layout.addWidget(text_edit)
        
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        button_box.accepted.connect(self.accept) # Close button triggers accept
        button_box.rejected.connect(self.reject) # Also connect reject just in case
        layout.addWidget(button_box)        
