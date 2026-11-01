from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from support import IsolatedTest, ROOT
from drivesync import app_model, config, presentation, rclone
from drivesync.progress import LogProgress, parse_progress
from drivesync.state import StateStore


STATS = ("2026/11/01 03:03:15 INFO  :     3.170 GiB / 9.169 GiB, 35%, "
         "1.158 MiB/s, ETA 1h28m22s (xfr#468/3604)")


class ParserTests(unittest.TestCase):
    def test_real_stats_line(self):
        value = parse_progress(STATS)
        self.assertEqual(value, dict(bytes_done=round(3.170 * 1024**3),
            bytes_total=round(9.169 * 1024**3), percent=35, speed=1.158 * 1024**2,
            eta_seconds=5302, files_done=468, files_total=3604))

    def test_units_unknown_eta_missing_files_and_transferred_prefix(self):
        for unit, factor in (("B", 1), ("KiB", 1024), ("MiB", 1024**2), ("GiB", 1024**3)):
            with self.subTest(unit=unit):
                value = parse_progress(f"INFO : Transferred: 1 {unit} / 2 {unit}, 50%, 3 {unit}/s, ETA -")
                self.assertEqual((value["bytes_done"], value["bytes_total"], value["speed"]),
                                 (factor, 2 * factor, 3 * factor))
                self.assertIsNone(value["eta_seconds"])
                self.assertIsNone(value["files_done"])
                self.assertIsNone(value["files_total"])

    def test_latest_valid_line_and_duration_variants(self):
        for eta, seconds in (("0s", 0), ("22s", 22), ("1m2s", 62), ("2h", 7200),
                             ("1d2h3m4s", 93784), ("1.5s", 2)):
            value = parse_progress(STATS + "\nINFO : unrelated\n" +
                f"0 B / 0 B, 0%, 0 B/s, ETA {eta}\nERROR : problem")
            self.assertEqual(value["percent"], 0)
            self.assertEqual(value["eta_seconds"], seconds)
        for line in ("", "INFO : Transferred: 1 / 1, 100%", "ERROR : " + STATS,
                     STATS.replace("1h28m22s", "unknown"), STATS + "filename"):
            self.assertIsNone(parse_progress(line))

    def test_turkish_row_tray_and_total(self):
        value = parse_progress(STATS)
        folder = dict(id="docs", local="/tmp/Documents", remote_name="Documents", initialized=False)
        state = dict(status="syncing", progress=value)
        row = app_model.folder_row(folder, state)
        self.assertEqual(row.subtitle, "İlk eşitleme sürüyor · 3,2 / 9,2 GB · %35 · 1,2 MB/sn · "
                         "kalan ~1 sa 28 dk · 468/3.604 dosya")
        self.assertEqual(row.fraction, .35)
        status = dict(folder_config=[folder], folders=dict(docs=state))
        self.assertEqual(presentation.present(status).text,
                         "İlk eşitleme sürüyor: Documents · %35 · kalan ~1 sa 28 dk")
        self.assertEqual(presentation.progress_text(value, compact=True), "%35 · kalan ~1 sa 28 dk")
        self.assertEqual(presentation.overall_progress(status), "Toplam: %35")
        status["folders"]["second"] = dict(status="syncing", progress=dict(value, bytes_done=0))
        self.assertEqual(presentation.overall_progress(status), "Toplam: %17")
        status["folders"]["second"]["progress"] = None
        self.assertEqual(presentation.overall_progress(status), "")
        self.assertIsNone(app_model.folder_row(folder, dict(state, status="ok")).fraction)

    def test_format_unknown_eta_zero_files_and_small_units(self):
        value = parse_progress("0 B / 2 KiB, 0%, 1 B/s, ETA - (xfr#0/1000)")
        self.assertEqual(presentation.progress_text(value), "0,0 / 2,0 KB · %0 · 1 B/sn · 0/1.000 dosya")
        self.assertEqual(presentation.progress_text(value, compact=True), "%0")
        self.assertEqual(presentation.eta_text(59), "kalan ~59 sn")
        self.assertEqual(presentation.eta_text(3600), "kalan ~1 sa")
        self.assertEqual(presentation.progress_text(None), "")

    def test_frame_cycle_stops_for_idle_or_reduced_motion_and_assets_match(self):
        frame = 0
        frames = []
        for _ in range(16):
            frame = presentation.tray_frame(frame, True)
            frames.append(frame)
        self.assertEqual(frames, list(range(1, 8)) + [0] + list(range(1, 8)) + [0])
        self.assertIsNone(presentation.tray_frame(3, False))
        self.assertIsNone(presentation.tray_frame(3, True, False))
        directory = ROOT / "data/icons/hicolor/symbolic/apps"
        original = ET.parse(directory / "drivesync-syncing-symbolic.svg").getroot()
        for frame in range(8):
            svg = ET.parse(directory / f"drivesync-syncing-{frame}-symbolic.svg").getroot()
            self.assertEqual(svg[0].get("transform"), f"rotate({frame * 45} 8 8)")
            self.assertEqual([path.attrib for path in svg[0]], [path.attrib for path in original[0]])


class ProgressStateTests(IsolatedTest):
    def test_write_throttle_clear_and_new_run_with_fake_clock(self):
        clock = [10.0]
        changes = []
        store = StateStore(self.paths, changed=lambda: changes.append(1), clock=lambda: clock[0])
        store.folder("docs", status="syncing", progress=None)
        value = parse_progress(STATS)
        self.assertTrue(store.progress("docs", value))
        initial = store.read()["folders"]["docs"]["progress"]
        self.assertIn("updated_at", initial)
        self.assertNotIn("updated_at", value)
        changes.clear()
        for moment in (10.1, 11, 11.999):
            clock[0] = moment
            self.assertFalse(store.progress("docs", dict(value, percent=40)))
            self.assertEqual(store.read()["folders"]["docs"]["progress"], initial)
        self.assertFalse(changes)
        clock[0] = 12
        self.assertTrue(store.progress("docs", dict(value, percent=42)))
        self.assertEqual(len(changes), 1)
        store.folder("docs", status="error")
        self.assertIsNone(store.read()["folders"]["docs"]["progress"])
        self.assertFalse(store.progress("docs", value))
        store.folder("docs", status="syncing", progress=None)
        self.assertTrue(store.progress("docs", value))
        rclone.apply_result(store, "docs", rclone.RunResult(0))
        self.assertIsNone(store.read()["folders"]["docs"]["progress"])

    def test_throttle_is_per_folder(self):
        store = StateStore(self.paths, clock=lambda: 0)
        for folder_id in ("one", "two"):
            store.folder(folder_id, status="syncing")
            self.assertTrue(store.progress(folder_id, parse_progress(STATS)))
            self.assertFalse(store.progress(folder_id, parse_progress(STATS)))

    def test_tail_latest_complete_lines_throttles_and_handles_truncation(self):
        clock = [0]
        values = []
        log = self.root / "stats.log"
        tracker = LogProgress(log, values.append, clock=lambda: clock[0])
        tracker.poll()  # File may not have been created yet.
        log.write_text(STATS + "\n" + STATS.replace("35%", "36%") + "\nINFO : partial")
        clock[0] = 2
        tracker.poll()
        self.assertEqual([value["percent"] for value in values], [36])
        with log.open("a") as stream:
            stream.write("\n" + STATS.replace("35%", "37%"))
        clock[0] = 3
        tracker.poll()
        self.assertEqual(len(values), 1)
        clock[0] = 4
        tracker.poll()
        self.assertEqual(len(values), 1)  # An incomplete line is never published.
        with log.open("a") as stream:
            stream.write("\n")
        clock[0] = 6
        tracker.poll()
        self.assertEqual(values[-1]["percent"], 37)
        log.write_text("0 B / 1 B, 0%, 0 B/s, ETA -\n")
        clock[0] = 8
        tracker.poll()
        self.assertEqual(values[-1]["percent"], 0)

    def test_cli_publishes_while_running_and_clears_success_and_failure(self):
        self.assertEqual(self.cli("init", "docs", "--dry-run")[0], 0)
        original = StateStore.progress
        for code in (0, 5):
            with self.subTest(code=code):
                # Refresh the preview token before each direct run.
                self.assertEqual(self.cli("resync", "docs", "--dry-run")[0], 0)
                self.settings(bisync=dict(log=STATS + "\n", sleep=.4, code=code))
                observed = []
                def publish(store, folder_id, value):
                    result = original(store, folder_id, value)
                    observed.append(deepcopy(store.read()["folders"][folder_id]))
                    return result
                with patch.object(StateStore, "progress", autospec=True, side_effect=publish):
                    result = self.cli("resync", "docs", "--yes", "--foreground")
                self.assertEqual(result[0], 0 if code == 0 else 1, result)
                self.assertTrue(observed)
                self.assertEqual(observed[0]["status"], "syncing")
                self.assertEqual(observed[0]["progress"]["percent"], 35)
                self.assertIsNone(self.store.read()["folders"]["docs"]["progress"])
                self.settings()
