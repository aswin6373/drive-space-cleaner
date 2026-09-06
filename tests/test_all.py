"""Deep test suite for drive-space-cleaner-agent.

Run: .venv/bin/python -m pytest tests/ -v  (or via unittest: .venv/bin/python -m unittest discover tests)
"""
from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import cleaner, config, downloader, main, report, scanner, verifier  # noqa: E402
from src.drive_client import DriveClient, parse_drive_size, total_size  # noqa: E402

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


class FakeVerification:
    def __init__(self, verified=True, reason="Verified"):
        self.verified = verified
        self.reason = reason


class FakeDownloadResult:
    def __init__(self, file, local_path=None, verified=True, skipped=False, error=None):
        self.file = file
        self.local_path = local_path
        self.verification = FakeVerification(verified)
        self.skipped = skipped
        self.error = error


def make_file(fid="f1", name="video.mp4", size=1000, mime="video/mp4", **extra):
    meta = {
        "id": fid,
        "name": name,
        "mimeType": mime,
        "size": str(size),
        "createdTime": "2024-01-01T00:00:00.000Z",
        "modifiedTime": "2024-01-02T00:00:00.000Z",
        "parents": ["root"],
        "webViewLink": f"https://drive.google.com/file/d/{fid}/view",
    }
    meta.update(extra)
    return meta


# ----------------------------------------------------------------------------
# verifier.py
# ----------------------------------------------------------------------------


class TestVerifier(unittest.TestCase):
    def test_verify_ok(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "f.bin"
            p.write_bytes(b"x" * 100)
            r = verifier.verify_download(p, 100)
            self.assertTrue(r.verified)
            self.assertEqual(r.reason, "Verified")

    def test_verify_missing_file(self):
        r = verifier.verify_download(Path("/nonexistent/f.bin"), 100)
        self.assertFalse(r.verified)
        self.assertIn("does not exist", r.reason)

    def test_verify_size_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "f.bin"
            p.write_bytes(b"x" * 50)
            r = verifier.verify_download(p, 100)
            self.assertFalse(r.verified)
            self.assertIn("Size mismatch", r.reason)

    def test_verify_none_expected(self):
        r = verifier.verify_download(Path("/nonexistent"), None)
        self.assertFalse(r.verified)
        self.assertIn("missing", r.reason)


# ----------------------------------------------------------------------------
# drive_client.parse_drive_size / total_size
# ----------------------------------------------------------------------------


class TestParseDriveSize(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(parse_drive_size(make_file(size=123)), 123)

    def test_missing(self):
        self.assertIsNone(parse_drive_size({"id": "x"}))
        self.assertIsNone(parse_drive_size({"size": ""}))

    def test_garbage(self):
        self.assertIsNone(parse_drive_size({"size": "not-a-number"}))

    def test_total(self):
        files = [make_file(size=10), make_file(size=20), {"id": "z"}]
        self.assertEqual(total_size(files), 30)


# ----------------------------------------------------------------------------
# downloader.py
# ----------------------------------------------------------------------------


class TestSafeFilename(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(downloader.safe_filename("video.mp4"), "video.mp4")

    def test_windows_reserved(self):
        self.assertEqual(downloader.safe_filename("CON"), "CON_file")
        self.assertEqual(downloader.safe_filename("com1.tape"), "com1_file.tape")
        self.assertEqual(downloader.safe_filename("NUL"), "NUL_file")
        self.assertEqual(downloader.safe_filename("lpt9.log"), "lpt9_file.log")

    def test_bad_chars(self):
        # ':' and one char are adjacent so both map to '_' -> double underscore is expected
        self.assertEqual(downloader.safe_filename('a<b>c:"d/e\\f|g?h*i'), "a_b_c__d_e_f_g_h_i")

    def test_control_chars(self):
        self.assertEqual(downloader.safe_filename("a\x00b\x1f"), "a_b_")

    def test_empty(self):
        self.assertEqual(downloader.safe_filename(""), "unnamed")
        self.assertEqual(downloader.safe_filename("..."), "unnamed")
        self.assertEqual(downloader.safe_filename("   "), "unnamed")

    def test_spaces_collapsed(self):
        self.assertEqual(downloader.safe_filename("a   b"), "a b")

    def test_length_cap(self):
        long = "v" * 500 + ".mp4"
        self.assertLessEqual(len(downloader.safe_filename(long)), 180)


class TestUniqueDestination(unittest.TestCase):
    def test_unique(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            self.assertEqual(downloader.unique_destination(out, "a.mp4", "id1"), out / "a.mp4")

    def test_collision_appends_id(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "a.mp4").write_bytes(b"x")
            dest = downloader.unique_destination(out, "a.mp4", "id1")
            self.assertEqual(dest, out / "a_id1.mp4")

    def test_double_collision(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "a.mp4").write_bytes(b"x")
            (out / "a_id1.mp4").write_bytes(b"x")
            dest = downloader.unique_destination(out, "a.mp4", "id1")
            self.assertEqual(dest, out / "a_id1_2.mp4")


class TestExistingVerifiedFile(unittest.TestCase):
    def test_exact_match(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "v.mp4").write_bytes(b"x" * 100)
            found = downloader.existing_verified_file(out, make_file(name="v.mp4", size=100, fid="id9"))
            self.assertEqual(found, out / "v.mp4")

    def test_id_glob_match(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "v_id9.mp4").write_bytes(b"x" * 100)
            found = downloader.existing_verified_file(out, make_file(name="v.mp4", size=100, fid="id9"))
            self.assertEqual(found, out / "v_id9.mp4")

    def test_wrong_size_not_matched(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "v.mp4").write_bytes(b"x" * 99)
            self.assertIsNone(downloader.existing_verified_file(out, make_file(size=100)))

    def test_no_size(self):
        self.assertIsNone(downloader.existing_verified_file(Path("/tmp"), make_file(size=None)))


class TestDownloadDriveFile(unittest.TestCase):
    def _run(self, client, meta, out, dry_run=False):
        return downloader.download_drive_file(client, meta, out, dry_run=dry_run)

    def test_missing_size_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            r = self._run(mock.MagicMock(), make_file(size=None), Path(d))
            self.assertIsNotNone(r.error)
            self.assertFalse(r.verification.verified)
            self.assertIn("size is missing", r.error)

    def test_existing_file_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "video.mp4").write_bytes(b"x" * 1000)
            r = self._run(mock.MagicMock(), make_file(size=1000), out)
            self.assertTrue(r.skipped)
            self.assertTrue(r.verification.verified)
            self.assertFalse(r.downloaded)

    def test_dry_run_no_files(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            r = self._run(mock.MagicMock(), make_file(), out, dry_run=True)
            self.assertTrue(r.skipped)
            self.assertFalse(r.verification.verified)
            self.assertEqual(list(out.iterdir()), [])

    def test_successful_download_and_sidecar(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)

            class FakeClient:
                def download_file(self, fid, dest, total):
                    dest.write_bytes(b"x" * 1000)

            r = self._run(FakeClient(), make_file(), out)
            self.assertTrue(r.downloaded)
            self.assertTrue(r.verification.verified)
            sidecar = out / "video.mp4.metadata.json"
            self.assertTrue(sidecar.exists())
            payload = json.loads(sidecar.read_text())
            self.assertEqual(payload["driveFile"]["id"], "f1")
            self.assertIn("downloadedAt", payload)

    def test_failed_download_cleans_up(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)

            class BadClient:
                def download_file(self, fid, dest, total):
                    dest.write_bytes(b"partial")
                    raise RuntimeError("network boom")

            r = self._run(BadClient(), make_file(), out)
            self.assertIsNotNone(r.error)
            self.assertIn("network boom", r.error)
            self.assertFalse((out / "video.mp4").exists())

    def test_size_mismatch_not_verified(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)

            class ShortClient:
                def download_file(self, fid, dest, total):
                    dest.write_bytes(b"x" * 10)

            r = self._run(ShortClient(), make_file(), out)
            self.assertFalse(r.verification.verified)
            # sidecar should NOT be written for unverified download
            self.assertFalse((out / "video.mp4.metadata.json").exists())


# ----------------------------------------------------------------------------
# cleaner.py
# ----------------------------------------------------------------------------


class TestEligibleTrashCandidates(unittest.TestCase):
    def test_only_verified_eligible(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "v.mp4"
            p.write_bytes(b"x" * 10)
            ok = cleaner.TrashCandidate(make_file(), p, FakeVerification(True))
            bad = cleaner.TrashCandidate(make_file(), p, FakeVerification(False))
            self.assertEqual(cleaner.eligible_trash_candidates([ok, bad]), [ok])

    def test_missing_local_excluded(self):
        ok = cleaner.TrashCandidate(make_file(), Path("/nope"), FakeVerification(True))
        self.assertEqual(cleaner.eligible_trash_candidates([ok]), [])

    def test_missing_size_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "v.mp4"
            p.write_bytes(b"x")
            c = cleaner.TrashCandidate(make_file(size=None), p, FakeVerification(True))
            self.assertEqual(cleaner.eligible_trash_candidates([c]), [])


class TestTrashVerifiedFiles(unittest.TestCase):
    def test_dry_run_does_not_touch_drive(self):
        client = mock.MagicMock()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "v.mp4"
            p.write_bytes(b"x")
            c = cleaner.TrashCandidate(make_file(), p, FakeVerification(True))
            results = cleaner.trash_verified_files(client, [c], dry_run=True)
        client.trash_file.assert_not_called()
        self.assertFalse(results[0].trashed)
        self.assertIn("Dry run", results[0].reason)

    def test_real_trash_called(self):
        client = mock.MagicMock()
        client.trash_file.return_value = {"id": "f1", "name": "video.mp4", "trashed": True}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "v.mp4"
            p.write_bytes(b"x")
            c = cleaner.TrashCandidate(make_file(), p, FakeVerification(True))
            results = cleaner.trash_verified_files(client, [c], dry_run=False)
        client.trash_file.assert_called_once_with("f1")
        self.assertTrue(results[0].trashed)

    def test_trash_failure_fail_closed(self):
        client = mock.MagicMock()
        client.trash_file.side_effect = RuntimeError("api down")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "v.mp4"
            p.write_bytes(b"x")
            c = cleaner.TrashCandidate(make_file(), p, FakeVerification(True))
            results = cleaner.trash_verified_files(client, [c], dry_run=False)
        self.assertFalse(results[0].trashed)
        self.assertIn("Trash failed", results[0].reason)

    def test_unverified_rejected_before_trash(self):
        client = mock.MagicMock()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "v.mp4"
            p.write_bytes(b"x")
            c = cleaner.TrashCandidate(make_file(), p, FakeVerification(False))
            results = cleaner.trash_verified_files(client, [c], dry_run=False)
        client.trash_file.assert_not_called()
        self.assertFalse(results[0].trashed)


# ----------------------------------------------------------------------------
# report.py
# ----------------------------------------------------------------------------


class TestHumanSize(unittest.TestCase):
    def test_units(self):
        self.assertEqual(report.human_size(0), "0 B")
        self.assertEqual(report.human_size(None), "unknown")
        self.assertEqual(report.human_size(512), "512 B")
        self.assertEqual(report.human_size(1024), "1.00 KB")
        self.assertEqual(report.human_size(5 * 1024 * 1024), "5.00 MB")
        self.assertEqual(report.human_size(3 * 1024**3), "3.00 GB")

    def test_huge(self):
        self.assertIn("TB", report.human_size(5 * 1024**4))


class TestPrintTable(unittest.TestCase):
    def test_empty_no_crash(self):
        report.print_table([], ["a"])

    def test_truncation(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            report.print_table([{"name": "x" * 100}], ["name"])
        out = buf.getvalue()
        self.assertIn("...", out)
        self.assertLess(max(len(line) for line in out.splitlines()), 70)

    def test_alignment(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            report.print_table([{"a": "1", "b": "22"}], ["a", "b"])
        lines = buf.getvalue().splitlines()
        self.assertIn("a", lines[0])
        self.assertIn("|", lines[0])


class TestSaveScanReports(unittest.TestCase):
    def test_reports_written(self):
        with tempfile.TemporaryDirectory() as d:
            rd = Path(d)
            csv_path, json_path = report.save_scan_reports([make_file()], rd)
            self.assertTrue(csv_path.exists())
            self.assertTrue(json_path.exists())
            self.assertTrue(csv_path.name.startswith("drive-media-scan-"))
            rows = list(csv.DictReader(csv_path.open()))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["id"], "f1")
            self.assertEqual(rows[0]["sizeBytes"], "1000")
            payload = json.loads(json_path.read_text())
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["totalBytes"], 1000)

    def test_empty_report(self):
        with tempfile.TemporaryDirectory() as d:
            csv_path, json_path = report.save_scan_reports([], Path(d))
            self.assertTrue(csv_path.exists())
            payload = json.loads(json_path.read_text())
            self.assertEqual(payload["count"], 0)

    def test_reports_written_now(self):
        import csv as csvmod

        with tempfile.TemporaryDirectory() as d:
            rd = Path(d)
            csv_path, _ = report.save_scan_reports([make_file()], rd)
            rows = list(csvmod.DictReader(csv_path.open()))
            self.assertEqual(rows[0]["id"], "f1")
            self.assertEqual(rows[0]["sizeBytes"], "1000")


# ----------------------------------------------------------------------------
# config.py
# ----------------------------------------------------------------------------


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = config.load_config()
        self.assertEqual(cfg.credentials_path, config.PROJECT_ROOT / "credentials.json")
        self.assertEqual(cfg.token_path, config.PROJECT_ROOT / "token.json")

    def test_overrides(self):
        cfg = config.load_config(
            credentials_path="/tmp/c.json",
            token_path="/tmp/t.json",
            download_dir="/tmp/dl",
            report_dir="/tmp/rp",
        )
        self.assertEqual(cfg.credentials_path, Path("/tmp/c.json"))
        self.assertEqual(cfg.download_dir, Path("/tmp/dl"))

    def test_env_override(self):
        with mock.patch.dict(
            "os.environ",
            {
                "DRIVE_CLEANER_CREDENTIALS": "/env/c.json",
                "DRIVE_CLEANER_TOKEN": "/env/t.json",
                "DRIVE_CLEANER_DOWNLOAD_DIR": "/env/dl",
                "DRIVE_CLEANER_REPORT_DIR": "/env/rp",
                "DRIVE_CLEANER_LOG_DIR": "/env/lg",
            },
        ):
            cfg = config.load_config()
        self.assertEqual(cfg.credentials_path, Path("/env/c.json"))
        self.assertEqual(cfg.log_dir, Path("/env/lg"))

    def test_ensure_runtime_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = config.AppConfig(
                credentials_path=Path(d) / "c",
                token_path=Path(d) / "t",
                download_dir=Path(d) / "a",
                report_dir=Path(d) / "b",
                log_dir=Path(d) / "c2",
            )
            config.ensure_runtime_dirs(cfg)
            for p in (cfg.download_dir, cfg.report_dir, cfg.log_dir):
                self.assertTrue(p.is_dir())


# ----------------------------------------------------------------------------
# main.py - arg logic
# ----------------------------------------------------------------------------


class TestWithDefaultScanCommand(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(main.with_default_scan_command([]), ["scan"])

    def test_explicit_scan(self):
        self.assertEqual(main.with_default_scan_command(["scan", "--limit", "5"]), ["scan", "--limit", "5"])

    def test_bare_flags_get_scan_prefix(self):
        self.assertEqual(main.with_default_scan_command(["--min-size-mb", "100"]), ["scan", "--min-size-mb", "100"])

    def test_credentials_pair_prefix(self):
        self.assertEqual(
            main.with_default_scan_command(["--credentials", "c.json", "--min-size-mb", "1"]),
            ["--credentials", "c.json", "scan", "--min-size-mb", "1"],
        )

    def test_equals_form(self):
        self.assertEqual(
            main.with_default_scan_command(["--token=t.json", "scan"]),
            ["--token=t.json", "scan"],
        )

    def test_help_not_mutated(self):
        self.assertEqual(main.with_default_scan_command(["--help"]), ["--help"])


# ----------------------------------------------------------------------------
# scanner.py
# ----------------------------------------------------------------------------


class TestScanDrive(unittest.TestCase):
    def test_passes_params(self):
        client = mock.MagicMock()
        client.list_media_files.return_value = [make_file()]
        files = scanner.scan_drive(client, include_images=True, min_size_mb=0.001, limit=5)
        client.list_media_files.assert_called_once_with(include_images=True, min_size_bytes=1048, limit=5)
        self.assertEqual(len(files), 1)

    def test_mb_conversion(self):
        client = mock.MagicMock()
        scanner.scan_drive(client, include_images=False, min_size_mb=500, limit=None)
        args = client.list_media_files.call_args.kwargs
        self.assertEqual(args["min_size_bytes"], 500 * 1024 * 1024)


# ----------------------------------------------------------------------------
# DriveClient.list_media_files with fake service (pagination + filtering)
# ----------------------------------------------------------------------------


class FakeFilesResource:
    def __init__(self, pages):
        self._pages = pages
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return self

    def execute(self):
        page = self._pages.pop(0)
        return page

    def update(self, **kwargs):
        self.last_update = kwargs
        return self


class FakeService:
    def __init__(self, pages):
        self.files_resource = FakeFilesResource(pages)
        self.files = lambda: self.files_resource

    def files(self):
        return self.files_resource


class TestListMediaFiles(unittest.TestCase):
    def _client(self, pages):
        client = DriveClient.__new__(DriveClient)
        client.service = FakeService(pages)
        return client

    def test_pagination_and_limit(self):
        page1 = {"files": [make_file(fid=f"a{i}", size=2000) for i in range(100)], "nextPageToken": "T"}
        page2 = {"files": [make_file(fid=f"b{i}", size=2000) for i in range(100)]}
        client = self._client([page1, page2])
        files = client.list_media_files(include_images=False, min_size_bytes=1000, limit=150)
        self.assertEqual(len(files), 150)

    def test_filters_small_files(self):
        page = {"files": [make_file(size=500), make_file(size=5000)]}
        client = self._client([page])
        files = client.list_media_files(include_images=False, min_size_bytes=1000, limit=None)
        self.assertEqual([f["id"] for f in files], ["f1"])

    def test_size_filter_removes_none(self):
        page = {"files": [make_file(size=None), make_file(size=5000)]}
        client = self._client([page])
        files = client.list_media_files(include_images=False, min_size_bytes=1000, limit=None)
        self.assertEqual([f["id"] for f in files], ["f1"])

    def test_sorted_desc(self):
        page = {"files": [make_file(fid="s", size=100), make_file(fid="l", size=900)]}
        client = self._client([page])
        files = client.list_media_files(include_images=False, min_size_bytes=0, limit=None)
        self.assertEqual([f["id"] for f in files], ["l", "s"])

    def test_images_included_flag(self):
        page = {"files": []}
        client = self._client([page])
        client.list_media_files(include_images=True, min_size_bytes=0, limit=None)
        self.assertIn("image/", client.service.files_resource.calls[0]["q"])

    def test_images_excluded_default(self):
        page = {"files": []}
        client = self._client([page])
        client.list_media_files(include_images=False, min_size_bytes=0, limit=None)
        self.assertNotIn("image/", client.service.files_resource.calls[0]["q"])

    def test_query_excludes_trashed(self):
        page = {"files": []}
        client = self._client([page])
        client.list_media_files(include_images=False, min_size_bytes=0, limit=None)
        self.assertIn("trashed = false", client.service.files_resource.calls[0]["q"])


# ----------------------------------------------------------------------------
# main.py - full CLI flows (mocked DriveClient)
# ----------------------------------------------------------------------------


class TestCliFlows(unittest.TestCase):
    def _run_main(self, argv, input_text=""):
        client = mock.MagicMock()
        scan_return = [
            make_file(fid="big1", name="movie.mp4", size=500 * 1024 * 1024),
            make_file(fid="small1", name="clip.mp4", size=1024),
        ]
        client.list_media_files.return_value = scan_return

        def fake_download(fid, dest, total):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"x" * total)

        client.download_file.side_effect = fake_download
        client.trash_file.return_value = {"id": "f1", "trashed": True}

        buf = io.StringIO()
        scratch = tempfile.mkdtemp()
        env = {
            "DRIVE_CLEANER_REPORT_DIR": f"{scratch}/reports",
            "DRIVE_CLEANER_LOG_DIR": f"{scratch}/logs",
        }
        with mock.patch.object(main, "DriveClient", return_value=client):
            with mock.patch.object(main, "scan_drive", return_value=scan_return):
                with mock.patch("builtins.input", return_value=input_text):
                    with mock.patch.dict("os.environ", env):
                        with redirect_stdout(buf):
                            code = main.main(
                                [
                                    "--credentials",
                                    f"{scratch}/c.json",
                                    "--token",
                                    f"{scratch}/t.json",
                                    *argv,
                                ]
                            )
        return code, buf.getvalue(), client

    def test_scan_command(self):
        code, out, _ = self._run_main(["scan", "--min-size-mb", "1", "--limit", "2"])
        self.assertEqual(code, 0)
        self.assertIn("Found 2 media file", out)
        self.assertIn("Reports saved", out)
        self.assertNotIn("Download folder", out)

    def test_download_command(self):
        code, out, client = self._run_main(["download", "--min-size-mb", "0.0001", "--output", tempfile.mkdtemp()])
        self.assertEqual(code, 0)
        self.assertIn("Downloaded and verified", out)
        client.download_file.assert_called()

    def test_download_dry_run(self):
        code, out, client = self._run_main(
            ["download", "--min-size-mb", "0.0001", "--output", tempfile.mkdtemp(), "--dry-run"]
        )
        self.assertEqual(code, 0)
        self.assertIn("Dry run enabled", out)
        client.download_file.assert_not_called()

    def test_download_with_trash_confirmed(self):
        tmp_out = tempfile.mkdtemp()
        code, out, client = self._run_main(
            ["download", "--min-size-mb", "0.0001", "--output", tmp_out, "--trash-after-download"],
            input_text="YES_TRASH_VERIFIED_FILES",
        )
        self.assertEqual(code, 0)
        self.assertIn("eligible for Google Drive Trash", out)
        client.trash_file.assert_any_call("big1")
        client.trash_file.assert_any_call("small1")
        self.assertEqual(client.trash_file.call_count, 2)

    def test_download_with_trash_declined(self):
        tmp_out = tempfile.mkdtemp()
        code, out, client = self._run_main(
            ["download --trash-after-download".split()[0], "--min-size-mb", "0.0001", "--output", tmp_out, "--trash-after-download"],
            input_text="no",
        )
        self.assertEqual(code, 0)
        client.trash_file.assert_not_called()
        self.assertIn("cancelled", out)

    def test_default_command_is_scan(self):
        code, out, _ = self._run_main(["--min-size-mb", "1"])
        self.assertEqual(code, 0)
        self.assertNotIn("Download folder", out)

    def test_trash_failure_still_zero_exit(self):
        tmp_out = tempfile.mkdtemp()
        client = mock.MagicMock()
        scan_return = [make_file(fid="f1", size=1000)]
        client.list_media_files.return_value = scan_return

        def fake_download(fid, dest, total):
            dest.write_bytes(b"x" * total)

        client.download_file.side_effect = fake_download
        client.trash_file.side_effect = RuntimeError("API exploded")

        buf = io.StringIO()
        scratch = tempfile.mkdtemp()
        env = {
            "DRIVE_CLEANER_REPORT_DIR": f"{scratch}/reports",
            "DRIVE_CLEANER_LOG_DIR": f"{scratch}/logs",
        }
        with mock.patch.object(main, "DriveClient", return_value=client):
            with mock.patch.object(main, "scan_drive", return_value=scan_return):
                with mock.patch("builtins.input", return_value="YES_TRASH_VERIFIED_FILES"):
                    with mock.patch.dict("os.environ", env):
                        with redirect_stdout(buf):
                            code = main.main(
                                [
                                    "--credentials",
                                    f"{scratch}/c.json",
                                    "--token",
                                    f"{scratch}/t.json",
                                    "download",
                                    "--min-size-mb",
                                    "0.0001",
                                    "--output",
                                    tmp_out,
                                    "--trash-after-download",
                                ]
                            )
        self.assertEqual(code, 0)
        self.assertIn("Skipped: video.mp4", buf.getvalue())
        self.assertIn("Trash failed", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
