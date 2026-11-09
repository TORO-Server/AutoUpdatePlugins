#!/usr/bin/env python3
"""auto_update_plugins の単体テスト。ネットワークは使わない。"""
import hashlib
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import auto_update_plugins as aup


def version(number, loaders, files, date="2026-01-01T00:00:00Z", vtype="release"):
    return {
        "version_number": number,
        "version_type": vtype,
        "loaders": loaders,
        "game_versions": ["1.21.11", "26.3"],
        "date_published": date,
        "files": files,
    }


def mfile(name, sha512="a" * 128, primary=True):
    return {"filename": name, "url": f"https://cdn.example/{name}", "primary": primary,
            "hashes": {"sha512": sha512, "sha1": "b" * 40}}


class ModrinthSelectTest(unittest.TestCase):
    def setUp(self):
        self.entry = {"source": "modrinth:example", "version": "1.0-paper", "glob": "ex-*-paper.jar"}

    def test_pinned(self):
        versions = [version("1.0-paper", ["paper"], [mfile("ex-1.0-paper.jar")]),
                    version("2.0-paper", ["paper"], [mfile("ex-2.0-paper.jar")])]
        got = aup.modrinth_select(versions, self.entry, latest=False)
        self.assertEqual(got["file"], "ex-1.0-paper.jar")
        self.assertEqual(got["sha512"], "a" * 128)

    def test_latest_skips_beta_and_wrong_loader(self):
        versions = [
            version("3.0-paper", ["paper"], [mfile("ex-3.0-paper.jar")], date="2026-03-01T00:00:00Z", vtype="beta"),
            version("2.0-fabric", ["fabric"], [mfile("ex-2.0-fabric.jar")], date="2026-02-01T00:00:00Z"),
            version("1.5-paper", ["paper"], [mfile("ex-1.5-paper.jar")], date="2026-01-15T00:00:00Z"),
            version("1.0-paper", ["paper"], [mfile("ex-1.0-paper.jar")], date="2026-01-01T00:00:00Z"),
        ]
        got = aup.modrinth_select(versions, self.entry, latest=True)
        self.assertEqual(got["version"], "1.5-paper")

    def test_latest_prefers_primary_file(self):
        v = version("2.0-paper", ["paper"], [
            mfile("ex-2.0-paper-sources.jar", primary=False),
            mfile("ex-2.0-paper.jar", primary=True),
        ])
        got = aup.modrinth_select([v], self.entry, latest=True)
        self.assertEqual(got["file"], "ex-2.0-paper.jar")

    def test_game_version_filter(self):
        versions = [version("2.0-paper", ["paper"], [mfile("ex-2.0-paper.jar")], date="2026-02-01T00:00:00Z"),
                    version("1.0-paper", ["paper"], [mfile("ex-1.0-paper.jar")], date="2026-01-01T00:00:00Z")]
        versions[0]["game_versions"] = ["1.21.11"]
        with mock.patch.dict(os.environ, {"AUP_GAME_VERSION": "26.3"}):
            got = aup.modrinth_select(versions, self.entry, latest=True)
        self.assertEqual(got["version"], "1.0-paper")


class GitHubTest(unittest.TestCase):
    def test_normalize_with_digest(self):
        release = {"tag_name": "v2.0", "assets": [
            {"name": "Other.jar", "browser_download_url": "https://x/Other.jar", "digest": None},
            {"name": "Plug-2.0.jar", "browser_download_url": "https://x/Plug-2.0.jar",
             "digest": "sha256:" + "c" * 64},
        ]}
        entry = {"source": "github:o/r", "glob": "Plug-*.jar"}
        got = aup.github_normalize(release, entry)
        self.assertEqual(got["file"], "Plug-2.0.jar")
        self.assertEqual(got["sha256_published"], "c" * 64)

    def test_normalize_without_digest(self):
        release = {"tag_name": "1.0", "assets": [
            {"name": "Plug.jar", "browser_download_url": "https://x/Plug.jar", "digest": None}]}
        got = aup.github_normalize(release, {"source": "github:o/r", "glob": "Plug.jar"})
        self.assertNotIn("sha256_published", got)


class ValidatedTest(unittest.TestCase):
    def test_rejects_bad_sha256(self):
        entry = {"source": "github:o/r", "version": "1.0", "url": "https://x/a.jar", "sha256": "zz"}
        with self.assertRaises(ValueError):
            aup.validated(entry)


class FetchTest(unittest.TestCase):
    def test_fetch_places_and_removes_stale(self):
        content = b"jar-bytes"
        sha = hashlib.sha256(content).hexdigest()
        lock = {
            "Plug": {"source": "github:o/r", "version": "2.0", "file": "Plug-2.0.jar",
                     "glob": "Plug-*.jar", "url": "https://x/Plug-2.0.jar", "sha256": sha},
            "Hand": {"source": "manual", "version": "1.0", "file": "Hand.jar", "note": "手動"},
        }

        def fake_download(url, dest):
            pathlib.Path(dest).write_bytes(content)

        with tempfile.TemporaryDirectory() as td:
            target = pathlib.Path(td)
            (target / "Plug-1.0.jar").write_bytes(b"old")
            with mock.patch.object(aup, "download_to", fake_download):
                rc = aup.fetch(lock, td)
            self.assertEqual(rc, 0)
            self.assertTrue((target / "Plug-2.0.jar").exists())
            self.assertFalse((target / "Plug-1.0.jar").exists())
            # 2回目はダウンロードしない
            with mock.patch.object(aup, "download_to", side_effect=AssertionError("再取得した")):
                rc = aup.fetch(lock, td)
            self.assertEqual(rc, 0)


class CheckTest(unittest.TestCase):
    def test_check_ok_and_ng(self):
        entry = {"source": "github:o/r", "version": "1.0", "file": "Plug.jar", "glob": "Plug.jar",
                 "url": "https://x/Plug.jar", "sha256": "d" * 64}
        published = {"version": "1.0", "file": "Plug.jar", "url": "https://x/Plug.jar"}
        lock = {"Plug": entry, "Hand": {"source": "compat", "version": "1", "file": "H.jar", "note": "n"}}
        with mock.patch.object(aup, "resolve", return_value=published), \
             mock.patch.object(aup, "download_hashes", return_value=("d" * 64, "e" * 128)):
            self.assertEqual(aup.check(lock), 0)
        with mock.patch.object(aup, "resolve", return_value=published), \
             mock.patch.object(aup, "download_hashes", return_value=("f" * 64, "e" * 128)):
            self.assertEqual(aup.check(lock), 1)


class VerifyTest(unittest.TestCase):
    def test_verify_ok_ng_and_skip(self):
        content = b"jar-bytes"
        sha = hashlib.sha256(content).hexdigest()
        lock = {
            "Auto": {"source": "github:o/r", "version": "1.0", "file": "Auto.jar", "sha256": sha},
            "Hand": {"source": "manual", "version": "1.0", "file": "Hand.jar", "sha256": sha},
            "NoHash": {"source": "manual", "version": "1.0", "file": "NoHash.jar", "note": "n"},
        }
        with tempfile.TemporaryDirectory() as td:
            target = pathlib.Path(td)
            (target / "Auto.jar").write_bytes(content)
            (target / "Hand.jar").write_bytes(content)
            self.assertEqual(aup.verify(lock, td), 0)
            # 改変すると NG
            (target / "Hand.jar").write_bytes(b"tampered")
            self.assertEqual(aup.verify(lock, td), 1)
            # ファイルが無くても NG
            (target / "Hand.jar").write_bytes(content)
            (target / "Auto.jar").unlink()
            self.assertEqual(aup.verify(lock, td), 1)


class UpdateTest(unittest.TestCase):
    def test_update_rewrites_lock(self):
        entry = {"source": "github:o/r", "version": "1.0", "file": "Plug-1.0.jar", "glob": "Plug-*.jar",
                 "url": "https://x/Plug-1.0.jar", "sha256": "0" * 64}
        latest = {"version": "2.0", "file": "Plug-2.0.jar", "url": "https://x/Plug-2.0.jar",
                  "sha256_published": "1" * 64}
        lock = {"Plug": entry, "Hand": {"source": "manual", "version": "9", "file": "H.jar", "note": "n"}}
        with tempfile.TemporaryDirectory() as td:
            lockpath = pathlib.Path(td) / "plugins.lock.json"
            with mock.patch.object(aup, "LOCK", lockpath), \
                 mock.patch.object(aup, "resolve", return_value=latest), \
                 mock.patch.object(aup, "download_hashes", return_value=("1" * 64, "2" * 128)):
                self.assertEqual(aup.update(lock), 0)
            written = json.loads(lockpath.read_text(encoding="utf-8"))
        self.assertEqual(written["Plug"]["version"], "2.0")
        self.assertEqual(written["Plug"]["sha256"], "1" * 64)
        self.assertEqual(written["Hand"]["version"], "9")


if __name__ == "__main__":
    unittest.main()
