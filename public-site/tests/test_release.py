import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release", ROOT / "scripts/prepare_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / "artifacts"
        scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="release-test-", dir=scratch)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / "dist", self.root / "dist")

    def test_explicit_owner_approval_required(self):
        with self.assertRaises(ValueError):
            release.prepare(self.root, "赣ICP备00000000号-1", False)
        self.assertFalse((self.root / "release").exists())

    def test_rejects_non_filing_input(self):
        for invalid in ["", "备案中", "苏ICP备00000000号-1", "皖ICP备00000000号-1", '<script>alert(1)</script>', "赣ICP备00000000号-1\n"]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                release.prepare(self.root, invalid, True)

    def test_fixture_number_is_used_only_in_temporary_test_release(self):
        target = release.prepare(self.root, "赣ICP备00000000号-1", True)
        for path in target.glob("*.html"):
            text = path.read_text(encoding="utf-8")
            self.assertIn('href="https://beian.miit.gov.cn/"', text)
            self.assertIn("赣ICP备00000000号-1", text)
            self.assertNotIn("本地预览版本", text)
            self.assertNotIn("noindex,nofollow", text)
        self.assertIn("Disallow: /", (self.root / "dist/robots.txt").read_text())
        self.assertIn("Allow: /", (target / "robots.txt").read_text())
        self.assertIn("ICP 备案尚未完成", (self.root / "dist/index.html").read_text(encoding="utf-8"))

    def test_existing_release_is_preserved(self):
        (self.root / "release").mkdir()
        sentinel = self.root / "release/old.txt"
        sentinel.write_text("keep")
        with self.assertRaises(ValueError):
            release.prepare(self.root, "赣ICP备00000000号-1", True)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_binary_screenshot_is_preserved_exactly(self):
        source = (self.root / "dist/workbench.png").read_bytes()
        self.assertTrue(source.startswith(b"\x89PNG\r\n\x1a\n"))
        target = release.prepare(self.root, "赣ICP备00000000号-1", True)
        self.assertEqual((target / "workbench.png").read_bytes(), source)

    def test_extra_files_block_release(self):
        (self.root / "dist/.env").write_text("synthetic-test-only")
        with self.assertRaises(ValueError):
            release.prepare(self.root, "赣ICP备00000000号-1", True)

    def test_subject_number_is_preserved_without_invented_suffix(self):
        target = release.prepare(self.root, "赣ICP备00000000号", True)
        text = (target / "index.html").read_text(encoding="utf-8")
        self.assertIn('>赣ICP备00000000号</a>', text)
        self.assertNotIn("赣ICP备00000000号-1", text)


if __name__ == "__main__":
    unittest.main()
