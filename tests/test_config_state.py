from copy import deepcopy
import json
import os
from pathlib import Path
from unittest.mock import patch

from support import IsolatedTest
from drivesync import config, filters, i18n
from drivesync.paths import Paths
from drivesync.state import BusyError, StateError, StateStore, execution_lock


class ConfigStateTests(IsolatedTest):
    def test_xdg_paths(self):
        self.assertEqual(self.paths.config, self.root / "config/drivesync/config.json")
        self.assertEqual(self.paths.state, self.root / "state/drivesync/state.json")
        self.assertEqual(self.paths.workdir, self.root / "data/drivesync/bisync")

    def test_default_folders_and_safe_flags(self):
        self.paths.config.unlink()
        value = config.load(self.paths)
        self.assertEqual(value["folders"], [])
        self.assertEqual(value["root_folder"], "Linux-PC")
        self.assertEqual(value["remote"], "gdrive:")
        self.assertTrue(Path(value["rclone_path"]).is_absolute())
        self.assertEqual(self.paths.config.stat().st_mode & 0o777, 0o600)

    def test_missing_defaults_and_unknown_keys_roundtrip(self):
        value = {"extension": {"preserve": True}, "folders": self.config["folders"]}
        value["folders"][0]["extension"] = 42
        del value["folders"][0]["initialized"]
        config.save(self.paths, value)
        loaded = config.load(self.paths)
        self.assertEqual(loaded["extension"], {"preserve": True})
        self.assertEqual(loaded["folders"][0]["extension"], 42)
        self.assertFalse(loaded["folders"][0]["initialized"])
        self.assertEqual(loaded["poll_interval_minutes"], 5)

    def test_invalid_config_rejected(self):
        for field, value in [("version", 2), ("paused", "false"), ("excludes", ["foo\n+ **"]),
                             ("poll_interval_minutes", 0), ("max_delete_percent", 101),
                             ("remote", "gdrive:other/"), ("root_folder", "../other"),
                             ("debounce_seconds", float("nan")), ("folders", None)]:
            with self.subTest(field=field):
                data = deepcopy(self.config)
                data[field] = value
                with self.assertRaises(config.ConfigError):
                    config.validate(data)

    def test_transfer_defaults_and_validation(self):
        value = config.validate({"folders": self.config["folders"]})
        self.assertEqual((value["transfers"], value["init_transfers"], value["checkers"]), (4, 8, 8))
        self.assertEqual(value["init_extra_flags"],
                         ["--drive-pacer-min-sleep", "10ms", "--drive-pacer-burst", "200"])
        for field in ("transfers", "init_transfers", "checkers"):
            for bad in (True, False, 0, -1, 1.5, "8", None):
                with self.subTest(field=field, bad=bad), self.assertRaises(config.ConfigError):
                    config.validate(dict(self.config, **{field: bad}))
        for bad in (None, "--flag", {}, [1], [True], [""], ["--flag\nvalue"]):
            with self.subTest(bad=bad), self.assertRaises(config.ConfigError):
                config.validate(dict(self.config, init_extra_flags=bad))
        self.assertEqual(config.validate(dict(self.config, init_extra_flags=[]))["init_extra_flags"], [])

    def test_duplicate_and_unsafe_folders(self):
        for changes in ({"id": "../escape"}, {"local": "relative"}, {"remote_name": "../unsafe"},
                        {"enabled": 1}, {"local": "/"}):
            with self.subTest(changes=changes):
                data = deepcopy(self.config)
                data["folders"][0].update(changes)
                with self.assertRaises(config.ConfigError):
                    config.validate(data)
        data = deepcopy(self.config)
        data["folders"].append(deepcopy(data["folders"][0]))
        with self.assertRaises(config.ConfigError):
            config.validate(data)

    def test_overlapping_roots_rejected(self):
        data = deepcopy(self.config)
        data["folders"].append({"id": "child", "local": data["folders"][0]["local"] + "/child",
                                "remote_name": "Child"})
        with self.assertRaises(config.ConfigError):
            config.validate(data)

    def test_malformed_json_is_clear_and_does_not_echo_content(self):
        self.paths.config.write_text('{"token":"secret"')
        with self.assertRaises(config.ConfigError) as error:
            config.load(self.paths)
        self.assertNotIn("secret", str(error.exception))

    def test_filters_protect_sentinel_and_recursive_directories(self):
        content = filters.render(["node_modules/", "*.tmp", "**", ".drivesync-check"])
        self.assertEqual(content.splitlines()[0], "+ /.drivesync-check")
        self.assertIn("- **/node_modules/**\n- node_modules/**", content)
        self.assertIn("- *.tmp", content)

    def test_filter_change_marks_initialized_even_disabled_folders(self):
        digest = self.prepare(initialized=True)
        self.config["excludes"].append("*.new")
        self.config["folders"][0]["enabled"] = False
        changed = filters.prepare(self.paths, self.config, self.store)["docs"]
        item = self.store.read()["folders"]["docs"]
        self.assertNotEqual(digest, changed)
        self.assertTrue(item["needs_resync"])
        self.assertEqual(item["status"], "disabled")
        self.assertEqual(item["last_error"], i18n.FILTER_CHANGED)

    def test_same_filters_and_pair_do_not_require_resync(self):
        self.prepare(initialized=True)
        filters.prepare(self.paths, self.config, self.store)
        self.assertFalse(self.store.read()["folders"]["docs"]["needs_resync"])

    def test_reconcile_leaves_external_syncing_folder_untouched(self):
        for initialized in (False, True):
            with self.subTest(initialized=initialized):
                self.prepare(initialized=initialized)
                self.store.folder("docs", status="syncing", last_error="keep")
                before = self.store.read()["folders"]["docs"]
                with self.external_folder_lock():
                    filters.prepare(self.paths, self.config, self.store)
                    self.assertEqual(self.store.read()["folders"]["docs"], before)

    def test_filter_change_is_deferred_for_externally_locked_folder(self):
        digest = self.prepare(initialized=True)
        self.store.folder("docs", status="syncing")
        self.config["excludes"].append("*.changed")
        with self.external_folder_lock():
            filters.prepare(self.paths, self.config, self.store)
            self.assertEqual(self.store.read()["folders"]["docs"]["status"], "syncing")
            self.assertEqual(self.store.read()["filters_sha256"], digest)
        filters.prepare(self.paths, self.config, self.store)
        item = self.store.read()["folders"]["docs"]
        self.assertEqual(item["status"], "error")
        self.assertTrue(item["needs_resync"])

    def test_state_read_preserves_active_uninitialized_resync(self):
        self.store.folder("docs", status="syncing", needs_resync=True)
        with self.external_folder_lock():
            self.store.daemon(status="idle")
            self.assertEqual(self.store.read()["folders"]["docs"]["status"], "syncing")

    def test_changed_target_requires_resync(self):
        self.prepare(initialized=True)
        self.config["folders"][0]["remote_name"] = "Different"
        filters.prepare(self.paths, self.config, self.store)
        self.assertTrue(self.store.read()["folders"]["docs"]["needs_resync"])

    def test_unknown_baseline_requires_resync(self):
        self.config["folders"][0]["initialized"] = True
        config.save(self.paths, self.config)
        filters.prepare(self.paths, self.config, self.store)
        self.assertTrue(self.store.read()["folders"]["docs"]["needs_resync"])

    def test_state_transactions_preserve_other_writers(self):
        other = StateStore(self.paths)
        self.store.folder("a", last_error="a")
        other.folder("b", last_error="b")
        self.store.daemon(status="paused")
        value = other.read()
        self.assertEqual(value["folders"]["a"]["last_error"], "a")
        self.assertEqual(value["folders"]["b"]["last_error"], "b")
        self.assertEqual(value["daemon"]["status"], "paused")
        self.assertEqual(self.paths.state.stat().st_mode & 0o777, 0o600)

    def test_daemon_last_error_defaults_for_new_and_existing_state(self):
        self.assertIsNone(self.store.read()["daemon"]["last_error"])
        self.store.daemon(status="offline")
        value = json.loads(self.paths.state.read_text())
        del value["daemon"]["last_error"]
        self.paths.state.write_text(json.dumps(value))
        self.assertIsNone(self.store.read()["daemon"]["last_error"])
        self.store.daemon(last_error=i18n.PROBE_ERROR)
        self.assertEqual(self.store.read()["daemon"]["last_error"], i18n.PROBE_ERROR)

    def test_atomic_failure_preserves_previous_state(self):
        self.store.folder("docs", last_error="previous")
        with patch("drivesync.paths.os.replace", side_effect=OSError("simulated")):
            with self.assertRaises(OSError):
                self.store.folder("docs", last_error="new")
        self.assertEqual(self.store.read()["folders"]["docs"]["last_error"], "previous")
        self.assertFalse(list(self.paths.state_dir.glob(".state.json.*")))

    def test_corrupt_state_refuses_to_overwrite(self):
        self.paths.state.write_text("{broken}")
        with self.assertRaises(StateError):
            self.store.folder("docs", status="ok")
        self.assertEqual(self.paths.state.read_text(), "{broken}")

    def test_execution_lock_serializes_all_folders(self):
        with execution_lock(self.paths, "docs"):
            with self.assertRaises(BusyError):
                with execution_lock(self.paths, "other"):
                    self.fail("concurrent execution")
        with execution_lock(self.paths, "docs"):
            pass

    def test_reenabling_folder_preserves_resync_requirement(self):
        self.prepare(initialized=True)
        self.config["folders"][0]["enabled"] = False
        self.config["excludes"].append("*.changed")
        filters.prepare(self.paths, self.config, self.store)
        self.config["folders"][0]["enabled"] = True
        filters.prepare(self.paths, self.config, self.store)
        item = self.store.read()["folders"]["docs"]
        self.assertEqual(item["status"], "error")
        self.assertTrue(item["needs_resync"])

    def test_state_loading_heals_uninitialized_resync_without_losing_error(self):
        self.store.folder("docs", status="error", needs_resync=True,
                          last_error="ERROR : directory not found")
        item = StateStore(self.paths).read()["folders"]["docs"]
        self.assertFalse(item["needs_resync"])
        self.assertEqual(item["status"], "not_initialized")
        self.assertEqual(item["last_error"], "ERROR : directory not found")
        filters.prepare(self.paths, self.config, self.store)
        item = self.store.read()["folders"]["docs"]
        self.assertFalse(item["needs_resync"])
        self.assertEqual(item["status"], "not_initialized")

    def test_folder_excludes_validate_and_merge(self):
        value = deepcopy(self.config)
        value["folders"][0]["excludes"] = ["*.psd", "*.tmp"]
        checked = config.validate(value)
        self.assertEqual(config.effective_excludes(checked, checked["folders"][0]),
                         checked["excludes"] + ["*.psd"])
        for bad in ("x", [""], [" a"], [1], ["a\nb"]):
            value["folders"][0]["excludes"] = bad
            with self.assertRaises(config.ConfigError):
                config.validate(value)

    def add_second_folder(self):
        other = self.root / "Other"
        other.mkdir()
        self.config["folders"].append({"id": "other", "local": str(other), "remote_name": "Other",
                                       "enabled": True, "initialized": False})
        with execution_lock(self.paths):
            filters.prepare(self.paths, self.config, self.store)
        for folder in self.config["folders"]:
            folder["initialized"] = True
        config.save(self.paths, self.config)
        self.store.folder("docs", status="ok")
        self.store.folder("other", status="ok")

    def test_per_folder_filter_files(self):
        self.add_second_folder()
        self.config["folders"][0]["excludes"] = ["*.psd"]
        digests = filters.prepare(self.paths, self.config, self.store)
        docs = self.paths.filters_for("docs").read_text()
        other = self.paths.filters_for("other").read_text()
        self.assertEqual(docs.splitlines()[0], "+ /.drivesync-check")
        self.assertIn("- *.psd", docs)
        self.assertNotIn("- *.psd", other)
        self.assertEqual(other, filters.render(self.config["excludes"]))
        self.assertNotEqual(digests["docs"], digests["other"])
        self.assertFalse(self.paths.legacy_filters.exists())
        del self.config["folders"][1]
        filters.prepare(self.paths, self.config, self.store)
        self.assertFalse(self.paths.filters_for("other").exists())

    def test_folder_excludes_change_resyncs_only_that_folder(self):
        self.add_second_folder()
        self.config["folders"][0]["excludes"] = ["*.psd"]
        filters.prepare(self.paths, self.config, self.store)
        folders = self.store.read()["folders"]
        self.assertTrue(folders["docs"]["needs_resync"])
        self.assertFalse(folders["other"]["needs_resync"])
        self.assertEqual(folders["other"]["status"], "ok")

    def test_global_change_resyncs_all_initialized(self):
        self.add_second_folder()
        self.config["excludes"].append("*.new")
        filters.prepare(self.paths, self.config, self.store)
        folders = self.store.read()["folders"]
        self.assertTrue(folders["docs"]["needs_resync"] and folders["other"]["needs_resync"])

    def test_migration_from_global_hash_does_not_force_resync(self):
        self.add_second_folder()
        legacy = filters.digest_of(self.config["excludes"])
        def downgrade(value):
            value["filters_sha256"] = legacy
            for item in value["folders"].values():
                del item["filters_sha256"]
        self.store.update(downgrade)
        self.paths.legacy_filters.write_text(filters.render(self.config["excludes"]))
        filters.prepare(self.paths, self.config, self.store)
        folders = self.store.read()["folders"]
        self.assertFalse(folders["docs"]["needs_resync"] or folders["other"]["needs_resync"])
        self.assertEqual(folders["docs"]["filters_sha256"], legacy)
        self.assertFalse(self.paths.legacy_filters.exists())
        # A folder that already had own patterns at migration time does change.
        self.store.update(downgrade)
        self.config["folders"][0]["excludes"] = ["*.psd"]
        filters.prepare(self.paths, self.config, self.store)
        folders = self.store.read()["folders"]
        self.assertTrue(folders["docs"]["needs_resync"])
        self.assertFalse(folders["other"]["needs_resync"])
