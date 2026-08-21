# Professional Tasks Context Range Estimator

Professional Tasks context estimator for checking required file count, source-token depth, and maximum context load.

## Two project workflows

The app now separates the two parts of the Professional Tasks program at the top of the UI.

### Part 1 · Duck Environments — Environment Creation

Use this mode when creating/checking the **source environment itself**, before downstream Prompt + Rubric tasks are written.

The app checks:

- the assigned environment size band: **20–29, 30–50, 51–99, or 100+ files**
- a **256,000-token total depth indicator**
- at least **3 distinct file formats**
- text and estimated image context

**No upper context limit is applied in Environment Creation mode.** Dense and massive environments may exceed 1,000,000 tokens because the full environment is not equivalent to the context required by one downstream task.

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
