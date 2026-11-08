"""Unit tests (stdlib unittest). Run: python -m unittest discover -s tests -v"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from diskkit import archives, backup, compress, dupes, hooks, remove, rules, safety  # noqa: E402
from diskkit.scan import analyze  # noqa: E402


def write(p: Path, data: bytes | str = b"x", mtime: float | None = None) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode()
    p.write_bytes(data)
    if mtime:
        os.utime(p, (mtime, mtime))
    return p


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="diskwise-test-"))
        self.env = {k: os.environ.get(k) for k in ("DISKWISE_HOME", "DISKWISE_QUARANTINE_DIR")}
        os.environ["DISKWISE_HOME"] = str(self.tmp / "_home")
        os.environ["DISKWISE_QUARANTINE_DIR"] = str(self.tmp / "_quarantine")

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)


class NameRules(unittest.TestCase):
    def test_version_keys(self):
        same = ["report.docx", "report_v2.docx", "Report_FINAL.docx", "report (1).docx",
                "Copy of report.docx", "report - Copy.docx", "report_2026-11-08.docx",
                "report_v3_final (2).docx", "report.docx.bak"]
        keys = {rules.version_key(n) for n in same}
        self.assertEqual(keys, {("report", ".docx")})

    def test_not_versions(self):
        self.assertFalse(rules.has_version_marker("chapter_2.md"))
        self.assertEqual(rules.version_key("chapter_2.md")[0], "chapter_2")
        self.assertFalse(rules.has_version_marker("ext_aqs_USGS_13342500.parquet"))
        self.assertEqual(rules.version_key("dev2.py")[0], "dev2")

    def test_dated_series(self):
        names = ["lake_2026-01-05.png", "lake_2026-02-05.png", "lake_2026-03-05.png"]
        self.assertTrue(rules.looks_like_series(names))
        self.assertFalse(rules.looks_like_series(["doc_2026-05-10_0842.docx", "doc_2026-05-10_0807.docx",
                                                  "doc_LATEST.docx"]))

    def test_rank_prefers_latest_and_higher_version(self):
        self.assertGreater(rules.version_rank("m_v3.pt"), rules.version_rank("m_v2.pt"))
        self.assertGreater(rules.version_rank("m_LATEST.pt"), rules.version_rank("m_v9.pt"))

    def test_artifact_guard(self):
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, "build"))
            self.assertIsNone(rules.match_artifact(d, "build"))  # no marker -> user data
            Path(d, "CMakeLists.txt").write_text("x")
            self.assertEqual(rules.match_artifact(d, "build").ecosystem, "cmake")
            os.mkdir(os.path.join(d, "venv"))
            self.assertIsNone(rules.match_artifact(d, "venv"))
            Path(d, "venv", "pyvenv.cfg").write_text("home = x")
            self.assertEqual(rules.match_artifact(d, "venv").ecosystem, "python")


class ScanAndDupes(TempDirCase):
    def test_analyze_finds_things(self):
        old = time.time() - 400 * 86400
        write(self.tmp / "proj" / "package.json", "{}")
        write(self.tmp / "proj" / "node_modules" / "a" / "index.js", b"x" * 5000)
        write(self.tmp / "docs" / "plan.docx", b"a" * 3000, old)
        write(self.tmp / "docs" / "plan_v2.docx", b"b" * 3000)
        write(self.tmp / "docs" / "~$plan.docx", b"lock")
        a = analyze(str(self.tmp / "proj").rsplit(os.sep, 1)[0] if False else str(self.tmp))
        self.assertEqual(len(a.artifacts), 1)
        self.assertEqual(a.artifacts[0].size, 5000)
        self.assertTrue(any(f.key == "plan.docx" for f in a.families))
        fam = next(f for f in a.families if f.key == "plan.docx")
        self.assertTrue(fam.members[0][0].endswith("plan_v2.docx"))
        self.assertTrue(any(k == "office-lock" for k, _ in a.junk))
        self.assertEqual(a.total, 5000 + 3000 + 3000 + 4 + 2)

    def test_duplicates(self):
        payload = os.urandom(300_000)
        write(self.tmp / "data" / "big.bin", payload, time.time() - 1000)
        write(self.tmp / "Downloads" / "big (1).bin", payload)
        write(self.tmp / "other" / "same_size.bin", os.urandom(300_000))
        groups, meta = dupes.find_duplicates([str(self.tmp)], min_size=1000, use_cache=False)
        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0].paths[0].endswith("big.bin"))
        self.assertEqual(groups[0].reclaimable, 300_000)

    def test_extracted_archive(self):
        write(self.tmp / "pkg" / "a.txt", "hello")
        write(self.tmp / "pkg" / "sub" / "b.txt", "world")
        z = self.tmp / "pkg.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.write(self.tmp / "pkg" / "a.txt", "a.txt")
            zf.write(self.tmp / "pkg" / "sub" / "b.txt", "sub/b.txt")
        r = archives.check_archive(str(z), verify_crc=True)
        self.assertEqual(r["fraction"], 1.0)
        self.assertTrue(r["extracted_to"].endswith("pkg"))

    def test_compressible(self):
        write(self.tmp / "big.csv", b"1,2,3,4,5\n" * 300_000)
        a = analyze(str(self.tmp), big_threshold=1)
        rows = compress.find_compressible(a.big_files, min_size=1)
        self.assertEqual(len(rows), 1)
        self.assertLess(rows[0]["est_ratio"], 0.2)


class Removal(TempDirCase):
    def test_quarantine_restore_purge(self):
        f = write(self.tmp / "work" / "junk.tmp", b"z" * 1234)
        res = remove.remove([str(f)], mode="quarantine")
        self.assertEqual(len(res["removed"]), 1, res)
        self.assertFalse(f.exists())
        self.assertEqual(res["pending_until_purge"], 1234)
        batch = res["batch"]
        self.assertTrue(any(b["batch"] == batch for b in remove.list_batches()))
        self.assertEqual(remove.restore(batch)["restored"], [os.path.abspath(f)])
        self.assertTrue(f.exists())
        res = remove.remove([str(f)], mode="quarantine")
        p = remove.purge(res["batch"])
        self.assertIn(res["batch"], p["purged"])

    def test_dry_run_and_plan_change_guard(self):
        f = write(self.tmp / "a.bin", b"1" * 10)
        res = remove.remove([str(f)], dry_run=True)
        self.assertTrue(f.exists())
        self.assertEqual(res["bytes"], 10)
        res = remove.remove([str(f)], expect={str(f): (99, 0)})
        self.assertEqual(res["skipped"][0]["why"], "changed since the plan was made")
        self.assertTrue(f.exists())

    def test_protected(self):
        self.assertIsNotNone(safety.why_protected(str(Path.home())))
        self.assertIsNotNone(safety.why_protected(str(Path.home() / ".ssh" / "id_rsa")))
        self.assertIsNone(safety.why_protected(str(self.tmp / "x")))

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_git_tracked_refused(self):
        repo = self.tmp / "repo"
        write(repo / "kept.txt", "data")
        write(repo / "untracked.log", "log")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "kept.txt"], check=True)
        self.assertIsNotNone(safety.check_removable(str(repo / "kept.txt")))
        self.assertIsNone(safety.check_removable(str(repo / "untracked.log")))


class Backup(TempDirCase):
    def test_backup_verify_move(self):
        src = self.tmp / "src"
        write(src / "a.bin", os.urandom(50_000))
        write(src / "deep" / "b.txt", "hello")
        res = backup.backup(str(src), str(self.tmp / "dest"))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["copied"], 2)
        again = backup.backup(str(src), str(self.tmp / "dest"))
        self.assertEqual(again["skipped_same"], 2)
        v = backup.verify_manifest(res["manifest"])
        self.assertTrue(v["all_good"], v)
        (self.tmp / "dest" / "src" / "deep" / "b.txt").write_text("tampered")
        self.assertFalse(backup.verify_manifest(res["manifest"])["all_good"])


class Hooks(TempDirCase):
    def run_hook(self, fn, payload):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(payload)
        out = buf.getvalue()
        return json.loads(out) if out else None

    def test_pre_write_flags_version_copy(self):
        write(self.tmp / "report.md", "x")
        out = self.run_hook(hooks.pre_write, {"tool_name": "Write", "cwd": str(self.tmp),
                                              "tool_input": {"file_path": str(self.tmp / "report_v2.md")}})
        self.assertIn("report.md", out["hookSpecificOutput"]["additionalContext"])
        # overwriting in place is fine
        self.assertIsNone(self.run_hook(hooks.pre_write, {"tool_name": "Write", "cwd": str(self.tmp),
                                        "tool_input": {"file_path": str(self.tmp / "report.md")}}))
        # a new unrelated file is fine
        self.assertIsNone(self.run_hook(hooks.pre_write, {"tool_name": "Write", "cwd": str(self.tmp),
                                        "tool_input": {"file_path": str(self.tmp / "notes.md")}}))

    def test_session_and_stop(self):
        sid = "test-session"
        self.run_hook(hooks.session_start, {"session_id": sid, "cwd": str(self.tmp)})
        time.sleep(0.05)
        write(self.tmp / "out.csv", "a")
        write(self.tmp / "out_final.csv", "b")
        out = self.run_hook(hooks.stop, {"session_id": sid, "cwd": str(self.tmp)})
        self.assertIn("out_final.csv", out["systemMessage"])
        # reported once only
        self.assertIsNone(self.run_hook(hooks.stop, {"session_id": sid, "cwd": str(self.tmp)}))


class CLI(TempDirCase):
    def test_cli_json_commands(self):
        write(self.tmp / "a" / "x.bin", b"1" * 2000)
        script = str(ROOT / "scripts" / "diskwise.py")
        for args in (["scan", str(self.tmp), "--json"], ["versions", str(self.tmp), "--json"],
                     ["audit", str(self.tmp), "--json", "--dupes"], ["status", "--json"]):
            out = subprocess.run([sys.executable, script, *args], capture_output=True, text=True,
                                 encoding="utf-8")
            self.assertEqual(out.returncode, 0, out.stderr)
            json.loads(out.stdout)

    def test_delete_requires_yes(self):
        f = write(self.tmp / "keep.bin", b"1")
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "diskwise.py"), "remove", str(f),
                              "--mode", "delete"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertTrue(f.exists())


if __name__ == "__main__":
    unittest.main()
