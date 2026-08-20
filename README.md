# Professional Tasks Context Range Estimator

A project-specific fork of the Spidey Context Budget Estimator for **Professional Tasks / Professional Duck**.

The key difference is that Professional Tasks has a **minimum reading-depth requirement**, not just an upper context-risk limit. The supplied task-writing guide states that a prompt must need **at least 10 files and 256k source tokens** from the environment. The separate Environment Creation guide also expects environments to be deep enough to support 256k+ tasks and sets a stronger environment-design target of **20 files or at least 50% of the environment** per future prompt.

## Default thresholds

- Minimum required files: **10**
- Minimum required source context: **256,000 tokens**
- Maximum context limit: **1,000,000 tokens** — **provisional and editable**

The supplied Professional Tasks guidelines confirm the 256k minimum but do **not** state a 1,000,000-token maximum. The app therefore treats 1M as a configurable working assumption rather than a project rule.

## What the app checks

### 1. Required subset / project minimum

You can upload the full environment, then select only the files that the prompt genuinely requires.

The minimum passes only when both are true:

- selected required files >= configured minimum (default 10), and
- selected required source context >= configured minimum (default 256,000 tokens).

**Prompt tokens do not count toward the 256k minimum.**

### 2. Full uploaded set / maximum safety

For a conservative ceiling check, the app uses:

`all uploaded source-file tokens + prompt tokens`

This helps catch a task that clears the 256k minimum but may still be too large to run reliably if the platform loads the full environment.

### 3. Text and image context separately

For each file the app reports:

- text tokens
- estimated image tokens
- combined estimate
- image count
- extraction notes

Text is estimated using `tiktoken` (`cl100k_base`) when available, with a fallback heuristic.

Image tokens use a **generic tiled-vision proxy based on raster dimensions**. This is deliberately labeled lower-confidence because the exact target-model image tokenizer has not been confirmed. OCR text is not added on top of the image estimate, which avoids obvious double counting.

## Supported input

Direct upload or CLI scanning supports:

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
- ZIP archives containing the formats above

ZIP archives are safely unpacked and scanned recursively. Unsupported files, path-traversal entries, and common macOS metadata entries are ignored.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run the Streamlit app

```bash
streamlit run streamlit_app.py
```

Then open the local Streamlit URL, upload individual files or an environment ZIP, click **Scan files**, select the files required by the prompt, and review the minimum/maximum status.

## Command line

All CLI input files are treated as required files.

```bash
python token_estimator.py /path/to/environment.zip
```

Include a prompt:

```bash
python token_estimator.py /path/to/environment.zip --prompt-file prompt.txt
```

Override thresholds:

```bash
python token_estimator.py /path/to/environment.zip \
  --min-files 10 \
  --min-tokens 256000 \
  --max-tokens 1000000
```

Write a JSON report:

```bash
python token_estimator.py /path/to/environment.zip --json-out report.json
```

## Status logic

Minimum status:

- `BELOW_MINIMUM_FILES_AND_CONTEXT`
- `BELOW_MINIMUM_FILES`
- `BELOW_MINIMUM_CONTEXT`
- `MEETS_MINIMUM_NARROW_MARGIN` — under 10% above the token minimum
- `COMFORTABLY_ABOVE_MINIMUM`

Maximum status:

- `COMFORTABLY_WITHIN_MAXIMUM` — under 70%
- `HIGH_CONTEXT_LOAD` — 70–85%
- `NEAR_MAXIMUM` — 85–95%
- `CRITICAL` — 95–100%
- `OVER_MAXIMUM`

The UI combines those into a simple overall message while keeping the two checks visible separately.

## Image-estimation caveats

The app can detect and estimate:

- standalone raster images
- raster images embedded in PDFs
- media files embedded in DOCX, PPTX, and XLSX packages

It cannot perfectly account for every multimodal representation. Vector drawings, charts rendered by the model pipeline, proprietary preprocessing, and model-specific image compression/tokenization can all differ from the proxy. Treat image-heavy results as an informed estimate, not an exact count.

## Why the maximum check uses the whole uploaded set

The minimum answers: **"Does the prompt genuinely require enough source material?"**

The maximum answers: **"Could the full fileset be too large if the platform/model loads all uploaded material?"**

Keeping these calculations separate avoids the old Spidey behavior where low token usage was automatically labeled safe even when Professional Tasks specifically requires high context depth.
