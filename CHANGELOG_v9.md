# v9.1 follow-up hardening

Changes added after reviewing a real Streamlit Community Cloud crash log:

- Pinned the dependency set to versions observed installing successfully on the app's Python 3.14.7 runtime.
- Explicitly pinned `pyarrow==24.0.0` because Community Cloud detected a known segfault in 25.0.1 and downgraded it automatically.
- Switched deprecated `import fitz` usage to `import pymupdf as fitz`.
- Suppressed only the known non-fatal openpyxl extension/style warnings that otherwise obscure scan diagnostics.
- Added a one-per-process `RUNTIME_EVENT` log containing Python/package versions and memory limits, making future crash logs much easier to compare.

# v9 — Stability + diagnostics

## What changed

- Streamed Streamlit uploads to temporary disk; removed the full-size `UploadedFile.getvalue()` copy.
- Streamed ZIP member extraction with 1 MB buffers.
- Retained the 3 GiB total uncompressed ZIP allowance, while adding 2 GiB per-member and suspicious-compression-ratio guards.
- Added incremental text/token metrics for heavy files so PDF pages, spreadsheet rows, CSV rows, text lines, HTML chunks, XML elements, and large JSON do not need to be assembled into one giant Python string.
- Avoided full embedded-image byte extraction when only dimensions are needed.
- Added a process-wide scan lock so only one heavy scan runs at a time.
- Added cgroup-aware memory headroom checks and a safe incomplete-scan state rather than continuing toward an OOM kill.
- Rotated uploader widget keys after scans so uploaded buffers can be released on rerun while results remain available.
- Added structured `SCAN_EVENT` logs with run IDs, timing, file/ZIP metadata, process RSS, and container memory.
- Added an in-app diagnostics expander and downloadable diagnostics JSON.
- Pinned dependency versions and added `psutil` for process memory reporting.
- Expanded unit tests from 4 to 8 cases, including ZIP preflight limits and incremental large-file paths.

## What did not change

- Part 1 vs Part 2 workflows.
- 10-file / 256k Professional Tasks minimum logic.
- Required-file subset selection.
- 1M editable tasking ceiling/warning behavior.
- Environment size-band, 256k baseline, 1M advisory density target, or 3-format checks.
- Supported user-facing input formats.
- `cl100k_base` text-token method when `tiktoken` is available.
- Image-token proxy formula.
- CSV/JSON report structure for the core estimator results.

## Validation performed

- `python -m unittest -v`: 8/8 tests pass.
- `python -m py_compile token_estimator.py streamlit_app.py`: pass.
- Regression check against two real project PDFs: text-token estimates differed from v8 by ~0.20–0.24%; image-token estimates were identical. The small text difference comes from bounded extraction/tokenization boundaries rather than a changed token model or project rule.
