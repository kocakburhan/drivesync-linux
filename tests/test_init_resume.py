"""Daemon-owned, reboot-safe init/resync: approval persistence, resume and re-approval."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from gi.repository import GLib

from support import IsolatedTest
from drivesync import config, i18n
from drivesync.daemon import Daemon, INIT_BACKOFF, INIT_MAX_ATTEMPTS
from drivesync.notify import Notifier
from test_daemon import drain_until


class InitResumeTests(IsolatedTest):
    def setUp(self):
        super().setUp()
        self.sent = []

    def daemon(self):
        digest = self.prepare(initialized=False)
        with patch("drivesync.daemon.Notifier", return_value=Notifier(send=lambda *a: self.sent.append(a))):
            daemon = Daemon(self.paths)
        daemon.next_poll = float("inf")
        self.addCleanup(daemon.watchers.close)
        self.addCleanup(lambda: daemon.child.kill() if daemon.child else None)
        return daemon, digest

    def approve(self, digest, kind="init", plan=None, fresh_dry_run=False):
        folder = self.config["folders"][0]
        request = dict(kind=kind, approved_at=datetime.now(timezone.utc).isoformat(),
                       approved_plan=plan or {"to_remote": 1, "to_local": 1, "deletes": 1},
                       filters_sha256=digest, pair_sha256=config.pair_hash(self.config, folder))
        fields = dict(init_request=request, status="init_queued")
        if fresh_dry_run:
            fields.update(dry_run_ok_at=datetime.now(timezone.utc).isoformat(),
                          dry_run_filters_sha256=digest, dry_run_pair_sha256=request["pair_sha256"])
        self.store.folder("docs", **fields)
        return request

    def run_jobs(self, daemon):
        timer = GLib.timeout_add(25, daemon._tick)
        try:
            daemon._scan_inits()
            daemon._pump()
            drain_until(lambda: daemon.child is None and not daemon.queue.pending
                        and daemon.queue.current is None)
        finally:
            GLib.source_remove(timer)

    def item(self):
        return self.store.read()["folders"]["docs"]

    def test_queued_init_runs_in_daemon_and_clears_request(self):
        daemon, digest = self.daemon()
        daemon.reload_config()
        self.approve(digest, fresh_dry_run=True)
        self.assertTrue(daemon.start_init("docs", "init"))
        self.run_jobs(daemon)
        self.assertEqual([c["argv"][0] for c in self.calls()], ["mkdir", "copyto", "bisync"])
        bisync = self.calls()[-1]["argv"]
        self.assertIn("--resync", bisync)
        self.assertEqual(bisync[bisync.index("--transfers") + 1], str(self.config["init_transfers"]))
        self.assertTrue(config.load(self.paths)["folders"][0]["initialized"])
        item = self.item()
        self.assertIsNone(item["init_request"])
        self.assertEqual(item["status"], "ok")

    def test_start_init_rejects_without_request_or_kind_mismatch(self):
        daemon, digest = self.daemon()
        daemon.reload_config()
        self.assertFalse(daemon.start_init("docs", "init"))
        self.approve(digest)
        self.assertFalse(daemon.start_init("docs", "resync"))
        self.assertFalse(daemon.start_init("docs", "bogus"))
        self.assertFalse(daemon.start_init("nope", "init"))

    def test_restart_with_pending_request_runs_fresh_dry_run_then_resumes(self):
        daemon, digest = self.daemon()
        # Interrupted run: status syncing, request kept, no valid dry-run any more.
        self.approve(digest)
        self.store.folder("docs", status="syncing")
        self.settings(dry_run=dict(log="INFO : - Path1 Queue copy to Path2 - big.bin\n"))
        daemon.reload_config()
        self.run_jobs(daemon)
        self.assertEqual([c["argv"][0] for c in self.calls()], ["mkdir", "bisync", "copyto", "bisync"])
        self.assertIn("--dry-run", self.calls()[1]["argv"])
        self.assertNotIn("--dry-run", self.calls()[3]["argv"])
        self.assertTrue(config.load(self.paths)["folders"][0]["initialized"])
        self.assertIsNone(self.item()["init_request"])

    def test_new_deletes_require_reapproval_and_notify(self):
        daemon, digest = self.daemon()
        self.approve(digest)
        # The fake's default dry-run output contains a delete.
        daemon.reload_config()
        self.run_jobs(daemon)
        self.assertEqual([c["argv"][0] for c in self.calls()], ["mkdir", "bisync"])
        item = self.item()
        self.assertEqual(item["status"], "needs_approval")
        self.assertEqual(item["last_error"], i18n.INIT_NEEDS_APPROVAL)
        self.assertIsNotNone(item["init_request"])
        self.assertFalse(config.load(self.paths)["folders"][0]["initialized"])
        self.assertTrue(any("İlk eşitleme yeniden onay bekliyor: Documents" in a[1] for a in self.sent))
        daemon._scan_inits()
        self.assertFalse(daemon.queue.pending)

    def test_more_downloads_than_approved_require_reapproval(self):
        daemon, digest = self.daemon()
        self.approve(digest, plan={"to_remote": 1, "to_local": 0, "deletes": 0})
        self.settings(dry_run=dict(log="INFO : - Path2 Queue copy to Path1 - new.txt\n"))
        daemon.reload_config()
        self.run_jobs(daemon)
        self.assertEqual(self.item()["status"], "needs_approval")
        self.assertNotIn("copyto", [c["argv"][0] for c in self.calls()])

    def test_changed_config_since_approval_requires_reapproval_without_touching_drive(self):
        daemon, digest = self.daemon()
        self.approve("stale")
        daemon.reload_config()
        self.run_jobs(daemon)
        self.assertEqual(self.item()["status"], "needs_approval")
        self.assertEqual(self.calls(), [])

    def test_failure_uses_bounded_backoff_then_gives_up(self):
        daemon, digest = self.daemon()
        request = self.approve(digest, fresh_dry_run=True)
        self.settings(bisync=dict(code=5, log="ERROR : boom\n"))
        daemon.reload_config()
        self.run_jobs(daemon)
        item = self.item()
        self.assertEqual((item["status"], item["init_attempts"]), ("init_queued", 1))
        due = datetime.fromisoformat(item["init_next_retry_at"]) - datetime.now(timezone.utc)
        self.assertLess(abs(due - timedelta(seconds=INIT_BACKOFF[0])), timedelta(seconds=5))
        daemon._scan_inits()
        self.assertFalse(daemon.queue.pending)  # still inside its backoff
        self.store.folder("docs", init_attempts=INIT_MAX_ATTEMPTS - 1, init_next_retry_at=None,
                          dry_run_ok_at=datetime.now(timezone.utc).isoformat(),
                          dry_run_filters_sha256=digest, dry_run_pair_sha256=request["pair_sha256"])
        self.run_jobs(daemon)
        item = self.item()
        self.assertIsNone(item["init_request"])
        self.assertEqual(item["status"], "not_initialized")
        self.assertTrue(any(i18n.INIT_GAVE_UP == a[1] for a in self.sent))

    def test_network_error_waits_without_using_a_retry(self):
        daemon, digest = self.daemon()
        self.approve(digest, fresh_dry_run=True)
        self.settings(mkdir=dict(code=1, output="dial tcp: no such host"))
        daemon.reload_config()
        self.run_jobs(daemon)
        item = self.item()
        self.assertEqual((item["status"], item["init_attempts"]), ("init_queued", 0))
        self.assertIsNotNone(item["init_next_retry_at"])
        self.assertIsNotNone(daemon.offline_since)

    def test_paused_daemon_keeps_request_queued(self):
        daemon, digest = self.daemon()
        self.approve(digest, fresh_dry_run=True)
        daemon.reload_config()
        daemon.queue.clear()
        daemon.config["paused"] = True
        daemon._scan_inits()
        self.assertFalse(daemon.queue.pending)
        self.assertIsNotNone(self.item()["init_request"])


class InitPresentationTests(IsolatedTest):
    def test_statuses_labels_and_rows(self):
        from drivesync import app_model, init_flow
        from drivesync.presentation import present
        folder = dict(self.config["folders"][0])
        self.assertIsNone(init_flow.init_label(folder, dict(status="init_queued")))
        self.assertEqual(init_flow.init_label(folder, dict(status="needs_approval")), i18n.INIT_REAPPROVE)
        row = app_model.folder_row(folder, dict(status="init_queued"))
        self.assertIn("yeniden başlatma sonrası devam edecek", row.subtitle)
        self.assertFalse(row.can_sync)
        row = app_model.folder_row(folder, dict(status="needs_approval"))
        self.assertIn("Yeniden onay gerekiyor", row.subtitle)
        self.assertEqual(row.icon, "error")
        status = dict(daemon=dict(status="idle"), folder_config=[folder],
                      folders={"docs": dict(status="needs_approval")})
        self.assertIn("yeniden onay bekliyor", present(status).text)
        status["folders"]["docs"] = dict(status="init_queued")
        self.assertIn("İlk eşitleme sırada", present(status).text)
        status["folders"]["docs"] = dict(status="syncing")
        self.assertIn("İlk eşitleme sürüyor", present(status).text)
