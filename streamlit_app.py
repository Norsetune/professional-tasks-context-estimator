import tempfile
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import streamlit as st

from token_estimator import (
    DEFAULT_MAX_CONTEXT_TOKENS,
    DEFAULT_MIN_FILES,
    DEFAULT_MIN_SOURCE_TOKENS,
    IMAGE_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    estimate_file,
    estimate_prompt,
    extract_supported_from_zip,
    summarize_project,
)


st.set_page_config(
    page_title="Professional Tasks Context Range Estimator",
    layout="wide",
)

st.title("Professional Tasks Context Range Estimator")
st.caption(
    "Checks whether the files required by a prompt are deep enough for Professional Tasks "
    "while separately monitoring an editable maximum context ceiling. Text and image estimates "
    "are shown separately because image-token estimates are less precise."
)

with st.expander("Project thresholds", expanded=True):
    c1, c2, c3 = st.columns(3)
    with c1:
        min_files = st.number_input(
            "Minimum required files",
            min_value=1,
            value=DEFAULT_MIN_FILES,
            step=1,
            help="Professional Tasks task-writing default: at least 10 files must be needed to solve the prompt.",
        )
    with c2:
        min_source_tokens = st.number_input(
            "Minimum required source context",
            min_value=1_000,
            value=DEFAULT_MIN_SOURCE_TOKENS,
            step=10_000,
            help="Default: 256,000 source tokens. Prompt tokens do not count toward this minimum.",
        )
    with c3:
        max_context_tokens = st.number_input(
            "Maximum context limit",
            min_value=10_000,
            value=DEFAULT_MAX_CONTEXT_TOKENS,
            step=50_000,
            help="Editable maximum context limit used for the upper-range safety check.",
        )

if int(max_context_tokens) <= int(min_source_tokens):
    st.error("Maximum context limit must be greater than the minimum source-context threshold.")
    st.stop()

st.info(
    "Minimum check: selected **required source files only**. Prompt text never helps satisfy the 256k minimum.  "
    "Maximum check: the **entire uploaded source set + prompt** is used as a conservative worst-case estimate."
)

prompt_text = st.text_area(
    "Paste prompt text here",
    height=150,
    placeholder="Optional: include the actual task prompt so it contributes to the maximum-context estimate.",
)

upload_types = sorted(
    {extension.lstrip(".") for extension in SUPPORTED_EXTENSIONS} | {"zip"}
)

uploads = st.file_uploader(
    "Upload source files or an environment ZIP",
    type=upload_types,
    accept_multiple_files=True,
    help="ZIP archives are unpacked safely and supported files inside them are scanned recursively.",
)


def scan_uploads(uploaded_files, maximum_tokens: int):
    estimates = []
    errors = []
    with tempfile.TemporaryDirectory(prefix="professional_tasks_scan_") as tmpdir:
        root = Path(tmpdir)
        for upload_index, uploaded in enumerate(uploaded_files):
            safe_name = Path(uploaded.name).name
            upload_dir = root / f"upload_{upload_index:04d}"
            upload_dir.mkdir(parents=True, exist_ok=True)
            local_path = upload_dir / safe_name
            local_path.write_bytes(uploaded.getvalue())

            if local_path.suffix.lower() == ".zip":
                try:
                    extracted = extract_supported_from_zip(local_path, upload_dir / "unzipped")
                except Exception as exc:
                    errors.append(f"{safe_name}: ZIP extraction failed — {exc}")
                    continue

                if not extracted:
                    errors.append(f"{safe_name}: no supported files found inside ZIP")
                    continue

                for nested_path, relative_name in extracted:
                    display_name = f"{safe_name}::{relative_name}"
                    try:
                        estimates.append(
                            estimate_file(
                                nested_path,
                                maximum_tokens=maximum_tokens,
                                display_name=display_name,
                            )
                        )
                    except Exception as exc:
                        errors.append(f"{display_name}: estimate failed — {exc}")
            else:
                try:
                    estimates.append(
                        estimate_file(
                            local_path,
                            maximum_tokens=maximum_tokens,
                            display_name=safe_name,
                        )
                    )
                except Exception as exc:
                    errors.append(f"{safe_name}: estimate failed — {exc}")

    return estimates, errors


if st.button("Scan files", type="primary"):
    if not uploads:
        st.warning("Upload at least one file or ZIP first.")
    else:
        with st.spinner("Extracting text, inspecting images, and estimating context…"):
            estimates, scan_errors = scan_uploads(uploads, int(max_context_tokens))
        st.session_state["pt_estimates"] = [asdict(item) for item in estimates]
        st.session_state["pt_scan_errors"] = scan_errors
        st.session_state["pt_scan_maximum"] = int(max_context_tokens)
        st.session_state["pt_required_names"] = [item.file for item in estimates]


if "pt_estimates" in st.session_state:
    # Rehydrate dataclasses after a Streamlit rerun.
    from token_estimator import FileEstimate

    estimates = [FileEstimate(**row) for row in st.session_state["pt_estimates"]]
    scan_errors = st.session_state.get("pt_scan_errors", [])

    if st.session_state.get("pt_scan_maximum") != int(max_context_tokens):
        st.warning(
            "The maximum limit changed after the scan. Re-scan files so per-file risk labels use the new ceiling. "
            "The overall calculation below already uses the current value."
        )

    for error in scan_errors:
        st.warning(error)

    if estimates:
        st.subheader("1. Choose the files the prompt actually requires")
        all_names = [item.file for item in estimates]

        # Keep only still-valid selections when the scan changes.
        current_selection = [
            name
            for name in st.session_state.get("pt_required_names", all_names)
            if name in all_names
        ]

        b1, b2, _ = st.columns([1, 1, 5])
        with b1:
            if st.button("Select all"):
                current_selection = all_names
                st.session_state["pt_required_names"] = current_selection
        with b2:
            if st.button("Clear selection"):
                current_selection = []
                st.session_state["pt_required_names"] = current_selection

        required_names = st.multiselect(
            "Required files",
            options=all_names,
            default=current_selection,
            help=(
                "Select only the files the model genuinely needs to solve the prompt. "
                "The 10-file and 256k checks use this subset."
            ),
        )
        st.session_state["pt_required_names"] = required_names

        required_lookup = set(required_names)
        required_estimates = [item for item in estimates if item.file in required_lookup]
        prompt_est = estimate_prompt(prompt_text) if prompt_text.strip() else None

        summary = summarize_project(
            required_estimates=required_estimates,
            all_estimates=estimates,
            prompt_estimate=prompt_est,
            min_files=int(min_files),
            min_source_tokens=int(min_source_tokens),
            max_context_tokens=int(max_context_tokens),
        )

        st.subheader("2. Context range result")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(
            "Required files",
            f"{summary['required_file_count']} / {summary['min_files']}",
            (
                "Requirement met"
                if summary["files_met"]
                else f"{summary['required_file_shortfall']} more needed"
            ),
        )
        m2.metric(
            "Required source context",
            f"{summary['required_source_tokens']:,}",
            (
                f"≥ {summary['min_source_tokens']:,} required"
                if summary["source_met"]
                else f"{summary['source_token_shortfall']:,} short"
            ),
        )
        m3.metric(
            "Image-context estimate",
            f"~{summary['required_image_tokens']:,}",
            "Lower-confidence proxy",
        )
        m4.metric(
            "Full uploaded turn",
            f"{summary['full_uploaded_turn_tokens']:,}",
            f"{summary['percent_of_maximum']}% of max",
        )

        min_progress = min(summary["required_source_tokens"] / int(min_source_tokens), 1.0)
        st.write("**Minimum source-context progress**")
        st.progress(min_progress)

        max_progress = min(summary["full_uploaded_turn_tokens"] / int(max_context_tokens), 1.0)
        st.write("**Maximum-context load**")
        st.progress(max_progress)

        status = summary["overall_status"]
        if status == "BELOW_PROJECT_MINIMUM":
            parts = []
            if not summary["files_met"]:
                parts.append(
                    f"needs {summary['required_file_shortfall']} more genuinely required file(s)"
                )
            if not summary["source_met"]:
                parts.append(
                    f"needs about {summary['source_token_shortfall']:,} more required source tokens"
                )
            st.error("BELOW PROJECT MINIMUM — " + "; ".join(parts) + ".")
        elif status == "OVER_SELECTED_MAXIMUM":
            st.error(
                "OVER SELECTED MAXIMUM — the minimum is satisfied, but the conservative full uploaded-turn "
                "estimate exceeds the selected maximum."
            )
        elif status == "MEETS_MINIMUM_BUT_NEAR_MAXIMUM":
            st.warning(
                "MEETS PROJECT MINIMUM, BUT CONTEXT IS NEAR THE SELECTED MAXIMUM — consider trimming unused "
                "or oversized source files before running the task."
            )
        else:
            if summary["minimum_status"] == "MEETS_MINIMUM_NARROW_MARGIN":
                st.success(
                    "MEETS PROFESSIONAL TASKS REQUIREMENTS — source context clears the minimum, but with a "
                    "narrow estimation margin."
                )
            else:
                st.success(
                    "MEETS PROFESSIONAL TASKS REQUIREMENTS — comfortably above the source minimum and within "
                    "the selected maximum."
                )

        if summary["maximum_status"] == "HIGH_CONTEXT_LOAD":
            st.warning(
                "High context load: the full uploaded-turn estimate is at least 70% of the selected maximum."
            )
        elif summary["maximum_status"] == "NEAR_MAXIMUM":
            st.warning(
                "Near maximum: the full uploaded-turn estimate is at least 85% of the selected maximum."
            )
        elif summary["maximum_status"] == "CRITICAL":
            st.error(
                "Critical context load: the full uploaded-turn estimate is at least 95% of the selected maximum."
            )

        st.caption(
            f"Required subset: {summary['required_text_tokens']:,} text + ~{summary['required_image_tokens']:,} image "
            f"= {summary['required_source_tokens']:,} source tokens. Prompt: {summary['prompt_tokens']:,} tokens. "
            f"Entire uploaded source set: {summary['all_source_tokens']:,} tokens."
        )

        st.subheader("Environment-utilization advisory")
        utilization = summary["required_file_utilization_pct"]
        advisory_icon = "✅" if summary["environment_utilization_advisory_met"] else "ℹ️"
        st.write(
            f"{advisory_icon} Required subset uses **{summary['required_file_count']} of "
            f"{summary['uploaded_file_count']} uploaded files ({utilization}%)**."
        )
        st.caption(
            "The Professional Tasks task-writing guide explicitly requires at least 10 files. The separate "
            "Environment Creation guide sets a stronger environment-design target: future prompts should need "
            "20 files or at least 50% of the environment. This advisory does not override your task card or the "
            "task-writing minimum."
        )

        rows = []
        for item in sorted(estimates, key=lambda x: x.estimated_tokens, reverse=True):
            rows.append(
                {
                    "Required?": item.file in required_lookup,
                    "File": item.file,
                    "Type": item.extension,
                    "Size MB": item.size_mb,
                    "Text tokens": item.text_tokens,
                    "Image tokens (est.)": item.image_tokens,
                    "Combined est.": item.estimated_tokens,
                    "Images": item.image_count,
                    "Words": item.words,
                    "Max-risk band": item.maximum_risk,
                    "Notes": item.extraction_notes,
                }
            )

        dataframe = pd.DataFrame(rows)
        st.subheader("Per-file estimates")
        st.dataframe(dataframe, width="stretch", hide_index=True)

        st.subheader("Largest required contributors")
        if summary["largest_required_contributors"]:
            for item in summary["largest_required_contributors"][:5]:
                st.write(
                    f"- **{item['file']}** — {item['estimated_tokens']:,} total "
                    f"({item['text_tokens']:,} text + ~{item['image_tokens']:,} image)"
                )
        else:
            st.write("No required files selected.")

        report_df = dataframe.copy()
        csv_report = report_df.to_csv(index=False).encode("utf-8")

        json_report = {
            "summary": summary,
            "files": rows,
            "prompt": asdict(prompt_est) if prompt_est else None,
            "notes": {
                "minimum_basis": "selected required source files only; prompt excluded",
                "maximum_basis": "entire uploaded source set plus prompt",
                "image_estimate": (
                    "lower-confidence raster-dimension tiled-vision proxy; not the exact target-model tokenizer; "
                    "OCR text is not added to avoid obvious double counting"
                ),
                "maximum_default": "1,000,000 tokens; editable in the app",
            },
        }

        d1, d2 = st.columns(2)
        with d1:
            st.download_button(
                "Download CSV report",
                data=csv_report,
                file_name="professional_tasks_context_report.csv",
                mime="text/csv",
            )
        with d2:
            import json

            st.download_button(
                "Download JSON report",
                data=json.dumps(json_report, indent=2, ensure_ascii=False).encode("utf-8"),
                file_name="professional_tasks_context_report.json",
                mime="application/json",
            )

st.divider()
st.markdown(
    """
**How to interpret the estimates**

- **Text tokens** use `tiktoken` (`cl100k_base`) when available and otherwise a conservative character/word heuristic.
- **Image tokens** are intentionally shown separately. They use a generic tiled-vision proxy based on raster dimensions, so they are less certain than text-token estimates.
- **No OCR token padding is added.** Scanned/image text may be visible to a multimodal model, but adding OCR text on top of an image estimate would create obvious double-counting risk.
- The **256k minimum** is a project depth requirement, so being far below it is a failure rather than "safe."
- The **maximum context limit** defaults to **1,000,000 tokens** and can be edited in the Project thresholds panel.
"""
)

st.caption("Professional Tasks estimator forked from the original Spidey Context Budget Estimator.")

st.markdown(
    """
    <div style="
        text-align: center;
        color: #888;
        font-size: 0.85rem;
        margin-top: 2rem;
        padding-bottom: 1rem;
    ">
        Built by Kenneth Pedersen · 2026
    </div>
    """,
    unsafe_allow_html=True,
)
