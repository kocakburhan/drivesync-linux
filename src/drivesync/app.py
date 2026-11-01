"""Thin GTK 4 / libadwaita frontend; synchronization belongs to the daemon."""

import os
from pathlib import Path
import sys
import shutil

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import app_model, config, i18n, init_flow
from .client import DRIVE_URL, StatusClient
from .paths import Paths
from .presentation import ICONS, present, overall_progress
from .state import StateError, StateStore

APP_ID = "io.github.kocakburhan.DriveSync.App"


def button(label, callback):
    widget = Gtk.Button(label=label)
    widget.connect("clicked", lambda _: callback())
    return widget


def margins(widget, size=18):
    for edge in ("top", "bottom", "start", "end"):
        getattr(widget, "set_margin_" + edge)(size)
    return widget


def css_class(widget, name, enabled):
    if enabled and not widget.has_css_class(name):
        widget.add_css_class(name)
    elif not enabled and widget.has_css_class(name):
        widget.remove_css_class(name)


class FolderWidget(Adw.PreferencesRow):
    """Keep widgets alive across status signals so their transitions can finish."""

    def __init__(self, application, folder):
        super().__init__()
        self.application = application
        self.folder = folder
        self.state = {}
        self.model = None
        self.animation = None
        self.flash_timer = None
        self.add_css_class("folder-row")
        layout = margins(Gtk.Box(spacing=12), 12)
        self.icon = Gtk.Image(valign=Gtk.Align.CENTER)
        layout.append(self.icon)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True,
                       valign=Gtk.Align.CENTER)
        self.title_label = Gtk.Label(xalign=0, wrap=True)
        self.subtitle = Gtk.Label(xalign=0, wrap=True)
        self.subtitle.add_css_class("dim-label")
        self.bar = Gtk.ProgressBar()
        self.bar.add_css_class("sync-progress")
        for widget in (self.title_label, self.subtitle, self.bar):
            text.append(widget)
        layout.append(text)
        self.init_button = button(i18n.INIT_START, lambda: application.start_init(self.folder))
        self.init_button.set_valign(Gtk.Align.CENTER)
        layout.append(self.init_button)
        self.init_spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        layout.append(self.init_spinner)
        menu = Gtk.MenuButton(icon_name="view-more-symbolic", valign=Gtk.Align.CENTER)
        popover = Gtk.Popover()
        choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.sync_item = button(i18n.SYNC_NOW, lambda: self.activate_choice(popover,
            lambda: application.sync(self.folder["id"])))
        choices.append(self.sync_item)
        for label, callback in (
                (i18n.OPEN_FOLDER, lambda: application.open_uri(Path(os.path.expanduser(self.folder["local"])).as_uri())),
                (i18n.OPEN_DRIVE, lambda: application.open_uri(DRIVE_URL)),
                (i18n.OPEN_LOGS, lambda: application.open_logs(self.state.get("last_log"))),
                (i18n.FOLDER_EXCLUDES, lambda: application.folder_excludes(self.folder)),
                (i18n.REMOVE_FOLDER, lambda: application.confirm_remove(self.folder))):
            choices.append(button(label, lambda callback=callback: self.activate_choice(popover, callback)))
        popover.set_child(margins(choices, 6))
        menu.set_popover(popover)
        layout.append(menu)
        self.revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                                     transition_duration=300)
        self.revealer.set_child(layout)
        self.set_child(self.revealer)

    @staticmethod
    def activate_choice(popover, callback):
        popover.popdown()
        callback()

    def get_subtitle(self):
        return self.subtitle.get_label()

    def update(self, folder, state, paused, animate):
        self.folder, self.state = folder, state
        model = app_model.folder_row(folder, state)
        previous = self.model
        self.model = model
        self.set_title(model.title)
        self.title_label.set_label(model.title)
        self.subtitle.set_label(model.subtitle + ("\n" + model.hint if model.hint else ""))
        self.icon.set_from_icon_name(self.application.icon_name(model.icon))
        self.sync_item.set_sensitive(model.can_sync and not paused)
        label = init_flow.init_label(folder, state)
        pending = folder["id"] in self.application.init_pending
        self.init_button.set_visible(bool(label))
        self.init_button.set_label(label or i18n.INIT_START)
        self.init_button.set_sensitive(not pending)
        self.init_spinner.set_visible(pending)
        self.init_spinner.set_spinning(pending)
        self.revealer.set_transition_duration(300 if animate else 0)
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN if animate
                                          else Gtk.RevealerTransitionType.NONE)
        css_class(self, "motion", animate)
        css_class(self.icon, "sync-rotate", animate and model.icon == "syncing")
        if not animate or model.icon != "ok":
            self.icon.remove_css_class("status-pop")
        if not animate:
            self.remove_css_class("status-flash")
        if previous and previous.status != model.status and animate:
            if self.flash_timer:
                GLib.source_remove(self.flash_timer)
            self.add_css_class("status-flash")
            def fade():
                self.flash_timer = None
                self.remove_css_class("status-flash")
                return GLib.SOURCE_REMOVE
            self.flash_timer = GLib.timeout_add(80, fade)
            if model.icon == "ok":
                self.icon.add_css_class("status-pop")
        self.bar.set_visible(model.icon == "syncing")
        if self.animation:
            self.animation.pause()
            self.animation = None
        if model.fraction is not None:
            target = max(0, min(1, model.fraction))
            # Leaving activity mode before animating the determinate fraction.
            if previous is None or previous.fraction is None:
                self.bar.set_fraction(0)
            if animate and self.bar.get_fraction() != target:
                self.animation = Adw.TimedAnimation.new(self.bar, self.bar.get_fraction(), target,
                    500, Adw.CallbackAnimationTarget.new(self.bar.set_fraction))
                self.animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
                self.animation.play()
            else:
                self.bar.set_fraction(target)
        else:
            self.bar.set_fraction(0)

    def close(self):
        if self.animation:
            self.animation.pause()
        if self.flash_timer:
            GLib.source_remove(self.flash_timer)
            self.flash_timer = None


class DriveSyncApp(Adw.Application, StatusClient):
    def __init__(self):
        GLib.set_prgname(APP_ID)
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.paths = Paths.from_env()
        self.window = None
        self.rows = []
        self.rows_loaded = False
        self.pulse_timer = None
        self.init_pending = set()
        self.sync_requested = set()
        self.sync_waiting = False
        self.header_key = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        Gtk.Window.set_default_icon_name("io.github.kocakburhan.DriveSync")
        self.css = Gtk.CssProvider()
        installed = Path(__file__).with_name("style.css")
        stylesheet = installed if installed.is_file() else Path(__file__).resolve().parents[2] / "data/style.css"
        self.css.load_from_path(str(stylesheet))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.settings = Gtk.Settings.get_default()
        self.settings_handler = self.settings.connect("notify::gtk-enable-animations", lambda *_: self.refresh())
        self.start_client()
        self.timer = GLib.timeout_add_seconds(60, self.refresh)

    def do_shutdown(self):
        self.close_client()
        GLib.source_remove(self.timer)
        self.clear_motion()
        self.settings.disconnect(self.settings_handler)
        Adw.Application.do_shutdown(self)

    def do_activate(self):
        if not self.get_active_window():
            self.build_window()
        self.window.present()

    def build_window(self):
        self.clear_motion()
        self.rows.clear()
        self.rows_loaded = False
        self.header_key = None
        self.window = Adw.ApplicationWindow(application=self, title=i18n.APP_NAME,
                                           default_width=800, default_height=650)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        layout.append(Adw.HeaderBar())
        self.banner = Adw.Banner(title="", button_label=i18n.REVIEW)
        self.banner.connect("button-clicked", lambda _: self.open_logs())
        layout.append(self.banner)
        self.stack = Gtk.Stack(vexpand=True)
        stopped = Adw.StatusPage(title=i18n.TRAY_STOPPED, icon_name="network-offline-symbolic")
        stopped.set_child(button(i18n.START_SERVICE, self.start_service))
        self.stack.add_named(stopped, "stopped")
        content = margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18))
        header = Gtk.Box(spacing=12)
        self.header_stack = Gtk.Stack(hexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE,
                                      transition_duration=250)
        self.header_pages = []
        for _ in range(2):
            page = Gtk.Box(spacing=8)
            icon = Gtk.Image()
            label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
            page.append(icon)
            page.append(label)
            self.header_stack.add_child(page)
            self.header_pages.append((page, icon, label))
        header.append(self.header_stack)
        self.sync_button = button(i18n.SYNC_NOW, lambda: self.sync(""))
        sync_content = Gtk.Box(spacing=6)
        self.sync_spinner = Gtk.Spinner()
        self.sync_static = Gtk.Image.new_from_icon_name("view-refresh-symbolic")
        sync_content.append(self.sync_spinner)
        sync_content.append(self.sync_static)
        sync_content.append(Gtk.Label(label=i18n.SYNC_NOW))
        self.sync_button.set_child(sync_content)
        header.append(self.sync_button)
        self.pause_button = Gtk.ToggleButton(label=i18n.PAUSE)
        self.pause_button.connect("clicked", self.toggle_pause)
        header.append(self.pause_button)
        self.conflicts_button = button("", self.show_conflicts)
        self.conflicts_button.add_css_class("flat")
        self.conflicts_button.set_valign(Gtk.Align.CENTER)
        conflicts_content = Gtk.Box(spacing=6)
        conflicts_content.append(Gtk.Label(label=i18n.CONFLICT_HISTORY))
        self.conflicts_badge = Gtk.Label()
        self.conflicts_badge.add_css_class("caption")
        self.conflicts_badge.add_css_class("conflict-count")
        conflicts_content.append(self.conflicts_badge)
        self.conflicts_button.set_child(conflicts_content)
        header.append(self.conflicts_button)
        content.append(header)
        self.total_progress = Gtk.Label(xalign=0)
        self.total_progress.add_css_class("dim-label")
        content.append(self.total_progress)
        self.group = Adw.PreferencesGroup(title=i18n.FOLDER_GROUP)
        content.append(self.group)
        content.append(button(i18n.ADD_FOLDER, self.choose_folder))
        bottom = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        for label, callback in ((i18n.SETTINGS, self.preferences), (i18n.LOGS, self.open_logs),
                                (i18n.OPEN_DRIVE, lambda: self.open_uri(DRIVE_URL))):
            bottom.append(button(label, callback))
        content.append(bottom)
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.set_child(content)
        self.stack.add_named(scrolled, "content")
        layout.append(self.stack)
        self.window.set_content(layout)
        self.window.connect("close-request", lambda *_: (self.clear_motion(), False)[1])
        self.refresh()

    def clear_motion(self):
        if self.pulse_timer:
            GLib.source_remove(self.pulse_timer)
            self.pulse_timer = None
        for row in self.rows:
            row.close()

    def pulse(self):
        for row in self.rows:
            if row.model.icon == "syncing" and row.model.fraction is None:
                row.bar.pulse()
        return GLib.SOURCE_CONTINUE

    def refresh(self):
        if not self.window or self.window not in self.get_windows():
            return GLib.SOURCE_CONTINUE
        available = self.status is not None
        self.conflicts_button.set_visible(app_model.recent_conflicts_visible(self.status))
        count = len((self.status or {}).get("recent_conflicts", []))
        self.conflicts_badge.set_label(str(count))
        self.conflicts_button.set_tooltip_text(i18n.RECENT_CONFLICTS.format(count=count))
        self.stack.set_visible_child_name("content" if available else "stopped")
        self.banner.set_revealed(False)
        animate = self.settings.get_property("gtk-enable-animations")
        if not available:
            self.sync_requested.clear()
            self.sync_waiting = False
            self.clear_motion()
            self.sync_spinner.stop()
            return GLib.SOURCE_CONTINUE
        view = present(self.status)
        self.header_stack.set_transition_duration(250 if animate else 0)
        key = (view.icon, app_model.overall_status(self.status))
        if key != self.header_key:
            page, icon, label = next(entry for entry in self.header_pages
                if entry[0] != self.header_stack.get_visible_child())
            icon.set_from_icon_name(self.icon_name(view.icon))
            label.set_label(key[1])
            self.header_stack.set_visible_child(page)
            self.header_key = key
        for _, icon, _ in self.header_pages:
            css_class(icon, "sync-rotate", animate and view.icon == "syncing")
        self.total_progress.set_label(overall_progress(self.status))
        self.total_progress.set_visible(bool(self.total_progress.get_label()))
        if not self.sync_waiting:
            self.sync_requested.intersection_update(folder_id for folder_id, item in
                self.status.get("folders", {}).items() if item.get("status") in ("queued", "syncing"))
        busy = app_model.sync_busy(self.status, self.sync_requested)
        self.sync_spinner.set_visible(busy and animate)
        self.sync_spinner.set_spinning(busy and animate)
        self.sync_static.set_visible(busy and not animate)
        self.pause_button.set_active(view.paused)
        self.pause_button.set_label(i18n.RESUME if view.paused else i18n.PAUSE)
        self.sync_button.set_sensitive(not view.paused)
        summary = app_model.problem_summary(self.status)
        self.banner.set_title(summary)
        self.banner.set_revealed(bool(summary))
        configured = {folder["id"] for folder in self.status.get("folder_config", [])}
        for row in list(self.rows):
            if row.folder["id"] not in configured:
                row.close()
                self.group.remove(row)
                self.rows.remove(row)
        for folder in self.status.get("folder_config", []):
            state = self.status.get("folders", {}).get(folder["id"], {})
            row = next((row for row in self.rows if row.folder["id"] == folder["id"]), None)
            if row is None:
                row = FolderWidget(self, folder)
                if self.rows_loaded and animate:
                    # Reveal after mapping so a newly added folder slides into place.
                    row.revealer.connect("map", lambda widget: widget.set_reveal_child(True))
                else:
                    row.revealer.set_reveal_child(True)
                self.group.add(row)
                self.rows.append(row)
            row.update(folder, state, view.paused, animate)
        self.rows_loaded = True
        pulse = animate and any(row.model.icon == "syncing" and row.model.fraction is None for row in self.rows)
        if pulse and self.pulse_timer is None:
            self.pulse_timer = GLib.timeout_add(100, self.pulse)
        elif not pulse and self.pulse_timer:
            GLib.source_remove(self.pulse_timer)
            self.pulse_timer = None
        return GLib.SOURCE_CONTINUE

    @staticmethod
    def icon_name(key):
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        return next((name for name in ICONS[key] if theme.has_icon(name)), ICONS[key][-1])

    def request_failed(self, message):
        self.sync_requested.clear()
        self.sync_waiting = False
        if self.window:
            self.refresh()
            dialog = Adw.MessageDialog.new(self.window, i18n.ERROR_TITLE, message)
            dialog.add_response("close", i18n.CLOSE)
            dialog.present()

    def toggle_pause(self, _button):
        paused = present(self.status).paused
        self.pause_button.set_active(paused)
        self._request("Resume" if paused else "Pause")

    def sync(self, folder_id):
        self.sync_requested.update(folder["id"] for folder in (self.status or {}).get("folder_config", [])
            if (not folder_id or folder["id"] == folder_id) and app_model.folder_row(folder,
                (self.status or {}).get("folders", {}).get(folder["id"], {})).can_sync)
        self.sync_waiting = True
        self.refresh()
        self._request("SyncNow", GLib.Variant("(s)", (folder_id,)))

    def request_finished(self, method, reply):
        if method == "SyncNow":
            if reply:
                self._request("GetStatus")
            else:
                self.sync_requested.clear()
                self.sync_waiting = False
                self.refresh()
        elif method == "GetStatus":
            self.sync_waiting = False
            self.refresh()

    def open_uri(self, uri):
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error:
            self.request_failed(i18n.REQUEST_REFUSED)

    def show_conflicts(self):
        window = Adw.Window(transient_for=self.window, modal=True, title=i18n.CONFLICT_HISTORY,
                            default_width=700, default_height=500)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        clear = button(i18n.CLEAR, lambda: self.clear_conflicts(window))
        header.pack_end(clear)
        content.append(header)
        group = Adw.PreferencesGroup(description=i18n.CONFLICT_POLICY)
        entries = (self.status or {}).get("recent_conflicts", [])
        clear.set_sensitive(bool(entries))
        if not entries:
            group.add(Gtk.Label(label=i18n.NO_CONFLICTS))
        for entry in reversed(entries):
            winner = {"local": i18n.WINNER_LOCAL, "remote": i18n.WINNER_REMOTE}.get(
                entry.get("winner"), i18n.WINNER_UNKNOWN)
            row = Adw.ActionRow(title=entry["path"], use_markup=False,
                               subtitle=f'{entry.get("folder_name", entry["folder"])} · '
                                        f'{entry["time"]}\n{winner}')
            target = entry.get("folder_local")
            action = button(i18n.OPEN_FOLDER, lambda p=target: self.open_uri(Path(p).as_uri()))
            action.set_sensitive(bool(target) and Path(target).is_absolute())
            action.set_valign(Gtk.Align.CENTER)
            row.add_suffix(action)
            group.add(row)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(margins(group))
        content.append(scroll)
        window.set_content(content)
        window.present()
        return window

    def clear_conflicts(self, window):
        try:
            StateStore(self.paths).clear_conflicts()
            if self.status is not None:
                self.status["recent_conflicts"] = []
            self._request("ReloadConfig")
            self.refresh()
            window.close()
        except (StateError, OSError):
            self.request_failed(i18n.REQUEST_REFUSED)

    def open_logs(self, last_log=None):
        try:
            self.paths.logs.mkdir(parents=True, exist_ok=True, mode=0o700)
            target = Path(last_log) if last_log and Path(last_log).is_file() else self.paths.logs
            self.open_uri(target.as_uri())
        except OSError:
            self.request_failed(i18n.REQUEST_REFUSED)

    def start_service(self):
        try:
            process = Gio.Subprocess.new(["systemctl", "--user", "start", "drivesync.service"],
                                         Gio.SubprocessFlags.NONE)
            def finished(child, result):
                try:
                    child.wait_check_finish(result)
                except GLib.Error:
                    self.request_failed(i18n.DAEMON_FAILED)
            process.wait_check_async(None, finished)
        except GLib.Error:
            self.request_failed(i18n.DAEMON_FAILED)

    def edit_config(self, transform):
        try:
            with config.edit_lock(self.paths):
                candidate = transform(config.load(self.paths))
                config.save(self.paths, candidate)
            self._request("ReloadConfig")
            return True
        except (config.ConfigError, OSError) as error:
            self.request_failed(str(error) if isinstance(error, config.ConfigError) else i18n.REQUEST_REFUSED)
            return False

    def choose_folder(self):
        chooser = Gtk.FileDialog(title=i18n.CHOOSE_FOLDER)
        def chosen(dialog, result):
            try:
                file = dialog.select_folder_finish(result)
                if file and file.get_path():
                    self.ask_remote_name(file.get_path())
                elif file:
                    self.request_failed(i18n.LOCAL_UNSAFE)
            except GLib.Error as error:
                if not (error.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED) or
                        error.matches(Gtk.DialogError.quark(), Gtk.DialogError.CANCELLED)):
                    self.request_failed(i18n.REQUEST_REFUSED)
        chooser.select_folder(self.window, None, chosen)

    def ask_remote_name(self, local):
        dialog = Adw.MessageDialog.new(self.window, i18n.REMOTE_NAME, None)
        entry = Gtk.Entry(text=Path(local).name, activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", i18n.CANCEL)
        dialog.add_response("add", i18n.ADD)
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")
        def response(_, response_id):
            if response_id != "add":
                return
            added = []
            def append(data):
                path = Path(local)
                updated = app_model.new_folder(data, local, entry.get_text(), exists=path.is_dir(),
                    symlink=path.is_symlink(), canonical=str(path.resolve()),
                    configured_roots=[os.path.realpath(os.path.expanduser(f["local"])) for f in data["folders"]])
                added.append(updated["folders"][-1])
                return updated
            if self.edit_config(append):
                self.offer_init(added[0])
        dialog.connect("response", response)
        dialog.present()
        return dialog

    def cli_path(self):
        sibling = Path(sys.argv[0]).resolve().with_name("drivesync-cli")
        if sibling.is_file():
            return str(sibling)
        return shutil.which("drivesync-cli") or str(Path(__file__).resolve().parents[2] / "bin/drivesync-cli")

    def run_background(self, command, done):
        """Keep the application alive until the subprocess has been reaped."""
        self.hold()
        try:
            process = Gio.Subprocess.new(command, Gio.SubprocessFlags.STDOUT_PIPE |
                                         Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error:
            self.release()
            done(False, "", i18n.INIT_LAUNCH_FAILED)
            return
        def finished(child, result):
            try:
                _, output, errors = child.communicate_utf8_finish(result)
                done(child.get_successful(), output, errors)
            except GLib.Error:
                done(False, "", i18n.INIT_LAUNCH_FAILED)
            finally:
                self.release()
        process.communicate_utf8_async(None, None, finished)

    def offer_init(self, folder):
        dialog = Adw.MessageDialog.new(self.window, i18n.INIT_OFFER,
                                       i18n.INIT_OFFER_BODY.format(name=folder["remote_name"]))
        dialog.add_response("cancel", i18n.CANCEL)
        dialog.add_response("preview", i18n.INIT_START)
        dialog.set_response_appearance("preview", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _, response: self.start_init(folder) if response == "preview" else None)
        dialog.present()
        return dialog

    def start_init(self, folder):
        if folder["id"] in self.init_pending:
            return
        self.init_pending.add(folder["id"])
        self.refresh()
        dialog = Adw.MessageDialog.new(self.window, i18n.INIT_PREVIEW, None)
        spinner = Gtk.Spinner(spinning=True, height_request=32)
        dialog.set_extra_child(spinner)
        dialog.add_response("cancel", i18n.CANCEL)
        dialog.set_close_response("cancel")
        cancelled = []
        dialog.connect("response", lambda *_: cancelled.append(True))
        dialog.present()
        def previewed(success, output, errors):
            self.init_pending.discard(folder["id"])
            self.refresh()
            was_cancelled = bool(cancelled)
            dialog.close()
            if was_cancelled or self.window not in self.get_windows():
                return
            if not success:
                self.request_failed(errors.strip() or i18n.DRY_FAILED)
                return
            try:
                plan = init_flow.parse_plan(output)
            except ValueError as error:
                self.request_failed(str(error))
                return
            self.show_init_summary(folder, plan)
        self.run_background(init_flow.preview_command(self.cli_path(), folder["id"]), previewed)

    def show_init_summary(self, folder, plan):
        dialog = Adw.MessageDialog.new(self.window, folder["remote_name"], plan.summary)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        details = Gtk.Expander(label=i18n.INIT_DETAILS)
        scroll = Gtk.ScrolledWindow(min_content_height=100, max_content_height=220,
                                   propagate_natural_height=True)
        scroll.set_child(Gtk.Label(label="\n".join(plan.actions), xalign=0, selectable=True, wrap=True))
        details.set_child(scroll)
        content.append(details)
        typed = Gtk.Entry()
        if plan.deletes:
            warning = Gtk.Label(label=i18n.INIT_WARNING.format(name=folder["remote_name"]), wrap=True, xalign=0)
            warning.add_css_class("error")
            content.append(warning)
            content.append(typed)
        if plan.unknown:
            content.append(Gtk.Label(label=i18n.INIT_UNKNOWN.format(count=plan.unknown), wrap=True))
        dialog.set_extra_child(content)
        dialog.add_response("cancel", i18n.CANCEL)
        dialog.add_response("start", i18n.INIT_CONFIRM)
        dialog.set_response_appearance("start", Adw.ResponseAppearance.DESTRUCTIVE if plan.deletes
                                       else Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        def ready(*_):
            dialog.set_response_enabled("start", plan.can_start(folder["remote_name"], typed.get_text()))
        typed.connect("changed", ready)
        ready()
        def response(_, choice):
            if choice == "start" and plan.can_start(folder["remote_name"], typed.get_text()):
                self.launch_init(folder)
        dialog.connect("response", response)
        dialog.present()
        return dialog, typed

    def launch_init(self, folder):
        self.init_pending.add(folder["id"])
        self.refresh()
        def launched(success, output, errors):
            self.init_pending.discard(folder["id"])
            self.refresh()
            if not success:
                self.request_failed(errors.strip() or i18n.INIT_LAUNCH_FAILED)
            else:
                self._request("GetStatus")
        kind = "resync" if folder.get("initialized") else "init"
        self.run_background(init_flow.submit_command(self.cli_path(), folder["id"], kind), launched)

    def confirm_remove(self, folder):
        dialog = Adw.MessageDialog.new(self.window, i18n.REMOVE_HEADING,
                                       i18n.REMOVE_BODY.format(name=folder["remote_name"]))
        dialog.add_response("cancel", i18n.CANCEL)
        dialog.add_response("remove", i18n.REMOVE_FOLDER)
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _, response: self.edit_config(
            lambda data: app_model.remove_folder(data, folder["id"])) if response == "remove" else None)
        dialog.present()

    def folder_excludes(self, folder):
        try:
            data = config.load(self.paths)
            current = config.folder_by_id(data, folder["id"])
        except (config.ConfigError, OSError) as error:
            self.request_failed(str(error))
            return None
        window = Adw.PreferencesWindow(transient_for=self.window, modal=True,
                                       title=i18n.FOLDER_EXCLUDES_TITLE.format(name=current["remote_name"]),
                                       default_width=600, default_height=500)
        page = Adw.PreferencesPage()
        rules = Adw.PreferencesGroup(
            title=i18n.EXCLUDES,
            description=i18n.FOLDER_EXCLUDES_NOTE + "\n" + i18n.EXCLUDES_WARNING)
        page.add(rules)
        entries = []
        def add_rule(text=""):
            row = Adw.ActionRow(title=i18n.EXCLUDE_PATTERN)
            entry = Gtk.Entry(text=text, hexpand=True, valign=Gtk.Align.CENTER)
            row.add_suffix(entry)
            def remove():
                entries.remove(entry)
                rules.remove(row)
            remove_button = button(i18n.REMOVE_FOLDER, remove)
            remove_button.set_valign(Gtk.Align.CENTER)
            row.add_suffix(remove_button)
            entries.append(entry)
            rules.add(row)
        for pattern in current.get("excludes", []):
            add_rule(pattern)
        rules.set_header_suffix(button(i18n.ADD_EXCLUDE, add_rule))
        save_group = Adw.PreferencesGroup()
        def save():
            if self.edit_config(lambda latest: app_model.folder_excludes_edit(
                    latest, folder["id"], [entry.get_text() for entry in entries])):
                window.close()
        save_group.add(button(i18n.SAVE, save))
        page.add(save_group)
        window.add(page)
        window.present()
        return window, entries, add_rule, save

    def preferences(self):
        try:
            data = config.load(self.paths)
        except (config.ConfigError, OSError) as error:
            self.request_failed(str(error))
            return
        window = Adw.PreferencesWindow(transient_for=self.window, modal=True, title=i18n.SETTINGS,
                                       default_width=650, default_height=650)
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        page.add(group)
        intervals = [5, 10, 15, 30]
        if data["poll_interval_minutes"] not in intervals:
            intervals = sorted(intervals + [data["poll_interval_minutes"]])
        poll = Adw.ComboRow(title=i18n.POLL_INTERVAL,
                           model=Gtk.StringList.new([str(n) for n in intervals]))
        poll.set_selected(intervals.index(data["poll_interval_minutes"]))
        group.add(poll)
        def spin(title, value, maximum):
            row = Adw.ActionRow(title=title)
            widget = Gtk.SpinButton.new_with_range(0, max(maximum, value), 1)
            widget.set_value(value)
            widget.set_valign(Gtk.Align.CENTER)
            row.add_suffix(widget)
            row.set_activatable_widget(widget)
            group.add(row)
            return widget
        debounce = spin(i18n.DEBOUNCE, data["debounce_seconds"], 3600)
        max_delete = spin(i18n.MAX_DELETE, data["max_delete_percent"], 100)
        network = Adw.PreferencesGroup(description=i18n.BWLIMIT_HINT)
        page.add(network)
        bandwidth = Adw.EntryRow(title=i18n.BWLIMIT)
        bandwidth.set_text(data.get("bwlimit", ""))
        network.add(bandwidth)
        on_metered = Adw.SwitchRow(title=i18n.PAUSE_ON_METERED, active=data.get("pause_on_metered", True))
        on_battery = Adw.SwitchRow(title=i18n.PAUSE_ON_BATTERY, active=data.get("pause_on_battery", False))
        network.add(on_metered)
        network.add(on_battery)
        rules = Adw.PreferencesGroup(title=i18n.EXCLUDES, description=i18n.EXCLUDES_WARNING)
        page.add(rules)
        entries = []
        def add_rule(text=""):
            row = Adw.ActionRow(title=i18n.EXCLUDE_PATTERN)
            entry = Gtk.Entry(text=text, hexpand=True, valign=Gtk.Align.CENTER)
            row.add_suffix(entry)
            def remove():
                entries.remove(entry)
                rules.remove(row)
            remove_button = button(i18n.REMOVE_FOLDER, remove)
            remove_button.set_valign(Gtk.Align.CENTER)
            row.add_suffix(remove_button)
            entries.append(entry)
            rules.add(row)
        for pattern in data["excludes"]:
            add_rule(pattern)
        rules.set_header_suffix(button(i18n.ADD_EXCLUDE, add_rule))
        save_group = Adw.PreferencesGroup()
        def save():
            if self.edit_config(lambda latest: app_model.settings_edit(latest,
                    intervals[poll.get_selected()], debounce.get_value(), max_delete.get_value(),
                    [entry.get_text() for entry in entries], bwlimit=bandwidth.get_text(),
                    pause_on_battery=on_battery.get_active(), pause_on_metered=on_metered.get_active())):
                window.close()
        save_group.add(button(i18n.SAVE, save))
        page.add(save_group)
        window.add(page)
        window.present()


def main():
    return DriveSyncApp().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
