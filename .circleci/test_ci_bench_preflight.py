"""Source-only isolated bench unit tests. No real Frappe, DB, CI or backup."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ci_bench_preflight as benchguard


class BenchPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.bench = self.home / benchguard.BENCH_NAME
        (self.bench / "apps").mkdir(parents=True)
        self.sites = self.bench / "sites"
        self.sites.mkdir()
        for app in benchguard.APP_REVISIONS:
            (self.bench / "apps" / app).mkdir()
        self.conf = self.sites / "common_site_config.json"
        self.config = dict(benchguard.LOCAL_SERVICES)
        self.write_config()
        self.git = patch.object(
            benchguard, "_read_git_head",
            side_effect=lambda p: benchguard.APP_REVISIONS[p.name],
        )
        self.git.start()
        self.addCleanup(self.git.stop)

    def write_config(self):
        self.conf.write_text(json.dumps(self.config), encoding="utf-8")

    def attest(self, created=False):
        benchguard.attest_pinned_bench(self.home, self.bench, site_created=created)

    def test_pinned_empty_bench_pre_site_passes(self):
        self.attest()

    def test_recheck_when_isolated_site_created(self):
        (self.sites / benchguard.SITE).mkdir()
        self.attest(True)

    def test_fails_if_site_exists_before_or_absent_after(self):
        with self.assertRaises(RuntimeError):
            self.attest(True)
        (self.sites / benchguard.SITE).mkdir()
        with self.assertRaises(RuntimeError):
            self.attest(False)

    def test_denies_wrong_app_revision(self):
        with patch.object(benchguard, "_read_git_head", return_value="f" * 40):
            with self.assertRaisesRegex(RuntimeError, "revision mismatch"):
                self.attest()

    def test_denies_external_redis_or_db(self):
        for k, val in (("redis_cache", "redis://prod:13000"),
                       ("db_host", "192.168.50.1"), ("db_port", 3307),
                       ("db_type", "postgres")):
            with self.subTest(k=k):
                self.config[k] = val
                self.write_config()
                with self.assertRaises(RuntimeError):
                    self.attest()
                self.config.pop(k)
        self.write_config()

    def test_denies_site_credentials_in_global_config(self):
        for k in ("db_password", "db_user", "db_socket", "mariadb_socket",
                  "mysql_socket", "host_name", "mail_server"):
            with self.subTest(k=k):
                self.config[k] = "synthetic-only"
                self.write_config()
                with self.assertRaises(RuntimeError):
                    self.attest()
                self.config.pop(k)

    def test_denies_symlinked_app_or_site(self):
        app = self.bench / "apps" / "frappe"
        app.rmdir()
        app.symlink_to(self.bench / "apps" / "erpnext", target_is_directory=True)
        with self.assertRaises(RuntimeError):
            self.attest()
        app.unlink()
        app.mkdir()
        (self.sites / benchguard.SITE).symlink_to(app, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            self.attest(True)

    def test_denies_wrong_bench_destination_or_phase(self):
        with self.assertRaises(RuntimeError):
            benchguard.attest_pinned_bench(self.home, self.home / "development", site_created=False)
        with self.assertRaises(RuntimeError):
            benchguard.attest_pinned_bench(self.home, self.bench, site_created="false")

    def test_denies_group_writable_directory_or_config(self):
        self.conf.chmod(0o666)
        with self.assertRaises(RuntimeError):
            self.attest()
        self.conf.chmod(0o600)
        self.bench.chmod(0o777)
        with self.assertRaises(RuntimeError):
            self.attest()

    def test_git_read_uses_fixed_no_shell_argv_and_drops_stderr(self):
        class Result:
            stdout = benchguard.APP_REVISIONS["frappe"] + "\n"
        self.git.stop()
        with patch.object(benchguard.subprocess, "run", return_value=Result()) as proc:
            sha = benchguard._read_git_head(self.bench / "apps" / "frappe")
            self.assertEqual(sha, benchguard.APP_REVISIONS["frappe"])
            argv = proc.call_args.args[0]
            self.assertEqual(argv[:1], ["/usr/bin/git"])
            self.assertNotIn("shell", proc.call_args.kwargs)
            self.assertEqual(proc.call_args.kwargs["stderr"], benchguard.subprocess.DEVNULL)

    def test_no_database_restore_or_network_actions(self):
        source = Path(benchguard.__file__).read_text()
        for token in ("bench restore", "--force", "age -d", "requests.get(",
                      "urlopen(", "CREATE DATABASE", "DROP DATABASE"):
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
