"""Display-gated GTK4 smoke; always run in a separate process from GTK3 tests."""

import json
from pathlib import Path
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from drivesync import app, config, i18n, init_flow


def buttons(widget):
    found = []
    child = widget.get_first_child() if widget is not None else None
    while child is not None:
        if isinstance(child, app.Gtk.Button):
            found.append(child)
        found.extend(buttons(child))
        child = child.get_next_sibling()
    return found


def main():
    app.Gtk.init_check()
    if app.Gdk.Display.get_default() is None:
        print("GTK4 görüntüye bağlanamadı")
        return 77
    application = app.DriveSyncApp()
    assert application.get_application_id() == app.APP_ID
    assert app.GLib.get_prgname() == app.APP_ID
    application.set_flags(app.Gio.ApplicationFlags.NON_UNIQUE)
    with patch.object(app.Gio, "bus_watch_name", return_value=1), patch.object(app.Gio, "bus_unwatch_name"):
        application.register(None)
        application.activate()
        window = application.window
        assert application.stack.get_visible_child_name() == "stopped"
        assert application.stack.get_visible_child().get_title() == i18n.TRAY_STOPPED
        application.activate()
        assert application.window is window
        folder = dict(id="example", local=str(application.paths.config_dir / "Example"),
                      remote_name="Example", enabled=True, initialized=False)
        config.save(application.paths, dict(config.defaults(), folders=[folder]))
        application._accept(json.dumps(dict(daemon={}, folder_config=[folder],
                                           folders={"example": dict(status="not_initialized")})))
        assert application.stack.get_visible_child_name() == "content"
        assert not application.conflicts_button.get_visible()
        assert len(application.rows) == 1
        assert application.rows[0].init_button.get_visible()
        assert application.rows[0].init_button.get_label() == i18n.INIT_START
        from drivesync.init_flow import Plan
        plan = Plan(2, 3, 1, 0, ("delete old.txt",))
        summary, typed = application.show_init_summary(folder, plan)
        assert summary.get_body() == "Drive'a 2 dosya, bilgisayara 3 dosya, 1 silme"
        assert not summary.get_response_enabled("start")
        typed.set_text("wrong")
        assert not summary.get_response_enabled("start")
        typed.set_text("Example")
        assert summary.get_response_enabled("start")
        with patch.object(application, "launch_init") as launch:
            summary.response("start")
            launch.assert_called_once_with(folder)
        # Large first uploads show the estimate and exclude buttons that re-run the preview.
        large = init_flow.Plan(2, 3, 0, 0, (), size=dict(
            bytes=int(23.4 * 1024 ** 3), files=142783, estimated_seconds=54000,
            assumed_bytes_per_second=1048576, large=True,
            largest_subfolders=[dict(name="Videos", bytes=300, files=1)]))
        large_dialog, _ = application.show_init_summary(folder, large)
        exclude = [item for item in buttons(large_dialog.get_extra_child())
                   if item.get_label() == i18n.EXCLUDE_SUBFOLDER]
        assert len(exclude) == 1
        with patch.object(application, "edit_config", return_value=True) as edit, \
             patch.object(application, "start_init") as restart:
            exclude[0].emit("clicked")
            before = config.load(application.paths)
            saved = edit.call_args.args[0](before)
            # Only this folder's own excludes change (anchored), never the global list.
            assert config.folder_by_id(saved, folder["id"])["excludes"][-1] == "/Videos/"
            assert saved["excludes"] == before["excludes"]
            restart.assert_called_once_with(folder)
        # Preview runs asynchronously, exposing a spinner while the CLI is busy.
        with patch.object(application, "run_background") as background:
            application.start_init(folder)
            assert application.rows[0].init_spinner.get_spinning()
            assert not application.rows[0].init_button.get_sensitive()
            command, done = background.call_args.args
            assert command[-4:] == ["init", "example", "--dry-run", "--json"]
            with patch.object(application, "show_init_summary") as show:
                done(True, json.dumps(dict(to_remote=2, to_local=3, deletes=0, unknown=0, actions=[])), "")
                show.assert_called_once()
            assert not application.rows[0].init_spinner.get_spinning()
        offer = application.offer_init(folder)
        assert offer.get_heading() == i18n.INIT_OFFER
        offer.response("cancel")
        # Keep the same row alive while stats arrive; motion follows Gtk.Settings.
        row = application.rows[0]
        application.settings.set_property("gtk-enable-animations", True)
        application.status["folders"]["example"] = dict(status="syncing")
        application.refresh()
        assert application.rows[0] is row
        assert row.bar.get_visible()
        assert not row.init_button.get_visible()
        assert row.icon.has_css_class("sync-rotate")
        assert application.sync_spinner.get_spinning()
        assert application.pulse_timer is not None
        from drivesync.progress import parse_progress
        application.status["folders"]["example"]["progress"] = parse_progress(
            "1 GiB / 2 GiB, 50%, 1 MiB/s, ETA 1h (xfr#1/1000)")
        application.refresh()
        assert application.pulse_timer is None
        assert "%50" in row.get_subtitle()
        application.settings.set_property("gtk-enable-animations", False)
        application.refresh()
        assert not application.sync_spinner.get_spinning()
        assert application.sync_static.get_visible()
        assert not row.icon.has_css_class("sync-rotate")
        assert row.bar.get_fraction() == .5
        assert application.header_stack.get_transition_duration() == 0
        application.status["folders"]["example"] = dict(status="ok")
        application.refresh()
        assert not row.bar.get_visible()
        assert not row.icon.has_css_class("status-pop")
        application.settings.set_property("gtk-enable-animations", True)
        application._request = Mock()
        # _accept deserializes a separate snapshot, not the original folder dict.
        application.status["folder_config"][0]["initialized"] = True
        application.sync("")
        assert application.sync_spinner.get_spinning()
        application.status["folders"]["example"] = dict(status="queued")
        application.request_finished("GetStatus", "")
        assert application.sync_spinner.get_spinning()
        application.status["folders"]["example"] = dict(status="syncing")
        application.refresh()
        assert application.sync_spinner.get_spinning()
        application.status["folders"]["example"] = dict(status="ok")
        application.refresh()
        assert not application.sync_spinner.get_spinning()
        assert row.icon.has_css_class("status-pop")
        assert row.has_css_class("status-flash")
        # Signals and GetStatus snapshots also drive activity without a local request.
        for state in ("queued", "syncing"):
            application._accept(json.dumps(dict(application.status,
                folders={"example": dict(status=state)})))
            assert application.sync_spinner.get_spinning()
        application.status["folders"]["example"] = dict(status="ok")
        application.refresh()
        assert not application.sync_spinner.get_spinning()
        application.sync("")
        assert application.sync_spinner.get_spinning()
        application.request_failed(i18n.NO_DAEMON)
        assert not application.sync_spinner.get_spinning()
        assert not application.sync_requested and not application.sync_waiting
        application.sync("")
        application.request_finished("SyncNow", False)
        assert not application.sync_spinner.get_spinning()
        assert not application.sync_requested and not application.sync_waiting
        added = dict(folder, id="added", remote_name="Added")
        application.status["folder_config"].append(added)
        application.refresh()
        assert len(application.rows) == 2
        assert application.rows[1].revealer.get_transition_duration() == 300
        application.status["folder_config"].pop()
        application.refresh()
        assert len(application.rows) == 1
        application.pause_button.emit("clicked")
        application._request.assert_called_with("Pause")
        application.preferences()
        dialog, entries, add_rule, save = application.folder_excludes(folder)
        assert dialog.get_title() == i18n.FOLDER_EXCLUDES_TITLE.format(name="Example")
        add_rule("*.psd")
        entries[0].set_text("*.psd")
        save()
        assert config.load(application.paths)["folders"][0]["excludes"] == ["*.psd"]
        application.ask_remote_name(folder["local"])
        new_local = application.paths.config_dir / "New Folder"
        new_local.mkdir()
        added_dialog = application.ask_remote_name(str(new_local))
        with patch.object(application, "offer_init") as offer:
            added_dialog.response("add")
            offer.assert_called_once()
            added_folder = offer.call_args.args[0]
            assert added_folder["local"] == str(new_local)
            assert not added_folder["initialized"]
            assert added_folder in config.load(application.paths)["folders"]
        with patch.object(application, "run_background") as background:
            application.launch_init(folder)
            command, done = background.call_args.args
            assert "systemd-run" not in command
            assert command[-4:] == ["init", "example", "--yes", "--json"]
            done(True, "", "")
            application._request.assert_called_with("GetStatus")
        application.confirm_remove(folder)
        application.status["folders"]["example"] = dict(status="error", needs_resync=True)
        application.refresh()
        assert row.init_button.get_visible()
        assert row.init_button.get_label() == i18n.INIT_RESYNC
        application.status["folders"]["example"] = dict(status="blocked_delete")
        application.refresh()
        assert application.banner.get_revealed()
        from drivesync.state import StateStore
        history = [dict(path="nested/rapor.txt", folder="example", folder_name="Example",
                        folder_local=folder["local"], time="2026-11-01T12:00:00+03:00", winner="remote")]
        StateStore(application.paths).update(lambda value: value.update(recent_conflicts=history))
        application.status["recent_conflicts"] = history
        application.refresh()
        assert application.conflicts_button.get_visible()
        assert application.conflicts_button.has_css_class("flat")
        assert application.conflicts_button.get_parent() is application.sync_button.get_parent()
        assert application.conflicts_badge.get_label() == "1"
        dialog = application.show_conflicts()
        assert dialog.get_visible()
        application.clear_conflicts(dialog)
        assert StateStore(application.paths).read()["recent_conflicts"] == []
        assert not application.conflicts_button.get_visible()
        application.status["folders"]["example"] = dict(status="ok")
        application.sync("")
        assert application.sync_spinner.get_spinning()
        application._vanished()
        assert application.stack.get_visible_child_name() == "stopped"
        assert not application.sync_spinner.get_spinning()
        assert not application.sync_requested and not application.sync_waiting
        # No daemon stop method is ever called when closing the application.
        for top in list(app.Gtk.Window.get_toplevels()):
            top.destroy()
        application.close_client()
        application.clear_motion()
        application.settings.disconnect(application.settings_handler)
        app.GLib.source_remove(application.timer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
