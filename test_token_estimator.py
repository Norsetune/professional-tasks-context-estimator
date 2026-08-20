import tempfile
import unittest
import zipfile
from pathlib import Path

from token_estimator import (
    DEFAULT_MAX_CONTEXT_TOKENS,
    estimate_file,
    estimate_image_tokens_from_dimensions,
    estimate_prompt,
    extract_supported_from_zip,
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


if __name__ == "__main__":
    unittest.main()
