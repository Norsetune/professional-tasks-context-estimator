import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from token_estimator import (
    DEFAULT_MAX_CONTEXT_TOKENS,
    estimate_file,
    estimate_image_tokens_from_dimensions,
    estimate_prompt,
    extract_supported_from_zip,
    inspect_zip,
    summarize_project,
)


class EstimatorTests(unittest.TestCase):
    def test_prompt_does_not_satisfy_source_minimum(self):
        prompt = estimate_prompt("hello " * 10000)
        summary = summarize_project(
            required_estimates=[],
            all_estimates=[],
            prompt_estimate=prompt,
            min_files=1,
            min_source_tokens=100,
            max_context_tokens=DEFAULT_MAX_CONTEXT_TOKENS,
        )
        self.assertFalse(summary["source_met"])
        self.assertEqual(summary["required_source_tokens"], 0)
        self.assertGreater(summary["prompt_tokens"], 0)

    def test_required_subset_is_separate_from_all_uploaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "a.txt"
            b = root / "b.txt"
            a.write_text("alpha " * 1000, encoding="utf-8")
            b.write_text("beta " * 1000, encoding="utf-8")
            ea = estimate_file(a)
            eb = estimate_file(b)
            summary = summarize_project(
                required_estimates=[ea],
                all_estimates=[ea, eb],
                min_files=1,
                min_source_tokens=1,
                max_context_tokens=DEFAULT_MAX_CONTEXT_TOKENS,
            )
            self.assertEqual(summary["required_file_count"], 1)
            self.assertEqual(summary["uploaded_file_count"], 2)
            self.assertGreater(summary["all_source_tokens"], summary["required_source_tokens"])

    def test_image_proxy_positive_for_normal_image(self):
        self.assertGreater(estimate_image_tokens_from_dimensions(1024, 768), 0)
        self.assertEqual(estimate_image_tokens_from_dimensions(32, 32), 0)

    def test_zip_path_traversal_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / "test.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("safe/a.txt", "hello")
                archive.writestr("../evil.txt", "bad")
            extracted = extract_supported_from_zip(archive_path, root / "out")
            names = [name for _, name in extracted]
            self.assertEqual(names, ["safe/a.txt"])
            self.assertFalse((root / "evil.txt").exists())

    def test_zip_preflight_reports_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "test.zip"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("a.txt", "hello" * 100)
                archive.writestr("b.csv", "x,y\n1,2\n")
            info = inspect_zip(archive_path)
            self.assertEqual(info["members"], 2)
            self.assertEqual(info["supported_members"], 2)
            self.assertGreater(info["uncompressed_bytes"], 0)
            self.assertGreater(info["largest_member_bytes"], 0)

    def test_zip_member_limit_is_enforced_without_large_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "test.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("a.txt", "01234567890")
            with patch("token_estimator.MAX_ZIP_MEMBER_BYTES", 10):
                with self.assertRaises(ValueError):
                    inspect_zip(archive_path)

    def test_large_json_uses_incremental_path_when_threshold_is_small(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.json"
            path.write_text('{"alpha": "' + ("x" * 1000) + '"}', encoding="utf-8")
            with patch("token_estimator.LARGE_JSON_STREAM_THRESHOLD_BYTES", 10):
                estimate = estimate_file(path)
            self.assertGreater(estimate.text_tokens, 0)
            self.assertIn("read incrementally", estimate.extraction_notes)

    def test_large_plain_text_is_counted_without_full_document_return(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.txt"
            # Large enough to exercise many incremental additions without making the test slow.
            with path.open("w", encoding="utf-8") as handle:
                for _ in range(50_000):
                    handle.write("alpha beta gamma delta epsilon\n")
            estimate = estimate_file(path)
            self.assertGreater(estimate.characters, 1_000_000)
            self.assertGreater(estimate.words, 200_000)
            self.assertGreater(estimate.text_tokens, 0)
            self.assertIn("incrementally", estimate.extraction_notes)


if __name__ == "__main__":
    unittest.main()
