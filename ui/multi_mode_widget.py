# -*- coding: utf-8 -*-

import time
import random
from typing import Dict, Optional, List, Tuple

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QScrollArea, QPushButton, QHBoxLayout, QGroupBox,
    QMessageBox, QApplication, QCheckBox
)
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, pyqtSlot # Import QTimer

# --- Project Imports ---
from utils import constants
from utils.logger import log_info, log_debug, log_error, log_warning
from utils.helpers import show_info_message, show_error_message
from core.settings_service import SettingsService
from core.api_key_service import ApiKeyService
from core.prompt_service import PromptService
from core.wildcard_resolver import WildcardResolver
from core.gemini_handler import GeminiHandler
from .components.instance_widget import InstanceWidget # Import the instance component

class MultiModeWidget(QWidget):
    """Widget for handling multiple parallel API interactions."""
    status_update = pyqtSignal(str, int) # message, timeout

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
        # IMPORTANT: Pass the single, shared GeminiHandler instance
        self.gemini_handler = gemini_handler
        self.main_window = parent # Reference to MainWindow

        self._instance_widgets: Dict[int, InstanceWidget] = {} # Store instances by ID
        self._assigned_keys: Dict[int, str] = {}
        self._next_instance_id = 1
        self._active_generations = 0 # Count how many instances are running
        self._scroll_timer = QTimer(self) 
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(250)

        self._setup_ui()
        self._connect_signals()
        
        # Initial loading might happen in activate_mode



    def _setup_ui(self):
        """Set up the UI elements for Multi Mode."""
        self.setObjectName("multiModeWidget") # Name the main widget
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setObjectName("mainLayoutMultiMode")
        self.main_layout.setContentsMargins(5, 5, 5, 5)

        # --- Controls Bar ---
        controls_layout = QHBoxLayout()
        controls_layout.setObjectName("controlsLayoutMultiMode")
        self.add_instance_button = QPushButton("Add API Instance")
        self.add_instance_button.setObjectName("addInstanceButtonMultiMode")
        self.global_continuous_checkbox = QCheckBox("Continuous (All)")
        self.global_continuous_checkbox.setObjectName("globalContinuousCheckboxMultiMode")
        self.global_autosave_checkbox = QCheckBox("Auto-Save (All)")
        self.global_autosave_checkbox.setObjectName("globalAutosaveCheckboxMultiMode")
        self.start_all_button = QPushButton("Start All Ready")
        self.start_all_button.setObjectName("startAllButtonMultiMode")
        self.stop_all_button = QPushButton("Stop All Running")
        self.stop_all_button.setObjectName("stopAllButtonMultiMode")
        self.clear_all_results_button = QPushButton("Clear All Results")
        self.clear_all_results_button.setObjectName("clearAllResultsButtonMultiMode")

        controls_layout.addWidget(self.add_instance_button)
        controls_layout.addWidget(self.global_continuous_checkbox)
        controls_layout.addWidget(self.global_autosave_checkbox)
        controls_layout.addStretch(1)
        controls_layout.addWidget(self.start_all_button)
        controls_layout.addWidget(self.stop_all_button)
        controls_layout.addWidget(self.clear_all_results_button)
        self.main_layout.addLayout(controls_layout)

        # --- Scroll Area for Instances ---
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("instanceScrollAreaMultiMode")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.instance_container_widget = QWidget() # Widget inside scroll area
        self.instance_container_widget.setObjectName("instanceContainerWidgetMultiMode")
        self.instance_layout = QVBoxLayout(self.instance_container_widget) # Layout for instances
        self.instance_layout.setObjectName("instanceLayoutMultiMode")
        self.instance_layout.setAlignment(Qt.AlignmentFlag.AlignTop) # Add instances from top
        self.instance_layout.setSpacing(5) # Spacing between instances

        # Placeholder label
        self.placeholder_label = QLabel("Click 'Add API Instance' to begin.")
        self.placeholder_label.setObjectName("placeholderLabelMultiMode")
        self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_label.setMinimumHeight(50)
        self.instance_layout.addWidget(self.placeholder_label)
        # Add stretch at the bottom to push instances up
        self.instance_layout.addStretch(1)

        self.scroll_area.setWidget(self.instance_container_widget)
        self.main_layout.addWidget(self.scroll_area)

        # Initial button states
        self.start_all_button.setEnabled(False)
        self.stop_all_button.setEnabled(False)
        self.clear_all_results_button.setEnabled(False)




    @pyqtSlot()
    def _on_scroll(self):
        """Restarts the timer whenever the scroll bar moves."""
        self._scroll_timer.start() # Restart timer on each scroll event
        
    
    @pyqtSlot()
    def _update_visible_thumbnails(self):
        """Checks visibility and loads/unloads thumbnails for instances."""
        log_debug("Scroll timer timeout: Updating visible thumbnails...")
        if not self.isVisible(): # Don't bother if the whole widget isn't visible
            return
        if self.window().isMinimized(): # Don't load if minimized
             log_debug("Skipping thumbnail update, window is minimized.")
             return

        viewport_rect = self.scroll_area.viewport().rect()
        for instance_widget in self._instance_widgets.values():
            # Map instance position relative to the viewport
            instance_pos_in_viewport = instance_widget.mapTo(self.scroll_area.viewport(), instance_widget.rect().topLeft())
            instance_rect_in_viewport = instance_widget.rect().translated(instance_pos_in_viewport)

            is_visible = viewport_rect.intersects(instance_rect_in_viewport)
            instance_widget.update_thumbnail_visibility(is_visible) # Tell instance to update    
    
    
    def handle_window_state_change(self, is_minimized: bool):
        """Handles window minimization/restoration for thumbnails."""
        log_debug(f"MultiMode handling window state change: Minimized={is_minimized}")
        if is_minimized:
            # Clear all thumbnails immediately
            for instance_widget in self._instance_widgets.values():
                instance_widget.clear_thumbnail()
        else:
            # Window restored, trigger check after a short delay for layout settling
            QTimer.singleShot(100, self._update_visible_thumbnails)    

    
    
    def _connect_signals(self):
        """Connect signals for the main controls."""
        self.add_instance_button.clicked.connect(self._add_instance)
        self.start_all_button.clicked.connect(self._start_all_instances)
        self.stop_all_button.clicked.connect(self._stop_all_instances)
        self.clear_all_results_button.clicked.connect(self._clear_all_results)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll) 
        self._scroll_timer.timeout.connect(self._update_visible_thumbnails)
        self.global_continuous_checkbox.toggled.connect(self._toggle_all_continuous)
        self.global_autosave_checkbox.toggled.connect(self._toggle_all_autosave)
        
    def _connect_instance_signals(self, instance: InstanceWidget):
         """Connect signals for a newly added instance widget."""
         instance.request_delete.connect(self._remove_instance)
         # Relay status updates to the main window status bar
         instance.status_update.connect(self.status_update)
         # Monitor when instances start/stop generating
         # We can use the start/stop button toggle signal for simplicity
         instance.generation_started.connect(self._on_instance_started)
         instance.generation_finished.connect(self._on_instance_finished)
         instance.request_new_key.connect(self._handle_key_request)

    def get_instance_summary_for_dialog(self) -> List[Tuple[int, str, str, str]]:
        """
        Retrieves summary data for each instance, suitable for the metadata viewer dialog.

        Returns:
            List of tuples: (instance_id, api_key_name, prompt_start, status)
        """
        summary = []
        for instance_id, widget in self._instance_widgets.items():
            try:
                # Use the helper methods added to InstanceWidget
                key_name = widget.get_api_key_name()
                prompt_start = widget.get_prompt_start()
                status = widget.get_status()
                summary.append((instance_id, key_name, prompt_start, status))
            except Exception as e:
                log_error(f"Error getting summary for instance {instance_id}: {e}")
                # Append placeholder data if an error occurs fetching details
                summary.append((instance_id, "Error", "Error fetching details", "Error"))

        # Sort by instance ID for consistent order
        summary.sort(key=lambda x: x[0])
        log_debug(f"Generated instance summary for dialog: {summary}")
        return summary




    @pyqtSlot(int)
    def _handle_key_request(self, instance_id: int):
        """Handles a request from an instance for a new API key after a rate limit."""
        log_info(f"Instance {instance_id} requested a new API key due to rate limit.")
        instance_widget = self._instance_widgets.get(instance_id)
        if not instance_widget:
            log_error(f"Received key request for non-existent instance ID: {instance_id}")
            return

        current_assigned_key = self._assigned_keys.get(instance_id)
        log_debug(f"Instance {instance_id} current key: {current_assigned_key}")

        all_keys = self.api_key_service.get_key_names()
        # --- CRITICAL: Get keys currently assigned to ALL instances ---
        currently_assigned_keys = set(self._assigned_keys.values())
        log_debug(f"All known keys: {all_keys}")
        log_debug(f"Currently assigned keys: {currently_assigned_keys}")

        new_key_found = None
        # Find keys that are NOT currently assigned to ANY instance
        available_keys = [k for k in all_keys if k not in currently_assigned_keys]
        log_debug(f"Keys available for assignment (not currently in use by any instance): {available_keys}")

        if available_keys:
            # --- MODIFIED LINE: Choose randomly ---
            new_key_found = random.choice(available_keys)
            # --- END MODIFICATION ---
            log_info(f"Randomly selected unused API key '{new_key_found}' for instance {instance_id}.")
        else:
            # If no completely unused keys, try assigning a key used by *another* instance
            # that isn't the current instance's key (less ideal, but fallback)
            potentially_available_keys = [k for k in all_keys if k != current_assigned_key]
            if potentially_available_keys:
                 # --- MODIFIED LINE: Choose randomly ---
                 new_key_found = random.choice(potentially_available_keys)
                 # --- END MODIFICATION ---
                 log_warning(f"No completely unused keys found. Assigning potentially shared key '{new_key_found}' to instance {instance_id}.")
            else:
                 # Only one key exists in total, cannot switch
                 new_key_found = None


        if new_key_found:
            log_info(f"Assigning key '{new_key_found}' to instance {instance_id}.")
            # Update the assignment record FIRST
            self._assigned_keys[instance_id] = new_key_found
            # Tell the instance to switch
            instance_widget.switch_to_api_key(new_key_found)
        else:
            log_warning(f"No alternative API keys available to assign to instance {instance_id} (Only one key exists or error).")
            # Tell the instance it failed to get a new key
            instance_widget.handle_no_available_key() # Call the method to stop loop gracefully



    @pyqtSlot(bool)
    def _toggle_all_continuous(self, checked):
        """Sets the continuous mode for all instances."""
        log_info(f"Setting continuous mode for all instances to: {checked}")
        for instance in self._instance_widgets.values():
            instance.set_continuous(checked) # Call instance method

    @pyqtSlot(bool)
    def _toggle_all_autosave(self, checked):
        """Sets the auto-save mode for all instances."""
        log_info(f"Setting auto-save for all instances to: {checked}")
        for instance in self._instance_widgets.values():
            instance.set_autosave(checked) # Call instance method
        # Optionally save this global preference?
        # self.settings_service.set_setting("global_multi_autosave", checked)

    @pyqtSlot(int)
    def _on_instance_started(self, instance_id):
        """Slot called when an instance starts generating."""
        log_debug(f"Instance {instance_id} reported started.")
        # Simple recount is easiest way to ensure accuracy
        self._active_generations = sum(1 for w in self._instance_widgets.values() if w.is_running())
        self._update_action_button_states()

    @pyqtSlot(int)
    def _on_instance_finished(self, instance_id):
        """Slot called when an instance finishes generating."""
        log_debug(f"Instance {instance_id} reported finished.")
        # Simple recount is easiest way to ensure accuracy
        self._active_generations = sum(1 for w in self._instance_widgets.values() if w.is_running())
        self._update_action_button_states()


    def _update_action_button_states(self):
        """Enable/disable Start/Stop/Clear All buttons based on instance running AND looping states."""
        has_instances = bool(self._instance_widgets)
        any_active = any(w.is_running() or w.is_looping() for w in self._instance_widgets.values())
        any_idle = any(not w.is_running() and not w.is_looping() for w in self._instance_widgets.values())

        # Start All: Enabled if instances exist AND at least one is truly idle
        self.start_all_button.setEnabled(has_instances and any_idle)
        # Stop All: Enabled if instances exist AND at least one is running OR looping
        self.stop_all_button.setEnabled(has_instances and any_active)
        # Clear All: Enabled if instances exist AND at least one is truly idle
        self.clear_all_results_button.setEnabled(has_instances and any_idle)

        # Update tooltip based on state
        self.start_all_button.setToolTip("Start generation on all idle instances.")
        self.stop_all_button.setToolTip("Stop generation on all running or looping instances.")
        self.clear_all_results_button.setToolTip("Clear results for all idle instances.")
    # --- Public Methods ---



    def shutdown_mode(self, is_closing=False) -> bool:
        """Prepares the widget for mode switching or app closing."""
        instance_log_prefix = "MultiModeWidget" # For logging clarity
        log_info(f"{instance_log_prefix}: Shutdown requested (is_closing={is_closing}).")

        running_instances = [id for id, w in self._instance_widgets.items() if w.is_running() or w.is_looping()]

        if running_instances and not is_closing:
            # If just switching modes, prevent if busy
            log_warning(f"{instance_log_prefix}: Instances are still running/looping: {running_instances}. Cannot switch mode now.")
            show_error_message(self.parent(), "Operation Active",
                               f"{len(running_instances)} instance(s) are currently running or looping. "
                               "Please stop them before switching modes.")
            return False # Prevent switch

        if is_closing:
            log_info(f"{instance_log_prefix}: Application is closing. Attempting to stop and remove all instances.")
            # Attempt to stop all first (non-blocking request)
            if running_instances:
                 log_info(f"{instance_log_prefix}: Requesting stop for running/looping instances: {running_instances}")
                 self._stop_all_instances(force=True) # Request stop/cancel
                 # Give signals a moment to process (optional, small delay)
                 QApplication.processEvents()
                 # time.sleep(0.1) # Avoid sleep if possible

            # --- IMPORTANT: Explicitly remove each instance ---
            # Iterate over a copy of the keys because _remove_instance modifies the dict
            instance_ids_to_remove = list(self._instance_widgets.keys())
            log_info(f"{instance_log_prefix}: Initiating removal for instance IDs: {instance_ids_to_remove}")
            all_removed_cleanly = True
            for instance_id in instance_ids_to_remove:
                log_debug(f"{instance_log_prefix}: Calling _remove_instance for ID {instance_id} during shutdown.")
                # Call _remove_instance which handles the worker thread wait
                self._remove_instance(instance_id, silent=True) # silent=True avoids popups during close
                # Check if removal was successful (widget might still exist if it refused removal, although unlikely with silent=True)
                if instance_id in self._instance_widgets:
                     log_error(f"{instance_log_prefix}: Instance {instance_id} was not fully removed during shutdown process!")
                     all_removed_cleanly = False # Should not happen if remove logic is correct

            if not all_removed_cleanly:
                 # If something went wrong with removal, maybe prevent closing?
                 # For now, log the error and allow closing to continue.
                 log_error(f"{instance_log_prefix}: One or more instances failed to remove cleanly during shutdown.")
            # --- End Explicit Removal ---
            log_info(f"{instance_log_prefix}: Finished attempting instance removal for shutdown.")
            return True # Allow closing after attempting removal

        # If not closing and no instances were running/looping:
        log_info(f"{instance_log_prefix}: Shutdown check complete (not closing, no active instances).")
        return True # Safe to switch mode







    def activate_mode(self):
        """Called when this mode becomes active."""
        log_info("MultiMode activated.")
        
        self.update_api_key_list()
        self.update_prompt_list()
        self.on_settings_changed() # Apply current settings

        
        # Set the initial state of the global checkboxes
        global_autosave = self.settings_service.get_setting("auto_save_enabled", constants.DEFAULT_AUTO_SAVE_ENABLED)
        self.global_autosave_checkbox.setChecked(global_autosave)
        self.global_continuous_checkbox.setChecked(False) # Default continuous off
        log_debug(f"Set global checkboxes: AutoSave={global_autosave}, Continuous=False")
        

        self._update_action_button_states() # Update buttons based on instance states
        self.status_update.emit("Multi-API Mode Activated.", 3000)

    def update_api_key_list(self):
        """Refreshes API key lists in all instances."""
        log_debug("MultiMode: Updating API key lists for all instances.")
        for instance_widget in self._instance_widgets.values():
            instance_widget.update_api_key_list()

    def update_prompt_list(self):
        """Refreshes prompt lists in all instances."""
        log_debug("MultiMode: Updating prompt lists for all instances.")
        for instance_widget in self._instance_widgets.values():
            instance_widget.update_prompt_list()

    def on_settings_changed(self):
        """Called when global settings are updated."""
        log_debug("MultiMode: Applying settings change to all instances.")
        for instance_widget in self._instance_widgets.values():
            # Tell instance to re-apply relevant settings
            instance_widget.apply_global_settings()

    def load_prompt_from_meta(self, prompt_text: str, target: str):
        """Loads prompt text received from metadata viewer into a specific instance."""
        log_debug(f"MultiMode attempting to load prompt into target: {target}")
        if target and target.startswith("multi_"):
            try:
                target_id_str = target.split('_')[1]
                target_id = int(target_id_str)
                instance_widget = self._instance_widgets.get(target_id)
                if instance_widget:
                    instance_widget.load_prompt(prompt_text)
                    log_info(f"Loaded prompt into Multi Mode instance: {target_id}")
                    # Scroll to the instance?
                    QTimer.singleShot(100, lambda: self.scroll_area.ensureWidgetVisible(instance_widget))
                else:
                    log_warning(f"Target instance ID '{target_id}' not found.")
                    show_info_message(self.parent(), "Load Failed", f"Could not find target instance ID: {target_id}")
            except (IndexError, ValueError) as e:
                log_error(f"Error parsing target '{target}': {e}")
                show_error_message(self.parent(), "Load Error", f"Invalid target format: {target}")
        else:
            log_debug(f"Ignoring prompt load, target '{target}' is not for Multi Mode.")

    # --- Internal Slots/Methods ---
    
    
    @pyqtSlot()
    def _add_instance(self):
        """Adds a new InstanceWidget to the layout, warning the user if exceeding 4 instances."""
        instance_log_prefix = "MultiModeWidget" # For logging consistency
        
        # --- NEW: Warning Logic ---
        # Trigger the warning when the user is about to add the 5th instance.
        if len(self._instance_widgets) == 4:
            log_warning(f"{instance_log_prefix}: User attempting to add a 5th instance. Displaying warning.")
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Icon.Warning)
            msg_box.setWindowTitle("High Instance Count Warning")
            msg_box.setText(
                "You are about to add a fifth API instance.\n\n"
                "Running many instances concurrently at high speeds can increase the risk of hitting API rate limits, which could lead to your API key being temporarily suspended or revoked by the service provider."
            )
            msg_box.setInformativeText("Proceed at your own risk. Are you sure you want to continue?")
            
            # Create custom buttons for clarity
            continue_button = msg_box.addButton("Continue", QMessageBox.ButtonRole.AcceptRole)
            cancel_button = msg_box.addButton(QMessageBox.StandardButton.Cancel)
            msg_box.setDefaultButton(cancel_button) # Make 'Cancel' the default action
            
            msg_box.exec()
            
            # Check which button was clicked
            if msg_box.clickedButton() != continue_button:
                log_info(f"{instance_log_prefix}: User cancelled adding 5th instance.")
                self.status_update.emit("Add instance cancelled.", 3000)
                return # Abort the method
        # --- END: Warning Logic ---

        instance_id = self._next_instance_id
        log_info(f"Attempting to add new instance with ID: {instance_id}")

        all_keys = self.api_key_service.get_key_names()
        used_keys = set(self._assigned_keys.values())
        available_keys = [key for key in all_keys if key not in used_keys]

        assigned_key_name = None
        if available_keys:
            assigned_key_name = available_keys[0]
            log_info(f"Found unused API key '{assigned_key_name}' for new instance {instance_id}.")
        else:
            log_warning("No unused API keys available to assign to new instance.")
            show_warning_message(self, "Cannot Add Instance", "All available API keys are currently assigned to other instances.")
            return

        log_info(f"Assigning API key '{assigned_key_name}' to instance {instance_id}.")
        self._assigned_keys[instance_id] = assigned_key_name

        instance_widget = InstanceWidget(
            instance_id=instance_id,
            settings_service=self.settings_service,
            api_key_service=self.api_key_service,
            prompt_service=self.prompt_service,
            wildcard_resolver=self.wildcard_resolver,
            gemini_handler=self.gemini_handler,
            initial_api_key_name=assigned_key_name,
            parent=self.instance_container_widget
        )
        self._connect_instance_signals(instance_widget)

        self.instance_layout.insertWidget(self.instance_layout.count() - 1, instance_widget)
        self._instance_widgets[instance_id] = instance_widget
        self._next_instance_id += 1

        if self.placeholder_label.isVisible():
            self.placeholder_label.hide()

        self._update_action_button_states()
        QTimer.singleShot(100, lambda: self.scroll_area.ensureWidgetVisible(instance_widget))
        QTimer.singleShot(100, self._update_visible_thumbnails)


    @pyqtSlot(int)
    def _remove_instance(self, instance_id: int, silent: bool = False):
        """Removes the specified InstanceWidget."""
        instance_widget = self._instance_widgets.get(instance_id)
        if not instance_widget:
            log_warning(f"Attempted to remove non-existent instance ID: {instance_id}")
            return

        if instance_widget.is_running():
             if not silent:
                  show_error_message(self, "Cannot Remove", f"Instance #{instance_id} is currently running. Please stop it first.")
             return # Don't remove if running

        log_info(f"Removing instance ID: {instance_id}")

        # Disconnect signals? Usually handled by Qt's parent/child mechanism + deleteLater
        # instance_widget.request_delete.disconnect(self._remove_instance)
        # instance_widget.status_update.disconnect(self.status_update)
        # instance_widget.start_stop_button.toggled.disconnect(self._update_generation_count)


        # Release the assigned key
        if instance_id in self._assigned_keys:
            log_debug(f"Releasing API key '{self._assigned_keys[instance_id]}' from instance {instance_id}")
            del self._assigned_keys[instance_id]
        else:
            log_debug(f"No assigned key found for instance {instance_id} during removal.")

        # Remove from layout and dictionary (existing code)
        self.instance_layout.removeWidget(instance_widget)
        del self._instance_widgets[instance_id]
        instance_widget.deleteLater()

        # Show placeholder if no instances left
        if not self._instance_widgets:
            self.placeholder_label.show()

        self._update_action_button_states()
        QTimer.singleShot(100, self._update_visible_thumbnails)



    @pyqtSlot()
    def _start_all_instances(self):
        """
        Starts generation on all idle instances, staggering the start
        times based on the request_delay setting.
        """
        log_info("Attempting to start all truly idle instances with staggered delay...")
        instance_log_prefix = "MultiModeWidget" # For logging clarity

        # --- Identify Idle Instances ---
        instances_to_start = []
        for instance_widget in self._instance_widgets.values():
            # Check if the instance is neither running nor set to loop continuously
            if not instance_widget.is_running() and not instance_widget.is_looping():
                instances_to_start.append(instance_widget)

        if not instances_to_start:
            log_info("No instances were ready (idle and not looping) to start.")
            self.status_update.emit("No instances were ready to start.", 3000)
            self._update_action_button_states() # Ensure buttons reflect state
            return

        # --- Get Delay and Schedule Starts ---
        # Get delay in milliseconds, ensure a minimum reasonable delay if set to 0 in settings
        request_delay_setting_sec = self.settings_service.get_setting("request_delay", constants.DEFAULT_REQUEST_DELAY)
        stagger_delay_ms = max(50, int(request_delay_setting_sec * 1000)) # Use at least 50ms between starts
        log_info(f"Starting {len(instances_to_start)} idle instance(s) with a stagger delay of {stagger_delay_ms}ms between each.")

        # Schedule the start for each instance with increasing delay
        for i, instance_widget in enumerate(instances_to_start):
            instance_id = instance_widget.get_instance_id()
            scheduled_delay = i * stagger_delay_ms
            log_debug(f"Scheduling start for Instance {instance_id} in {scheduled_delay}ms.")
            # Use lambda to capture the correct instance_widget for the timer's slot
            QTimer.singleShot(scheduled_delay, lambda widget=instance_widget: self._safely_start_instance(widget))

        self.status_update.emit(f"Scheduled start for {len(instances_to_start)} instance(s)...", 3000)
        # Update buttons immediately AFTER scheduling all starts
        # The UI might appear busy before the first instance actually starts generating
        self._update_action_button_states()


    def _safely_start_instance(self, instance_widget: InstanceWidget):
        """Helper method called by QTimer to start a single instance, checking its state first."""
        instance_id = instance_widget.get_instance_id()
        # Double-check if the instance is still idle before starting
        if instance_widget and not instance_widget.is_running() and not instance_widget.is_looping():
            log_info(f"Timer fired: Starting Instance {instance_id}")
            try:
                # Ensure UI reflects 'generating' state briefly before worker starts
                # instance_widget._set_ui_generating(True) # Let start_generation handle this
                instance_widget.start_generation()
            except Exception as e:
                 log_error(f"Error occurred while trying to start Instance {instance_id} via timer: {e}", exc_info=True)
                 # Attempt to reset UI state if start failed
                 try:
                      instance_widget._set_ui_generating(False)
                      instance_widget._update_status_label("Start Error.")
                 except Exception as reset_err:
                      log_error(f"Error resetting UI for Instance {instance_id} after start error: {reset_err}")
        else:
            if instance_widget:
                log_warning(f"Timer fired for Instance {instance_id}, but it's no longer idle (Running: {instance_widget.is_running()}, Looping: {instance_widget.is_looping()}). Skipping start.")
            else:
                 log_warning(f"Timer fired for an instance that no longer exists (ID potentially {instance_id}). Skipping start.")
        # No need to update global buttons here; signals from instance will handle it



    @pyqtSlot()
    def _stop_all_instances(self, force=False): # Keep force flag maybe for shutdown
        """Stops generation and/or cancels loops on all applicable instances."""
        log_info("Attempting to stop all active (running or looping) instances...")
        stopped_count = 0
        instances_to_stop = []

        for instance_widget in self._instance_widgets.values():
             # Check if running OR looping
             # Use the public methods which now handle the logic correctly
             if instance_widget.is_running() or instance_widget.is_looping():
                  instances_to_stop.append(instance_widget)

        if not instances_to_stop:
             self.status_update.emit("No instances were running or looping to stop.", 3000)
             self._update_action_button_states() # Ensure buttons reflect state
             return

        log_info(f"Requesting stop for {len(instances_to_stop)} active instance(s)...")
        for instance_widget in instances_to_stop:
            instance_id = instance_widget.get_instance_id()
            was_looping = instance_widget.is_looping()
            log_debug(f"Requesting stop for instance {instance_id} (Looping: {was_looping})")
            # Call the instance's stop_generation method.
            # This method now handles both cancelling the worker AND
            # ensuring the loop flag is cleared if it was active.
            instance_widget.stop_generation()
            stopped_count += 1
            if force: # Process events more aggressively if forcing (e.g., on close)
                 QApplication.processEvents()
                 time.sleep(0.02)

        self.status_update.emit(f"Requested stop for {stopped_count} instance(s).", 3000)
        # Buttons will update as instances emit generation_finished signals.
        # Call update immediately to reflect any immediate state changes (like loop flag).
        self._update_action_button_states()


    @pyqtSlot()
    def _clear_all_results(self):
        """Clears the result fields of all instances that are not running AND not looping."""
        log_info("Attempting to clear results for all truly idle instances.")
        if not self._instance_widgets: return

        instances_to_clear = []
        for instance_widget in self._instance_widgets.values():
             if not instance_widget.is_running() and not instance_widget.is_looping():
                  instances_to_clear.append(instance_widget)

        if not instances_to_clear:
             show_info_message(self, "Clear Results", "No instances are currently idle (not running and not looping) to clear.")
             return

        reply = QMessageBox.question(self, "Confirm Clear",
                                     f"Are you sure you want to clear the results from {len(instances_to_clear)} idle instance(s)?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            cleared_count = 0
            for instance_widget in instances_to_clear:
                 # Double check state again just before clearing
                 if not instance_widget.is_running() and not instance_widget.is_looping():
                      log_debug(f"Clearing results for instance {instance_widget.get_instance_id()}")
                      instance_widget.result_text_edit.clear()
                      instance_widget.clear_thumbnail() # Use method to clear thumb/set placeholder
                      instance_widget._full_result_pixmap = None # Clear full pixmap too
                      instance_widget._update_status_label("Results cleared.") # Update instance status
                      QTimer.singleShot(2000, lambda w=instance_widget: w._update_status_label("Ready.") if not w.is_running() else None) # Reset status later
                      cleared_count += 1
                 else:
                      log_warning(f"Skipping clear for instance #{instance_widget.get_instance_id()} as its state changed.")

            self.status_update.emit(f"Results cleared for {cleared_count} idle instance(s).", 3000)
        else:
            self.status_update.emit("Clear results cancelled.", 2000)