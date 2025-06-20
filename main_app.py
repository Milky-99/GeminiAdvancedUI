# -*- coding: utf-8 -*-

import sys
from pathlib import Path

# --- PyQt Imports ---
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QCoreApplication, QSettings

# --- Project Imports ---
# Initialize directories first
from utils import constants
constants.ensure_dirs() # Create data folders if they don't exist

# Then import modules that might use these constants or logger
from utils.logger import log_info, log_error, log_critical, log_debug, log_warning
from utils.helpers import apply_theme
from core.settings_service import SettingsService
from core.api_key_service import ApiKeyService
from core.prompt_service import PromptService
from core.wildcard_resolver import WildcardResolver
from core.image_processor import ImageProcessor
from core.gemini_handler import GeminiHandler, SDK_AVAILABLE
from ui.main_window import MainWindow


def main():
    """Main entry point for the application."""
    # --- Basic Application Setup ---
    # Add a debug log at the very start of the function
    log_debug("--- main() function started ---")
    
    QCoreApplication.setOrganizationName("Milky99")
    QCoreApplication.setApplicationName("Gemini Studio UI")

    app = QApplication(sys.argv)

    log_info("-" * 30)
    log_info(f"Starting {QCoreApplication.applicationName()}...")
    log_info(f"Application Directory: {constants.APP_DIR}")
    log_info(f"Data Directory: {constants.DATA_DIR}")

    if not SDK_AVAILABLE:
        log_critical("Google GenAI SDK not found. Application cannot run.")
        # Show a simple message box even without the full UI if SDK is missing
        from PyQt6.QtWidgets import QMessageBox
        msg_box = QMessageBox()
        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.setWindowTitle("Fatal Error")
        msg_box.setText("The required 'google-genai' library is not installed.\nPlease install it using: pip install google-genai")
        msg_box.exec()
        sys.exit(1)

    # --- Initialize Core Services ---
    try:
        log_info("Initializing core services...")
        settings_service = SettingsService()
        api_key_service = ApiKeyService()
        log_debug(f"DEBUG: Value of constants.MAX_PROMPT_SLOTS before PromptService init: {constants.MAX_PROMPT_SLOTS}")
        prompt_service = PromptService(max_slots=constants.MAX_PROMPT_SLOTS)
        wildcard_resolver = WildcardResolver() # ImageProcessor is static methods
        gemini_handler = GeminiHandler()
        log_info("Core services initialized.")
    except Exception as e:
        log_critical(f"Failed to initialize core services: {e}", exc_info=True)
        # Attempt to show error even if full UI fails
        from PyQt6.QtWidgets import QMessageBox
        msg_box = QMessageBox()
        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.setWindowTitle("Initialization Error")
        msg_box.setText(f"Failed to initialize core services:\n{e}\n\nCheck logs for details.")
        msg_box.exec()
        sys.exit(1)


    # --- Apply Initial Theme ---
    initial_theme = settings_service.get_setting("theme", constants.DEFAULT_THEME)
    if not initial_theme: # Checks for None or empty string
        log_warning(f"Found invalid/empty theme value ('{initial_theme}') in settings. Falling back to default: {constants.DEFAULT_THEME}")
        initial_theme = constants.DEFAULT_THEME
        # Optionally fix the setting in the file immediately
        settings_service.set_setting("theme", initial_theme)    
    log_info(f"Applying initial theme: {initial_theme}")
    apply_theme(app, initial_theme)

    # --- Create and Show Main Window ---
    try:
        log_info("Creating main window...")
        main_window = MainWindow(
            settings_service=settings_service,
            api_key_service=api_key_service,
            prompt_service=prompt_service,
            wildcard_resolver=wildcard_resolver,
            gemini_handler=gemini_handler
        )
        main_window.show()
        log_info("Main window displayed.")
    except Exception as e:
        log_critical(f"Failed to create or show main window: {e}", exc_info=True)
        # Attempt to show error
        from PyQt6.QtWidgets import QMessageBox
        msg_box = QMessageBox()
        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.setWindowTitle("UI Error")
        msg_box.setText(f"Failed to create the main application window:\n{e}\n\nCheck logs for details.")
        msg_box.exec()
        sys.exit(1)


    # --- Start Event Loop ---
    log_info("Starting application event loop.")
    exit_code = app.exec()
    log_info(f"Application finished with exit code: {exit_code}")
    # Add a debug log at the very end of the function
    log_debug("--- main() function finished ---")
    sys.exit(exit_code)
    
    
if __name__ == "__main__":
    main()