# -*- coding: utf-8 -*-
import webbrowser 
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QStackedWidget, QMenuBar, QStatusBar,
    QFileDialog, QApplication, QSplitter
)
from PyQt6.QtGui import QAction, QIcon, QKeySequence # Add QKeySequence for shortcuts
from PyQt6.QtCore import Qt, pyqtSlot, QSize, QEvent 
from PyQt6.QtGui import QActionGroup
# --- Project Imports ---
from utils import constants
from utils.logger import log_info, log_debug, log_error, log_warning 
from utils.helpers import apply_theme, show_info_message, show_error_message, show_warning_message, discover_custom_themes, HelpDialog

# Import Core components (use Type checking if needed)
from core.settings_service import SettingsService
from core.api_key_service import ApiKeyService
from core.prompt_service import PromptService
from core.wildcard_resolver import WildcardResolver
from core.gemini_handler import GeminiHandler
from core.image_processor import ImageProcessor # For metadata extraction

# Import UI Components
from .single_mode_widget import SingleModeWidget # To be created
from .multi_mode_widget import MultiModeWidget   # To be created
from .settings_dialog import SettingsDialog     # To be created
from .api_key_manager_dialog import ApiKeyManagerDialog # To be created
from .prompt_manager_dialog import PromptManagerDialog   # To be created
from .wildcard_manager_dialog import WildcardManagerDialog
from .image_selector_meta_viewer import ImageSelectorMetaViewerDialog
from utils.helpers import get_themed_icon
class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self,
                 settings_service: SettingsService,
                 api_key_service: ApiKeyService,
                 prompt_service: PromptService,
                 wildcard_resolver: WildcardResolver,
                 gemini_handler: GeminiHandler,
                 parent=None):
        super().__init__(parent)

        self.settings_service = settings_service
        self.api_key_service = api_key_service
        self.prompt_service = prompt_service
        self.wildcard_resolver = wildcard_resolver
        self.gemini_handler = gemini_handler

        self._setup_ui()
        self._connect_signals()
        self._load_initial_state()



    def _setup_ui(self):
        """Initialize UI elements."""
        log_debug("--- MainWindow _setup_ui called ---")
        self.setObjectName("mainWindow")
        self.setWindowTitle(QApplication.applicationName())
        self.setMinimumSize(QSize(1000, 700))
        self.resize(1200, 800)

        # Set Application Icon
        try:
            app_icon_path = constants.APP_DIR / "icons" / "app_icon.png"
            if app_icon_path.is_file():
                self.setWindowIcon(QIcon(str(app_icon_path)))
                log_info(f"Application icon set from: {app_icon_path}")
            else:
                log_warning(f"Application icon not found at '{app_icon_path}'.")
        except Exception as e:
            log_error(f"Failed to set window icon: {e}", exc_info=True)

        # Central Widget and Layout
        self.central_widget = QWidget()
        self.central_widget.setObjectName("centralWidgetMainWindow")
        self.main_layout = QVBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.setCentralWidget(self.central_widget)

        # Stacked Widget for Modes
        self.stacked_widget = QStackedWidget()
        self.main_layout.addWidget(self.stacked_widget)

        # Instantiate Mode Widgets
        self.single_mode_widget = SingleModeWidget(
            self.settings_service, self.api_key_service, self.prompt_service,
            self.wildcard_resolver, self.gemini_handler, self
        )
        self.multi_mode_widget = MultiModeWidget(
            self.settings_service, self.api_key_service, self.prompt_service,
            self.wildcard_resolver, self.gemini_handler, self
        )
        self.stacked_widget.addWidget(self.single_mode_widget)
        self.stacked_widget.addWidget(self.multi_mode_widget)

        # Menu Bar
        self.menu_bar = self.menuBar()
        # ... (File and View menus are unchanged) ...
        # File Menu
        self.file_menu = self.menu_bar.addMenu("&File")
        self.exit_action = QAction(get_themed_icon("exit.png"), "&Exit", self)
        self.exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.file_menu.addAction(self.exit_action)

        # View Menu
        self.view_menu = self.menu_bar.addMenu("&View")
        self.switch_mode_action = QAction("Switch to &Multi-API Mode", self)
        self.view_menu.addAction(self.switch_mode_action)
        self.theme_menu = self.view_menu.addMenu("&Theme")
        self.theme_action_group = QActionGroup(self)
        self.theme_action_group.setExclusive(True)
        # ... theme actions ...
        self.auto_theme_action = QAction("&Auto", self, checkable=True)
        self.light_theme_action = QAction("&Light", self, checkable=True)
        self.dark_theme_action = QAction("&Dark", self, checkable=True)
        self.theme_menu.addAction(self.auto_theme_action)
        self.theme_menu.addAction(self.light_theme_action)
        self.theme_menu.addAction(self.dark_theme_action)
        self.theme_action_group.addAction(self.auto_theme_action)
        self.theme_action_group.addAction(self.light_theme_action)
        self.theme_action_group.addAction(self.dark_theme_action)
        # ... custom theme actions ...
        self.custom_theme_actions: Dict[str, QAction] = {}
        custom_themes = discover_custom_themes()
        if custom_themes:
            self.theme_menu.addSeparator()
            for theme_name, theme_path in custom_themes:
                safe_theme_name = theme_name.replace(" ", "_").replace("-", "_")
                action = QAction(theme_name, self, checkable=True)
                action.setObjectName(f"customThemeAction_{safe_theme_name}")
                action.setData(theme_name)
                self.theme_menu.addAction(action)
                self.theme_action_group.addAction(action)
                self.custom_theme_actions[theme_name] = action

        # Tools Menu
        self.tools_menu = self.menu_bar.addMenu("&Tools")
        # ... (Tools menu is unchanged) ...
        self.api_keys_action = QAction("&API Key Manager...", self)
        self.prompts_action = QAction("&Prompt Manager...", self)
        self.wildcards_action = QAction("&Wildcard Manager...", self)
        self.image_meta_action = QAction("&View Image Metadata...", self)
        self.settings_action = QAction("&Settings...", self)
        self.settings_action.setIcon(get_themed_icon("settings.png"))
        self.tools_menu.addAction(self.api_keys_action)
        self.tools_menu.addAction(self.prompts_action)
        self.tools_menu.addAction(self.wildcards_action)
        self.tools_menu.addSeparator()
        self.tools_menu.addAction(self.image_meta_action)
        self.tools_menu.addSeparator()
        self.tools_menu.addAction(self.settings_action)


        # --- MODIFIED Help Menu ---
        self.help_menu = self.menu_bar.addMenu("&Help")
        self.help_menu.setObjectName("helpMenu")

        self.app_guide_action = QAction(get_themed_icon("help.png"), "Application Guide", self)
        self.app_guide_action.setObjectName("appGuideAction")

        self.wildcard_help_action = QAction(get_themed_icon("wildcard.png"), "Wildcard Syntax Help", self)
        self.wildcard_help_action.setObjectName("wildcardHelpAction")
        
        self.support_action = QAction(get_themed_icon("heart.png"), "&Support Me", self)
        self.support_action.setObjectName("supportAction")
        
        self.about_action = QAction("&About...", self)
        self.about_action.setObjectName("aboutAction")
        
        self.help_menu.addAction(self.app_guide_action)
        self.help_menu.addAction(self.wildcard_help_action)
        self.help_menu.addSeparator()
        self.help_menu.addAction(self.support_action)
        self.help_menu.addSeparator()
        self.help_menu.addAction(self.about_action)
        # --- END MODIFIED Help Menu ---

        # Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready.")
        log_debug("--- MainWindow _setup_ui finished ---")

    def _connect_signals(self):
        """Connect UI signals to slots."""
        log_debug("--- MainWindow _connect_signals called ---")
        self.exit_action.triggered.connect(self.close)
        self.switch_mode_action.triggered.connect(self._switch_mode)
        self.single_mode_widget.status_update.connect(self.status_bar.showMessage)
        self.multi_mode_widget.status_update.connect(self.status_bar.showMessage)

        # Theme Actions
        self.auto_theme_action.triggered.connect(lambda: self._set_theme("Auto"))
        self.light_theme_action.triggered.connect(lambda: self._set_theme("Light"))
        self.dark_theme_action.triggered.connect(lambda: self._set_theme("Dark"))
        for theme_name, action in self.custom_theme_actions.items():
            action.triggered.connect(lambda checked=False, name=theme_name: self._set_theme(name))

        # Tools Actions
        self.settings_action.triggered.connect(self._open_settings)
        self.api_keys_action.triggered.connect(self._open_api_keys)
        self.prompts_action.triggered.connect(self._open_prompts)
        self.wildcards_action.triggered.connect(self._open_wildcard_manager)
        self.image_meta_action.triggered.connect(self._open_image_meta_viewer)

        # Help Actions
        self.app_guide_action.triggered.connect(self._show_app_guide)             # <<< ADD THIS LINE
        self.wildcard_help_action.triggered.connect(self._show_wildcard_syntax)   # <<< ADD THIS LINE
        self.support_action.triggered.connect(self._open_support_link)
        self.about_action.triggered.connect(self._about)

        # Connect Prompt Service Signal for Real-time Updates
        if hasattr(self.prompt_service, 'prompts_updated') and callable(getattr(self.prompt_service, 'prompts_updated', None)):
            try:
                self.prompt_service.prompts_updated.connect(self.single_mode_widget.update_prompt_list)
                self.prompt_service.prompts_updated.connect(self.multi_mode_widget.update_prompt_list)
                log_debug("Connected prompt_service.prompts_updated signal to mode widget updates.")
            except (TypeError, AttributeError) as e:
                 log_error(f"Error connecting prompt_service.prompts_updated signal: {e}")
        else:
            log_warning("PromptService does not have prompts_updated signal or it's not callable.")
        log_debug("--- MainWindow _connect_signals finished ---")

    def _load_initial_state(self):
        """Load settings and set initial UI state."""
        log_info("Loading initial window state...")
        current_theme = self.settings_service.get_setting("theme", constants.DEFAULT_THEME)
        if not current_theme:
            log_warning(f"Found invalid/empty theme value ('{current_theme}') during window load. Using default: {constants.DEFAULT_THEME}")
            current_theme = constants.DEFAULT_THEME        
        action_to_check = None
        if current_theme == "Dark": 
            action_to_check = self.dark_theme_action
        elif current_theme == "Light":
            action_to_check = self.light_theme_action
        elif current_theme == "Auto":
            action_to_check = self.auto_theme_action
        elif current_theme in self.custom_theme_actions: # Check if it's a custom theme
            action_to_check = self.custom_theme_actions[current_theme]
        else:
            log_warning(f"Saved theme '{current_theme}' not found in built-in or custom themes. Defaulting to 'Auto'.")
            self.settings_service.set_setting("theme", "Auto", save=True) # Fix setting
            action_to_check = self.auto_theme_action

        if action_to_check:
             action_to_check.setChecked(True)
        else: # Should not happen if fallback works, but good practice
            self.auto_theme_action.setChecked(True)


        # Set initial mode
        last_mode = self.settings_service.get_setting("last_mode", "Single")
        if last_mode == "Multi":
            self.stacked_widget.setCurrentWidget(self.multi_mode_widget)
            self.switch_mode_action.setText("Switch to &Single-API Mode")
        else:
            self.stacked_widget.setCurrentWidget(self.single_mode_widget)
            self.switch_mode_action.setText("Switch to &Multi-API Mode")
        log_info(f"Initial mode set to: {last_mode}")


    def changeEvent(self, event: QEvent):
        """Handle window state changes for thumbnail optimization."""
        super().changeEvent(event) # Call base implementation first
        if event.type() == QEvent.Type.WindowStateChange:
            is_minimized = self.isMinimized()
            log_debug(f"Window state changed. Minimized: {is_minimized}")
            # Notify MultiModeWidget (assuming it's the active relevant widget)
            # Check if multi_mode_widget exists and is the current one (or just always notify it)
            if hasattr(self, 'multi_mode_widget'): # Check if it exists
                 self.multi_mode_widget.handle_window_state_change(is_minimized)


    @pyqtSlot()
    def _open_support_link(self):
        """Opens the 'Buy Me a Coffee' link in the default web browser."""
        url = "https://buymeacoffee.com/milky99"
        log_info(f"Opening support link: {url}")
        try:
            webbrowser.open(url)
        except Exception as e:
            log_error(f"Failed to open support link: {e}", exc_info=True)
            show_error_message(self, "Error", f"Could not open the URL:\n{url}")


    @pyqtSlot()
    def _switch_mode(self):
        """Switches between Single and Multi API modes."""
        current_widget = self.stacked_widget.currentWidget()
        next_index = 1 - self.stacked_widget.currentIndex() # Toggle between 0 and 1
        next_widget = self.stacked_widget.widget(next_index)

        log_info(f"Attempting to switch mode from {type(current_widget).__name__} to {type(next_widget).__name__}")

        # --- IMPORTANT: Mode Shutdown/Cleanup ---
        # Before switching, ensure the current mode stops all API requests
        # and potentially releases the API key if needed (especially for multi-mode)
        can_switch = True
        if hasattr(current_widget, 'shutdown_mode') and callable(current_widget.shutdown_mode):
             log_debug(f"Calling shutdown_mode for {type(current_widget).__name__}...")
             can_switch = current_widget.shutdown_mode() # This method should return True if safe to switch
             if not can_switch:
                  log_warning("Mode switch cancelled by current widget's shutdown_mode.")
                  show_warning_message(self, "Switch Cancelled", "Cannot switch mode while operations are active. Please stop all requests first.")
                  return
        else:
             log_warning(f"Current widget {type(current_widget).__name__} has no shutdown_mode method.")


        # Proceed with switch
        self.stacked_widget.setCurrentIndex(next_index)
        new_mode_name = "Multi" if next_index == 1 else "Single"
        self.settings_service.set_setting("last_mode", new_mode_name)

        if new_mode_name == "Multi":
            self.switch_mode_action.setText("Switch to &Single-API Mode")
        else:
            self.switch_mode_action.setText("Switch to &Multi-API Mode")

        # --- IMPORTANT: Mode Startup/Initialization ---
        if hasattr(next_widget, 'activate_mode') and callable(next_widget.activate_mode):
             log_debug(f"Calling activate_mode for {type(next_widget).__name__}...")
             next_widget.activate_mode() # Allow the new widget to initialize itself
        else:
             log_warning(f"Next widget {type(next_widget).__name__} has no activate_mode method.")


        log_info(f"Switched mode to: {new_mode_name}")
        self.status_bar.showMessage(f"Switched to {new_mode_name}-API Mode.")


    @pyqtSlot()
    def _open_settings(self):
        """Opens the Settings dialog."""
        log_debug("Opening Settings dialog.")
        # Pass necessary services if the dialog needs them directly
        dialog = SettingsDialog(self.settings_service, self)
        if dialog.exec(): # exec() blocks until dialog is closed
            log_info("Settings dialog accepted.")
            # Re-apply theme in case it changed
            new_theme = self.settings_service.get_setting("theme", constants.DEFAULT_THEME)
            self._set_theme(new_theme, force_apply=True) # Force apply even if name is same
            # Update logging status immediately
            # set_logging_enabled is handled within SettingsService.set_setting
            log_info("Settings possibly updated.")
            # Potentially notify mode widgets if settings they care about changed
            self.single_mode_widget.on_settings_changed()
            self.multi_mode_widget.on_settings_changed()

        else:
            log_info("Settings dialog cancelled.")

    @pyqtSlot()
    def _open_api_keys(self):
        """Opens the API Key Manager dialog."""
        log_debug("Opening API Key Manager dialog.")
        dialog = ApiKeyManagerDialog(self.api_key_service, self.settings_service, self)
        dialog.exec()
        log_info("API Key Manager dialog closed.")
        # Update API key selectors in modes if necessary
        self.single_mode_widget.update_api_key_list()
        self.multi_mode_widget.update_api_key_list()

    @pyqtSlot()
    def _open_prompts(self):
        """Opens the redesigned Prompt Manager dialog, passing context and handling signals."""
        log_debug("Opening Prompt Manager dialog.")

        # Determine context
        current_widget = self.stacked_widget.currentWidget()
        instance_data = None
        mode = "Single" # Default
        if current_widget == self.multi_mode_widget:
            mode = "Multi"
            if hasattr(self.multi_mode_widget, 'get_instance_summary_for_dialog'):
                instance_data = self.multi_mode_widget.get_instance_summary_for_dialog()
                log_debug(f"Launching prompt manager from Multi-Mode with instance data: {instance_data}")
            else:
                log_warning("MultiModeWidget is missing 'get_instance_summary_for_dialog' method.")
        else:
            log_debug("Launching prompt manager from Single-Mode.")

        # Instantiate the dialog
        dialog = PromptManagerDialog(
            prompt_service=self.prompt_service,
            wildcard_resolver=self.wildcard_resolver,
            settings_service=self.settings_service,
            current_mode=mode,
            multi_mode_instance_data=instance_data,
            parent=self
        )

        # --- Connect Prompt Service Signal TO the Dialog ---
        connected_slot = None # Keep track of the connected slot
        if hasattr(self.prompt_service, 'prompts_updated') and hasattr(dialog, '_handle_external_prompt_update'):
            try:
                connected_slot = dialog._handle_external_prompt_update
                self.prompt_service.prompts_updated.connect(connected_slot)
                log_debug("Connected prompt_service.prompts_updated to PromptManagerDialog._handle_external_prompt_update.")
            except (TypeError, AttributeError) as e:
                 log_warning(f"Failed to connect prompts_updated signal to dialog: {e}")
                 connected_slot = None # Ensure it's None if connection failed
        else:
            log_warning("PromptService signal or dialog slot missing for real-time updates.")
        # --- End Connect ---

        # Execute the dialog (blocks here)
        result = dialog.exec()

        # --- Disconnect Signal FROM the Dialog ---
        if connected_slot:
            try:
                self.prompt_service.prompts_updated.disconnect(connected_slot)
                log_debug("Disconnected prompt_service.prompts_updated from PromptManagerDialog.")
            except (TypeError, RuntimeError) as e:
                log_warning(f"Error disconnecting prompt service signal from PromptManagerDialog: {e}")
        # --- End Disconnect ---

        # --- Handle Results (Load Prompt) ---
        if result == PromptManagerDialog.Accepted:
            selected_prompt_text, load_target = dialog.get_load_data()
            if selected_prompt_text and load_target:
                log_info(f"Loading prompt from Prompt Manager into target: {load_target}")
                target_widget = None
                expected_widget_type = None
                if load_target == "single":
                     target_widget = self.single_mode_widget
                     expected_widget_type = SingleModeWidget # Use class name
                elif load_target.startswith("multi_"):
                     target_widget = self.multi_mode_widget
                     expected_widget_type = MultiModeWidget # Use class name

                # Verify current widget matches the target type
                if isinstance(current_widget, expected_widget_type):
                    if hasattr(target_widget, 'load_prompt_from_meta'):
                        target_widget.load_prompt_from_meta(selected_prompt_text, load_target)
                    else:
                        log_warning(f"{type(target_widget).__name__} has no 'load_prompt_from_meta' method.")
                        show_error_message(self, "Load Error", f"{type(target_widget).__name__} cannot load prompt.")
                else:
                     log_error(f"Mismatch between load target '{load_target}' and current widget '{type(current_widget).__name__}'. Load aborted.")
                     show_error_message(self, "Load Error", "Cannot load prompt: Target mode does not match active mode.")
            elif result == PromptManagerDialog.Accepted:
                 log_debug("Prompt Manager accepted, but no prompt/target selected for loading.")
        else: # Dialog was rejected/closed
            log_info("Prompt Manager dialog closed or cancelled.")

        # --- Final Update After Close (Optional Fallback) ---
        # While real-time updates should handle most cases, this ensures lists
        # are definitely up-to-date after the dialog is closed, regardless of
        # whether signals worked or if changes were only saved on dialog close.
        try:
            log_debug("Updating prompt lists in modes after PromptManagerDialog closed (fallback).")
            self.single_mode_widget.update_prompt_list()
            self.multi_mode_widget.update_prompt_list()
        except Exception as e:
            log_error(f"Error updating prompt lists after PromptManager close: {e}")

    @pyqtSlot()
    def _open_wildcard_manager(self):
        """Opens the Wildcard Manager dialog."""
        log_debug("Opening Wildcard Manager dialog.")
        # Pass the wildcard_resolver instance to the dialog
        dialog = WildcardManagerDialog(self.wildcard_resolver, self)
        dialog.exec() # Show modally
        log_info("Wildcard Manager dialog closed.")

    @pyqtSlot()
    def _open_image_meta_viewer(self):
        """Opens the combined Image Selector & Metadata Viewer dialog, passing context."""
        log_debug("Opening Image Selector & Metadata Viewer dialog.")

        # Determine context
        current_widget = self.stacked_widget.currentWidget()
        instance_data = None
        mode = "Single" # Default
        if current_widget == self.multi_mode_widget:
            mode = "Multi"
            instance_data = self.multi_mode_widget.get_instance_summary_for_dialog()
            log_debug(f"Launching meta viewer from Multi-Mode with instance data: {instance_data}")
        else:
            log_debug("Launching meta viewer from Single-Mode.")

        # Instantiate the dialog, passing context
        dialog = ImageSelectorMetaViewerDialog(
            settings_service=self.settings_service,
            current_mode=mode,
            multi_mode_instance_data=instance_data,
            parent=self
        )
        result = dialog.exec()

        if result == ImageSelectorMetaViewerDialog.Accepted:
            selected_prompt_text, load_target = dialog.get_load_data()

            if selected_prompt_text and load_target:
                log_info(f"Loading prompt from image metadata into target: {load_target}")

                # Route call based on target
                if load_target == "single" and current_widget == self.single_mode_widget:
                    if hasattr(current_widget, 'load_prompt_from_meta'):
                        current_widget.load_prompt_from_meta(selected_prompt_text, load_target)
                    else:
                        log_warning(f"SingleModeWidget has no 'load_prompt_from_meta' method.")
                        show_error_message(self, "Load Error", "Single Mode widget cannot load prompt from metadata.")
                elif load_target.startswith("multi_") and current_widget == self.multi_mode_widget:
                    if hasattr(current_widget, 'load_prompt_from_meta'):
                        current_widget.load_prompt_from_meta(selected_prompt_text, load_target)
                    else:
                        log_warning(f"MultiModeWidget has no 'load_prompt_from_meta' method.")
                        show_error_message(self, "Load Error", "Multi Mode widget cannot load prompt from metadata.")
                else:
                    log_error(f"Mismatch between load target '{load_target}' and current widget '{type(current_widget).__name__}'. Load aborted.")
                    show_error_message(self, "Load Error", "Cannot load prompt: Target mode does not match active mode.")

            elif result == ImageSelectorMetaViewerDialog.Accepted:
                 log_debug("Dialog accepted, but no prompt or target was selected/available for loading.")
            else:
                 # Should not happen if Accepted is returned, but handle defensively
                 log_debug("Dialog accepted but load data was incomplete.")

        else: # Dialog was rejected/closed
            log_debug("Image Selector & Metadata Viewer dialog cancelled.")
    @pyqtSlot(str)
    def _set_theme(self, theme_name: str, force_apply=False):
        """Applies the selected theme and saves the setting."""
        current_theme = self.settings_service.get_setting("theme")
        if theme_name != current_theme or force_apply:
            log_info(f"Setting theme to: {theme_name}")
            # The apply_theme function now handles both built-in and custom
            apply_theme(QApplication.instance(), theme_name)
            self.settings_service.set_setting("theme", theme_name)

            # Update check state in menu (uses the action group now)
            action_to_check = None
            if theme_name == "Dark": action_to_check = self.dark_theme_action
            elif theme_name == "Light": action_to_check = self.light_theme_action
            elif theme_name == "Auto": action_to_check = self.auto_theme_action
            elif theme_name in self.custom_theme_actions: action_to_check = self.custom_theme_actions[theme_name]

            # Ensure the correct action is checked (the group handles unchecking others)
            if action_to_check and not action_to_check.isChecked():
                 action_to_check.setChecked(True)

        else:
            log_debug(f"Theme '{theme_name}' is already active.")


    @pyqtSlot()
    def _show_app_guide(self):
        """Displays the general application guide."""
        log_debug("Showing Application Guide dialog.")
        help_dialog = HelpDialog("Application Guide", constants.GENERAL_APP_HELP_TEXT, self)
        help_dialog.exec()

    @pyqtSlot()
    def _show_wildcard_syntax(self):
        """Displays the wildcard syntax help."""
        log_debug("Showing Wildcard Syntax dialog.")
        help_dialog = HelpDialog("Wildcard Syntax Help", constants.WILDCARD_SYNTAX_HELP_TEXT, self)
        help_dialog.exec()


    @pyqtSlot()
    def _about(self):
        """Shows a simple about message."""
        # Replace with a proper About dialog later if needed
        show_info_message(self, "About Gemini Advanced UI",
                          f"{QApplication.applicationName()}\n\n"
                          "A UI for interacting with the Google Gemini API.\n\n"
                          f"(Organization: {QApplication.organizationName()})") # Example

    def closeEvent(self, event):
        """Handle window close event."""
        log_info("Close event triggered. Shutting down...")
        # --- IMPORTANT: Ensure modes are shut down cleanly ---
        can_close = True
        log_debug("Requesting stop on active instances before shutdown check...")
        active_widgets = []
        if self.stacked_widget.currentWidget() == self.multi_mode_widget:
            active_widgets = list(self.multi_mode_widget._instance_widgets.values())
        elif self.stacked_widget.currentWidget() == self.single_mode_widget:
            active_widgets = [self.single_mode_widget]

        for widget in active_widgets:
             if hasattr(widget, 'is_running') and widget.is_running():
                  log_info(f"Requesting stop for active widget: {type(widget).__name__} (ID: {getattr(widget, 'instance_id', 'N/A')})")
                  if hasattr(widget, 'stop_generation'):
                      widget.stop_generation()
        # Give signals a moment to process (optional, but can help)
        QApplication.processEvents()
        # time.sleep(0.05) # Avoid sleep in main thread during close if possible        
        for i in range(self.stacked_widget.count()):
             widget = self.stacked_widget.widget(i)
             if hasattr(widget, 'shutdown_mode') and callable(widget.shutdown_mode):
                  if not widget.shutdown_mode(is_closing=True): # Pass flag indicating app closure
                       log_warning(f"Shutdown cancelled by {type(widget).__name__}. Aborting close.")
                       can_close = False
                       break # Stop checking other widgets

        if can_close:
            log_info("Shutting down Gemini handler...")
            self.gemini_handler.shutdown_all_clients()
            # Settings are saved automatically by SettingsService on set_setting
            log_info("Exiting application.")
            event.accept()
        else:
            show_warning_message(self, "Close Cancelled", "Cannot close the application while operations are active in one of the modes. Please stop all requests first.")
            event.ignore()