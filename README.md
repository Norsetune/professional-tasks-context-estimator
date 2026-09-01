# Professional Tasks Context Range Estimator

Professional Tasks context estimator for checking required file count, source-token depth, and maximum context load.

## Two project workflows

The app now separates the two parts of the Professional Tasks program at the top of the UI.

### Part 1 · Duck Environments — Environment Creation

Use this mode when creating/checking the **source environment itself**, before downstream Prompt + Rubric tasks are written.

The app checks:

- the assigned environment size band: **20–29, 30–50, 51–99, or 100+ files**
- a **256,000-token baseline depth floor**, particularly for the smallest environment size band
- a **1,000,000-token advisory density target** (guidance only, not pass/fail)
- at least **3 distinct file formats**
- text and estimated image context

**No upper context limit is applied in Environment Creation mode.** Dense and massive environments may exceed 1,000,000 tokens because the full environment is not equivalent to the context required by one downstream task. The 256k figure is used as a baseline depth floor, particularly for the smallest environment size band; 1M is shown separately as an advisory density target. The app does not invent higher token minima for larger file-count bands.

The Environment Creation guidance also expects the environment to support future prompts that require **20 files or at least 50% of the environment**. That environment-design utilization rule is separate from the **10-file minimum** used for an actual Professional Tasks Prompt + Rubric task.

### Part 2 · Professional Tasks — Prompt + Rubric

Use this mode when creating a task from an **existing environment**.

Default checks:

- minimum **10 files genuinely required by the prompt**
- minimum **256,000 required source tokens**
- editable **1,000,000-token tasking ceiling/warning**
- required subset is checked separately from the full uploaded environment
- prompt text is excluded from the 256k minimum
- full uploaded source set + prompt is used for the conservative upper-context estimate

This is the workflow where the selected files/context are processed during model runs, so the upper-context warning remains visible for tasking stability.

## Supported input

Direct upload or ZIP scanning supports:

- PDF
- DOCX
- PPTX
- XLSX
- CSV
- TXT / MD / LOG
- HTML / HTM
- XML
- JSON
- PNG / JPG / JPEG / WEBP / BMP / TIFF
- ZIP archives containing supported formats

## Token-estimation caveats

Text uses `tiktoken` (`cl100k_base`) when available, with a conservative fallback heuristic.

Image tokens use a generic tiled-vision proxy based on raster dimensions. They are shown separately because the exact target-model image tokenizer is not known. OCR text is not added on top of the image estimate to avoid obvious double counting.

## Run

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

The existing CLI remains focused on the Prompt + Rubric calculation. The dual-workflow distinction is implemented in the Streamlit UI.

## v9 stability and diagnostics

v9.1 keeps the same two estimator workflows, supported formats, project thresholds, required-file selection, report downloads, and text/image token methodology, while changing how heavy files are processed so the Streamlit process uses substantially less peak memory.

### Reliability changes

- Uploaded files are copied to temporary disk with a 1 MB streaming buffer instead of `UploadedFile.getvalue()`, avoiding a second full-size in-memory copy.
- ZIP members are extracted with streaming copies instead of `source.read()`. The existing 3 GiB total uncompressed ZIP allowance is retained; v9 also checks member count, per-member size, and suspicious compression ratios before extraction.
- Text metrics for PDF, DOCX, PPTX, XLSX, CSV, TXT/MD/LOG, HTML, XML, and large JSON are accumulated incrementally instead of building one full extracted-text string in RAM. The same `cl100k_base` tokenizer is used when `tiktoken` is installed.
- Embedded Office images are inspected without reading each complete image into a Python bytes object, and PDF image dimensions are taken from PDF metadata instead of extracting image bytes.
- Only one heavy scan runs at a time in a Streamlit process. Other users receive a short retry message instead of competing for memory.
- The file-uploader widget is rotated after a completed scan so Streamlit can release the uploaded `BytesIO` buffers while the compact scan results remain in `session_state`.
- A cgroup-aware memory guard stops a scan cleanly at extreme memory pressure (90% container usage or less than 192 MB headroom) instead of intentionally pushing the process toward an OOM kill. An incomplete scan is clearly marked and must not be used as the final compliance result.
- Dependency versions are pinned in `requirements.txt` so a reboot/redeploy does not silently resolve a different package set.

### Diagnostics

Every scan gets a short run ID. v9 writes structured `SCAN_EVENT` JSON records to standard output, which means they appear in **Streamlit Community Cloud → Manage app → Cloud logs**. Events include scan start/end, ZIP preflight metadata, file start/end/error, elapsed time, process RSS, cgroup/container usage, and any memory-safety stop. File contents are never logged.

The app also exposes a **Scan diagnostics** expander after each run. It shows the run ID, completion state, number of files processed, peak RSS/container usage, ZIP preflight data, and a downloadable diagnostics JSON file. To keep `session_state` small, only the first 500 diagnostic events are retained in the UI/report; all emitted events still go to Cloud logs.

If the app is ever terminated unexpectedly, search the Cloud logs for the last `SCAN_EVENT`. The final `file_start` without a matching `file_complete`, or a `memory_safety_stop`, should identify where the scan was when the process failed.

### Estimation continuity

The estimator still uses the same source material and token model; the change is that large documents are counted in bounded chunks. Chunk boundaries can produce a very small token-count difference versus v8 because BPE tokenization can merge across boundaries. In a local regression check against two real project PDFs, v9 differed from v8 by about **0.20–0.24%** in text-token estimates while producing identical image-token estimates. This is far below the estimator's existing approximation uncertainty and does not change the project thresholds or workflow logic.


## v9.1 runtime diagnostics

A `RUNTIME_EVENT` line is written once whenever the Streamlit process starts. It records the Python and key package versions plus the container memory snapshot. The dependency set is pinned to the versions observed on the current Community Cloud Python 3.14.7 deployment, with `pyarrow==24.0.0` pinned explicitly because the platform flagged 25.0.1 as a known segfaulting release.
