"""Initial-sync size estimate: local walk, thresholds, dry-run JSON/human output and config."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from support import IsolatedTest, ROOT
from drivesync import config, i18n, init_flow, presentation


def write(path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


class MeasureTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="drivesync-size-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "Local"
        self.root.mkdir()

    def test_bytes_files_excludes_and_symlinks(self):
        write(self.root / "a.txt", 10)
        write(self.root / "big/x.bin", 1000)
        write(self.root / "big/sub/y.bin", 500)
        write(self.root / "big/node_modules/z.js", 9999)  # "node_modules/" also excludes nested dirs
        write(self.root / "big/tmp.tmp", 7777)            # "*.tmp" excludes files
        write(self.root / "docs/readme", 200)
        write(self.root / "docs/cache.tmp", 4000)
        write(self.root / "node_modules/top.js", 4444)    # top-level excluded directory
        (self.root / "link_file").symlink_to(self.root / "a.txt")
        (self.root / "link_dir").symlink_to(self.root / "big", target_is_directory=True)
        total, files, subfolders = init_flow.measure(str(self.root), ["node_modules/", "*.tmp"])
        self.assertEqual((total, files), (1710, 4))
        self.assertEqual(subfolders, [dict(name="big", bytes=1500, files=2),
                                      dict(name="docs", bytes=200, files=1)])

    def test_symlink_to_outside_file_is_not_counted(self):
        outside = self.root.parent / "outside.bin"
        write(outside, 5000)
        (self.root / "inside-link.bin").symlink_to(outside)
        self.assertEqual(init_flow.measure(str(self.root), [])[:2], (0, 0))

    def test_top_five_subfolders_sorted_by_size_then_name(self):
        for index, size in enumerate((100, 700, 300, 700, 50, 900, 10)):
            write(self.root / f"dir{index}" / "f.bin", size)
        _, _, subfolders = init_flow.measure(str(self.root), [])
        self.assertEqual([item["name"] for item in subfolders], ["dir5", "dir1", "dir3", "dir2", "dir0"])
        self.assertEqual(len(subfolders), init_flow.LARGEST_SUBFOLDERS)

    def test_size_report_estimate_and_flags(self):
        write(self.root / "one/f.bin", 10000)
        report = init_flow.size_report(str(self.root), [], 1000)
        self.assertEqual(report["estimated_seconds"], 10)
        self.assertEqual(report["assumed_bytes_per_second"], 1000)
        self.assertFalse(report["large"])
        self.assertEqual(report["largest_subfolders"], [dict(name="one", bytes=10000, files=1)])


class ThresholdTests(unittest.TestCase):
    def test_boundaries_are_strict(self):
        self.assertFalse(init_flow.is_large(init_flow.LARGE_FILE_COUNT, 0))
        self.assertTrue(init_flow.is_large(init_flow.LARGE_FILE_COUNT + 1, 0))
        self.assertFalse(init_flow.is_large(0, init_flow.LARGE_BYTES))
        self.assertTrue(init_flow.is_large(0, init_flow.LARGE_BYTES + 1))

    def test_constants_are_the_single_source(self):
        self.assertEqual(init_flow.LARGE_FILE_COUNT, 20000)
        self.assertEqual(init_flow.LARGE_BYTES, 10 * 1024 ** 3)
        with patch.object(init_flow, "LARGE_FILE_COUNT", 2):
            self.assertTrue(init_flow.is_large(3, 0))


class PlanTests(unittest.TestCase):
    def dry_run(self, **extra):
        return json.dumps(dict(to_remote=1, to_local=0, deletes=0, unknown=0, actions=["copy a"], **extra))

    def size(self, **changes):
        size = dict(bytes=5, files=1, estimated_seconds=0, assumed_bytes_per_second=1048576, large=False,
                    largest_subfolders=[dict(name="x", bytes=5, files=1)])
        size.update(changes)
        return size

    def test_size_is_optional_and_validated(self):
        self.assertIsNone(init_flow.parse_plan(self.dry_run()).size)
        self.assertEqual(init_flow.parse_plan(self.dry_run(size=self.size())).size["files"], 1)
        bad = [self.size(bytes=-1), self.size(files=1.5), self.size(large="yes"),
               self.size(assumed_bytes_per_second=0), self.size(largest_subfolders=[{"name": "a"}] * 6),
               self.size(largest_subfolders=[dict(name=1, bytes=1, files=1)])]
        for item in bad:
            with self.subTest(size=item), self.assertRaises(ValueError):
                init_flow.parse_plan(self.dry_run(size=item))
        with self.assertRaises(ValueError):
            init_flow.parse_plan(self.dry_run(size="large"))


class ConfigTests(unittest.TestCase):
    def test_default_and_validation_of_assumed_speed(self):
        self.assertEqual(config.defaults()["assumed_upload_bytes_per_second"], 1048576)
        for good in (1, 2.5, 1048576):
            self.assertEqual(config.validate(dict(config.defaults(), assumed_upload_bytes_per_second=good))
                             ["assumed_upload_bytes_per_second"], good)
        for bad in (0, -1, True, "1", None, float("nan"), float("inf")):
            with self.subTest(bad=bad), self.assertRaises(config.ConfigError):
                config.validate(dict(config.defaults(), assumed_upload_bytes_per_second=bad))


class PresentationSizeTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(i18n.set_language)

    def test_turkish_and_english_summary(self):
        size = dict(bytes=int(23.4 * 1024 ** 3), files=142783, estimated_seconds=54000)
        i18n.set_language("tr")
        self.assertEqual(presentation.size_summary(size), "≈ 23,4 GB · 142.783 dosya · tahmini ~15 sa")
        i18n.set_language("en")
        self.assertEqual(presentation.size_summary(size), "≈ 23.4 GB · 142,783 files · estimated ~15 h")

    def test_small_units_and_durations(self):
        i18n.set_language("tr")
        self.assertEqual(presentation.size_text(0), "0 B")
        self.assertEqual(presentation.size_text(1536), "1,5 KB")
        self.assertEqual(presentation.duration_text(90), "1 dk")
        self.assertEqual(presentation.duration_text(30), "30 sn")


class CliSizeTests(IsolatedTest):
    def setUp(self):
        super().setUp()
        self.local = Path(self.config["folders"][0]["local"])
        write(self.local / "Photos/a.jpg", 100)
        write(self.local / "Photos/b.jpg", 100)
        write(self.local / "Videos/v.mp4", 300)
        write(self.local / "Videos/cache.tmp", 1000)   # excluded by default "*.tmp"
        write(self.local / "node_modules/x.js", 5000)  # excluded by default "node_modules/"
        write(self.local / "top.txt", 50)
        patcher = patch("drivesync.cli.dbus_service.call", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def value_of(self, *args):
        code, out, err = self.cli(*args)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_json_contains_size_object(self):
        size = self.value_of("init", "docs", "--dry-run", "--json")["size"]
        self.assertEqual(size, dict(bytes=550, files=4, estimated_seconds=0, assumed_bytes_per_second=1048576,
                                    large=False,
                                    largest_subfolders=[dict(name="Videos", bytes=300, files=1),
                                                        dict(name="Photos", bytes=200, files=2)]))

    def test_speed_setting_changes_estimate(self):
        self.assertEqual(self.cli("config", "set", "assumed_upload_bytes_per_second", "100")[0], 0)
        self.assertEqual(self.value_of("init", "docs", "--dry-run", "--json")["size"]["estimated_seconds"], 5)
        self.assertEqual(self.cli("config", "set", "assumed_upload_bytes_per_second", "0")[0], 1)
        self.assertEqual(self.value_of("config", "get", "assumed_upload_bytes_per_second", "--json")["value"], 100)

    def test_excludes_are_applied_to_size_and_subfolders(self):
        self.assertEqual(self.cli("config", "excludes", "add", "Videos/")[0], 0)
        size = self.value_of("init", "docs", "--dry-run", "--json")["size"]
        self.assertEqual((size["bytes"], size["files"]), (250, 3))
        self.assertEqual([item["name"] for item in size["largest_subfolders"]], ["Photos"])

    def test_large_flag_follows_thresholds(self):
        with patch.object(init_flow, "LARGE_FILE_COUNT", 3):
            self.assertTrue(self.value_of("init", "docs", "--dry-run", "--json")["size"]["large"])
        with patch.object(init_flow, "LARGE_BYTES", 500):
            self.assertTrue(self.value_of("init", "docs", "--dry-run", "--json")["size"]["large"])

    def test_human_output_shows_summary_and_subfolders(self):
        size = self.value_of("init", "docs", "--dry-run", "--json")["size"]
        code, out, err = self.cli("init", "docs", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn(i18n.SIZE_LINE.format(summary=presentation.size_summary(size)), out)
        self.assertIn(i18n.SUBFOLDER_LINE.format(name="Videos", size="300 B", files=1), out)
        self.assertIn(i18n.SUBFOLDER_LINE.format(name="Photos", size="200 B", files=2), out)
        self.assertNotIn(i18n.LARGE_WARNING, out)
        with patch.object(init_flow, "LARGE_FILE_COUNT", 3):
            code, out, err = self.cli("init", "docs", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn(i18n.LARGE_WARNING, out)
        self.assertIn(i18n.DRY_LARGE_HINT, out)

    def test_agents_document_every_safe_key(self):
        from drivesync import cli
        docs = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for key in cli.SAFE_KEYS:
            self.assertIn("`" + key + "`", docs)


if __name__ == "__main__":
    unittest.main()
