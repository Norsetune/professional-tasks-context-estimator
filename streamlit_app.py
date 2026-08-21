# Professional Tasks Context Range Estimator — v8
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import streamlit as st

from token_estimator import (
    DEFAULT_MAX_CONTEXT_TOKENS,
    DEFAULT_MIN_FILES,
    DEFAULT_MIN_SOURCE_TOKENS,
    SUPPORTED_EXTENSIONS,
    FileEstimate,
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
    "Estimate source context for either part of the Professional Tasks program. "
    "The two workflows use different file-count and context rules."
)

WORKFLOW_ENV = "Part 1 · Duck Environments — Environment Creation"
WORKFLOW_TASK = "Part 2 · Professional Tasks — Prompt + Rubric"

workflow = st.radio(
    "Which part of the program are you checking?",
    options=[WORKFLOW_ENV, WORKFLOW_TASK],
    horizontal=True,
    help=(
        "Choose Prompt + Rubric when building a task from an existing environment. "
        "Choose Environment Creation when checking the source environment itself."
    ),
)

if workflow == WORKFLOW_TASK:
    st.info(
        "### Part 2 · Professional Tasks — Prompt + Rubric\n"
        "You are building a **task from an existing environment**. The task must genuinely require "
        "**at least 10 files and 256k source tokens**. Because these files are processed during model runs, "
        "the app also keeps the **1,000,000-token tasking ceiling/warning** visible for stability."
    )
else:
    st.info(
        "### Part 1 · Duck Environments — Environment Creation\n"
        "You are checking the **source environment itself**, before prompts/rubrics are created from it. "
        "Environment size follows the assigned file-count band and should be dense enough to support "
        "**256k+ token tasks**. **No upper token limit is applied in this mode**, so dense or massive "
        "environments may exceed 1,000,000 tokens."
    )


def scan_uploads(uploaded_files, maximum_tokens: int):
    """Scan direct uploads or ZIPs using the common extraction engine."""
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
                    extracted = extract_supported_from_zip(
                        local_path,
                        upload_dir / "unzipped",
                    )
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


upload_types = sorted(
    {extension.lstrip(".") for extension in SUPPORTED_EXTENSIONS} | {"zip"}
)


if workflow == WORKFLOW_TASK:
    # -------------------------
    # PART 2: PROMPT + RUBRIC
    # -------------------------
    with st.expander("Professional Tasks thresholds", expanded=True):
        c1, c2, c3 = st.columns(3)

        with c1:
            min_files = st.number_input(
                "Minimum required files",
                min_value=1,
                value=DEFAULT_MIN_FILES,
                step=1,
                help=(
                    "Professional Tasks / Prompt + Rubric default: at least 10 files "
                    "must genuinely be needed to solve the prompt."
                ),
            )

        with c2:
            min_source_tokens = st.number_input(
                "Minimum required source context",
                min_value=1_000,
                value=DEFAULT_MIN_SOURCE_TOKENS,
                step=10_000,
                help=(
                    "Default: 256,000 source tokens. Prompt text does not count "
                    "toward this minimum."
                ),
            )

        with c3:
            max_context_tokens = st.number_input(
                "Maximum context limit",
                min_value=10_000,
                value=DEFAULT_MAX_CONTEXT_TOKENS,
                step=50_000,
                help=(
                    "Editable upper tasking threshold used to flag potentially unstable "
                    "full-context loads."
                ),
            )

    if int(max_context_tokens) <= int(min_source_tokens):
        st.error(
            "Maximum context limit must be greater than the minimum source-context threshold."
        )
        st.stop()

    st.markdown(
        """
**Minimum requirement:** Only files marked as **required for the prompt** count toward the
**10-file** and **256k-token** checks. **Prompt text is excluded from these calculations.**

**Maximum context estimate:** The **entire uploaded source set plus the prompt** is counted
to provide a conservative worst-case tasking estimate.
"""
    )

    prompt_text = st.text_area(
        "Paste prompt text here",
        height=150,
        placeholder=(
            "Optional: include the actual task prompt so it contributes to "
            "the maximum-context estimate."
        ),
    )

    uploads = st.file_uploader(
        "Upload source files or the existing environment ZIP",
        type=upload_types,
        accept_multiple_files=True,
        key="task_uploads",
        help=(
            "Upload the environment used by the task. ZIP archives are unpacked safely "
            "and supported files inside them are scanned recursively."
        ),
    )

    if st.button("Scan task files", type="primary", key="scan_task"):
        if not uploads:
            st.warning("Upload at least one file or ZIP first.")
        else:
            with st.spinner(
                "Extracting text, inspecting images, and estimating task context…"
            ):
                estimates, scan_errors = scan_uploads(
                    uploads,
                    int(max_context_tokens),
                )

            st.session_state["task_estimates"] = [asdict(item) for item in estimates]
            st.session_state["task_scan_errors"] = scan_errors
            st.session_state["task_scan_maximum"] = int(max_context_tokens)
            st.session_state["task_required_names"] = [
                item.file for item in estimates
            ]

    if "task_estimates" in st.session_state:
        estimates = [
            FileEstimate(**row)
            for row in st.session_state["task_estimates"]
        ]
        scan_errors = st.session_state.get("task_scan_errors", [])

        if st.session_state.get("task_scan_maximum") != int(max_context_tokens):
            st.warning(
                "The maximum limit changed after the scan. Re-scan files so per-file "
                "risk labels use the new ceiling. The overall calculation below already "
                "uses the current value."
            )

        for error in scan_errors:
            st.warning(error)

        if estimates:
            st.subheader("1. Select the files this task genuinely requires")
            all_names = [item.file for item in estimates]

            current_selection = [
                name
                for name in st.session_state.get(
                    "task_required_names",
                    all_names,
                )
                if name in all_names
            ]

            b1, b2, _ = st.columns([1, 1, 5])
            with b1:
                if st.button("Select all", key="task_select_all"):
                    current_selection = all_names
                    st.session_state["task_required_names"] = current_selection
            with b2:
                if st.button("Clear selection", key="task_clear_all"):
                    current_selection = []
                    st.session_state["task_required_names"] = current_selection

            required_names = st.multiselect(
                "Required files for this prompt",
                options=all_names,
                default=current_selection,
                help=(
                    "Select only files the model genuinely needs to solve this Prompt + Rubric task. "
                    "The 10-file and 256k checks use this subset."
                ),
            )
            st.session_state["task_required_names"] = required_names

            required_lookup = set(required_names)
            required_estimates = [
                item for item in estimates
                if item.file in required_lookup
            ]
            prompt_est = (
                estimate_prompt(prompt_text)
                if prompt_text.strip()
                else None
            )

            summary = summarize_project(
                required_estimates=required_estimates,
                all_estimates=estimates,
                prompt_estimate=prompt_est,
                min_files=int(min_files),
                min_source_tokens=int(min_source_tokens),
                max_context_tokens=int(max_context_tokens),
            )

            st.subheader("2. Professional Tasks result")

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

            min_progress = min(
                summary["required_source_tokens"] / int(min_source_tokens),
                1.0,
            )
            st.write("**Minimum source-context progress**")
            st.progress(min_progress)

            max_progress = min(
                summary["full_uploaded_turn_tokens"] / int(max_context_tokens),
                1.0,
            )
            st.write("**Maximum-context load**")
            st.progress(max_progress)

            status = summary["overall_status"]

            if status == "BELOW_PROJECT_MINIMUM":
                parts = []
                if not summary["files_met"]:
                    parts.append(
                        f"needs {summary['required_file_shortfall']} more "
                        "genuinely required file(s)"
                    )
                if not summary["source_met"]:
                    parts.append(
                        f"needs about {summary['source_token_shortfall']:,} "
                        "more required source tokens"
                    )
                st.error(
                    "BELOW PROJECT MINIMUM — "
                    + "; ".join(parts)
                    + "."
                )

            elif status == "OVER_SELECTED_MAXIMUM":
                st.error(
                    "OVER SELECTED MAXIMUM — the minimum is satisfied, but the "
                    "conservative full uploaded-turn estimate exceeds the selected "
                    "tasking ceiling."
                )

            elif status == "MEETS_MINIMUM_BUT_NEAR_MAXIMUM":
                st.warning(
                    "MEETS PROJECT MINIMUM, BUT CONTEXT IS NEAR THE SELECTED MAXIMUM — "
                    "consider trimming unused or oversized source files before running "
                    "the task."
                )

            else:
                if summary["minimum_status"] == "MEETS_MINIMUM_NARROW_MARGIN":
                    st.success(
                        "MEETS PROFESSIONAL TASKS REQUIREMENTS — source context clears "
                        "the minimum, but with a narrow estimation margin."
                    )
                else:
                    st.success(
                        "MEETS PROFESSIONAL TASKS REQUIREMENTS — comfortably above "
                        "the source minimum and within the selected maximum."
                    )

            if summary["maximum_status"] == "HIGH_CONTEXT_LOAD":
                st.warning(
                    "High context load: the full uploaded-turn estimate is at least "
                    "70% of the selected maximum."
                )
            elif summary["maximum_status"] == "NEAR_MAXIMUM":
                st.warning(
                    "Near maximum: the full uploaded-turn estimate is at least "
                    "85% of the selected maximum."
                )
            elif summary["maximum_status"] == "CRITICAL":
                st.error(
                    "Critical context load: the full uploaded-turn estimate is at least "
                    "95% of the selected maximum."
                )

            st.caption(
                f"Required subset: {summary['required_text_tokens']:,} text + "
                f"~{summary['required_image_tokens']:,} image = "
                f"{summary['required_source_tokens']:,} source tokens. "
                f"Prompt: {summary['prompt_tokens']:,} tokens. "
                f"Entire uploaded source set: {summary['all_source_tokens']:,} tokens."
            )

            st.caption(
                "This result evaluates the **Prompt + Rubric task**, not whether the "
                "source environment itself satisfies Duck Environment Creation requirements."
            )

            rows = []
            for item in sorted(
                estimates,
                key=lambda x: x.estimated_tokens,
                reverse=True,
            ):
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
            st.dataframe(
                dataframe,
                width="stretch",
                hide_index=True,
            )

            st.subheader("Largest required contributors")
            if summary["largest_required_contributors"]:
                for item in summary["largest_required_contributors"][:5]:
                    st.write(
                        f"- **{item['file']}** — {item['estimated_tokens']:,} total "
                        f"({item['text_tokens']:,} text + "
                        f"~{item['image_tokens']:,} image)"
                    )
            else:
                st.write("No required files selected.")

            csv_report = dataframe.to_csv(index=False).encode("utf-8")

            json_report = {
                "workflow": "professional_tasks_prompt_rubric",
                "summary": summary,
                "files": rows,
                "prompt": asdict(prompt_est) if prompt_est else None,
                "notes": {
                    "minimum_basis": (
                        "selected required source files only; prompt excluded"
                    ),
                    "maximum_basis": (
                        "entire uploaded source set plus prompt"
                    ),
                    "image_estimate": (
                        "lower-confidence raster-dimension tiled-vision proxy; "
                        "not the exact target-model tokenizer; OCR text is not "
                        "added to avoid obvious double counting"
                    ),
                    "maximum_default": (
                        "1,000,000 tokens; editable in the app"
                    ),
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
                st.download_button(
                    "Download JSON report",
                    data=json.dumps(
                        json_report,
                        indent=2,
                        ensure_ascii=False,
                    ).encode("utf-8"),
                    file_name="professional_tasks_context_report.json",
                    mime="application/json",
                )


else:
    # -------------------------
    # PART 1: ENVIRONMENT CREATION
    # -------------------------
    st.markdown(
        """
**Environment Creation is intentionally different:** this mode checks the source collection
itself. It does **not** apply the 1M tasking ceiling, because the environment may contain far
more material than any single downstream task needs to process.
"""
    )

    with st.expander("Environment checks", expanded=True):
        e1, e2, e3 = st.columns(3)

        with e1:
            size_band = st.selectbox(
                "Assigned environment size band",
                options=[
                    "20–29 files",
                    "30–50 files",
                    "51–99 files",
                    "100+ files",
                ],
                help=(
                    "Choose the band on the Environment Creation task card. "
                    "The band is the hard file-count boundary; the task card may also "
                    "give a target count within it."
                ),
            )

        with e2:
            env_depth_tokens = st.number_input(
                "Required environment depth floor",
                min_value=1_000,
                value=256_000,
                step=10_000,
                help=(
                    "256k is the documented minimum depth bar. Passing this floor alone "
                    "does not prove that a future prompt genuinely requires 256k."
                ),
            )

        with e3:
            min_formats = st.number_input(
                "Minimum distinct file formats",
                min_value=1,
                value=3,
                step=1,
                help="Environment Creation requires at least 3 distinct file formats.",
            )

    advisory_density_target = 1_000_000

    st.caption(
        "Environment-design utilization rule: future prompts should be capable of requiring "
        "**20 files or at least 50% of the environment**. That is separate from the "
        "**10-file minimum used when actually creating a Professional Tasks Prompt + Rubric task**."
    )

    st.info(
        "**Environment density guidance:** 256k is the actual depth floor used by this checker. "
        "**1M+ is shown as an advisory density target only — not a documented project minimum, "
        "not a pass/fail requirement, and not an upper limit.** The target does not automatically "
        "increase with the 20–29 / 30–50 / 51–99 / 100+ file bands."
    )

    env_uploads = st.file_uploader(
        "Upload environment source files or the environment ZIP",
        type=upload_types,
        accept_multiple_files=True,
        key="env_uploads",
        help=(
            "No upper token ceiling is applied in Environment Creation mode. "
            "ZIP archives are unpacked safely and supported files inside them are scanned recursively."
        ),
    )

    if st.button("Scan environment", type="primary", key="scan_environment"):
        if not env_uploads:
            st.warning("Upload at least one file or ZIP first.")
        else:
            with st.spinner(
                "Extracting text, inspecting images, and estimating environment depth…"
            ):
                env_estimates, env_errors = scan_uploads(
                    env_uploads,
                    DEFAULT_MAX_CONTEXT_TOKENS,
                )

            st.session_state["env_estimates"] = [
                asdict(item)
                for item in env_estimates
            ]
            st.session_state["env_scan_errors"] = env_errors

    if "env_estimates" in st.session_state:
        env_estimates = [
            FileEstimate(**row)
            for row in st.session_state["env_estimates"]
        ]
        env_errors = st.session_state.get("env_scan_errors", [])

        for error in env_errors:
            st.warning(error)

        if env_estimates:
            total_files = len(env_estimates)
            total_text = sum(item.text_tokens for item in env_estimates)
            total_images = sum(item.image_tokens for item in env_estimates)
            total_source = total_text + total_images

            distinct_formats = sorted(
                {
                    item.extension.lower()
                    for item in env_estimates
                    if item.extension
                }
            )
            format_count = len(distinct_formats)

            band_rules = {
                "20–29 files": (20, 29),
                "30–50 files": (30, 50),
                "51–99 files": (51, 99),
                "100+ files": (100, None),
            }
            floor, ceiling = band_rules[size_band]
            file_band_met = (
                total_files >= floor
                and (ceiling is None or total_files <= ceiling)
            )
            depth_met = total_source >= int(env_depth_tokens)
            formats_met = format_count >= int(min_formats)

            st.subheader("Environment Creation result")
            m1, m2, m3, m4, m5 = st.columns(5)

            with m1:
                st.metric(
                    "Environment files",
                    f"{total_files}",
                )
                if file_band_met:
                    st.markdown(
                        f"""
                        <div style="
                            display: inline-block;
                            padding: 0.22rem 0.55rem;
                            border-radius: 999px;
                            background: rgba(33, 195, 84, 0.18);
                            color: #49d17d;
                            font-size: 0.82rem;
                            line-height: 1.2;
                            margin-top: -0.15rem;
                        ">
                            ✓ Within {size_band}
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"""
                        <div style="
                            display: inline-block;
                            padding: 0.22rem 0.55rem;
                            border-radius: 999px;
                            background: rgba(255, 196, 0, 0.18);
                            color: #f0d264;
                            font-size: 0.82rem;
                            line-height: 1.2;
                            margin-top: -0.15rem;
                        ">
                            ⚠ Outside {size_band}
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

            with m2:
                st.metric(
                    "Estimated source context",
                    f"{total_source:,}",
                )
                if depth_met:
                    st.markdown(
                        f"""
                        <div style="
                            display: inline-block;
                            padding: 0.22rem 0.55rem;
                            border-radius: 999px;
                            background: rgba(33, 195, 84, 0.18);
                            color: #49d17d;
                            font-size: 0.82rem;
                            line-height: 1.2;
                            margin-top: -0.15rem;
                        ">
                            ✓ Above {int(env_depth_tokens):,} required floor
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"""
                        <div style="
                            display: inline-block;
                            padding: 0.22rem 0.55rem;
                            border-radius: 999px;
                            background: rgba(255, 196, 0, 0.18);
                            color: #f0d264;
                            font-size: 0.82rem;
                            line-height: 1.2;
                            margin-top: -0.15rem;
                        ">
                            ⚠ {int(env_depth_tokens) - total_source:,} below required floor
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

            with m3:
                st.metric(
                    "1M density target",
                    f"{total_source / advisory_density_target:.0%}",
                )
                if total_source >= advisory_density_target:
                    st.markdown(
                        """
                        <div style="
                            display: inline-block;
                            padding: 0.22rem 0.55rem;
                            border-radius: 999px;
                            background: rgba(33, 195, 84, 0.18);
                            color: #49d17d;
                            font-size: 0.82rem;
                            line-height: 1.2;
                            margin-top: -0.15rem;
                        ">
                            ✓ Above advisory target
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"""
                        <div style="
                            display: inline-block;
                            padding: 0.22rem 0.55rem;
                            border-radius: 999px;
                            background: rgba(60, 150, 255, 0.16);
                            color: #69b7ff;
                            font-size: 0.82rem;
                            line-height: 1.2;
                            margin-top: -0.15rem;
                        ">
                            ℹ {advisory_density_target - total_source:,} below advisory target
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

            m4.metric(
                "Distinct formats",
                f"{format_count}",
                (
                    "Requirement met"
                    if formats_met
                    else f"{int(min_formats) - format_count} more needed"
                ),
            )
            m5.metric(
                "Image-context estimate",
                f"~{total_images:,}",
                "Lower-confidence proxy",
            )

            if file_band_met and depth_met and formats_met:
                st.success(
                    "BASIC ENVIRONMENT CHECKS MET — the selected file-count band, "
                    "256k required depth floor, and format-diversity check are satisfied."
                )
            else:
                missing = []
                if not file_band_met:
                    missing.append("assigned file-count band")
                if not depth_met:
                    missing.append("256k required depth floor")
                if not formats_met:
                    missing.append("3-format minimum")
                st.warning(
                    "ENVIRONMENT CHECK NEEDS ATTENTION — "
                    + ", ".join(missing)
                    + "."
                )

            if total_source >= advisory_density_target:
                st.info(
                    f"Environment context is ~{total_source:,} tokens, which is above the "
                    "**1M advisory density target**. This is not an upper limit — larger dense "
                    "environments are allowed."
                )
            else:
                st.info(
                    f"Environment context is ~{total_source:,} tokens. It clears the documented "
                    f"**{int(env_depth_tokens):,} required floor** but is below the **1M advisory "
                    "density target**. The 1M target is guidance only, not a pass/fail requirement."
                )

            st.caption(
                "These are context/file-count checks only — they do not replace the full "
                "Environment Creation QA for length mix, organization, licensing/sourcing, "
                "realism, conflicts, prompt capacity, or other project requirements."
            )

            env_rows = []
            for item in sorted(
                env_estimates,
                key=lambda x: x.estimated_tokens,
                reverse=True,
            ):
                env_rows.append(
                    {
                        "File": item.file,
                        "Type": item.extension,
                        "Size MB": item.size_mb,
                        "Text tokens": item.text_tokens,
                        "Image tokens (est.)": item.image_tokens,
                        "Combined est.": item.estimated_tokens,
                        "Images": item.image_count,
                        "Words": item.words,
                        "Notes": item.extraction_notes,
                    }
                )

            env_df = pd.DataFrame(env_rows)
            st.subheader("Per-file environment estimates")
            st.dataframe(
                env_df,
                width="stretch",
                hide_index=True,
            )

            st.write(
                "**Formats detected:** "
                + ", ".join(distinct_formats)
            )

            env_json = {
                "workflow": "duck_environment_creation",
                "selected_size_band": size_band,
                "checks": {
                    "file_count": total_files,
                    "file_band_met": file_band_met,
                    "source_context_tokens": total_source,
                    "required_depth_floor_tokens": int(env_depth_tokens),
                    "required_depth_floor_met": depth_met,
                    "advisory_density_target_tokens": advisory_density_target,
                    "advisory_density_target_met": total_source >= advisory_density_target,
                    "distinct_formats": distinct_formats,
                    "distinct_format_count": format_count,
                    "format_requirement_met": formats_met,
                    "upper_context_limit_applied": False,
                },
                "files": env_rows,
            }

            d1, d2 = st.columns(2)
            with d1:
                st.download_button(
                    "Download environment CSV report",
                    data=env_df.to_csv(index=False).encode("utf-8"),
                    file_name="duck_environment_context_report.csv",
                    mime="text/csv",
                )
            with d2:
                st.download_button(
                    "Download environment JSON report",
                    data=json.dumps(
                        env_json,
                        indent=2,
                        ensure_ascii=False,
                    ).encode("utf-8"),
                    file_name="duck_environment_context_report.json",
                    mime="application/json",
                )


st.divider()
st.markdown(
    """
### Two workflows, two purposes

**Part 1 · Duck Environments — Environment Creation**  
Checks the **source environment** before downstream tasks are written. Environment size follows
the assigned file-count band, needs **3+ formats**, and uses **256k as the baseline** required depth floor, particularly for the smallest environment size band.
It also shows **1M as an advisory density target**, while intentionally applying **no upper token ceiling**.

**Part 2 · Professional Tasks — Prompt + Rubric**  
Checks the context a model may need to process for **one task**. Use the required-file subset,
the **10-file + 256k minimum**, and the editable **1M tasking ceiling/warning**.

**Token-estimation notes**

- Text tokens use `tiktoken` (`cl100k_base`) when available and otherwise a conservative heuristic.
- Image tokens are a lower-confidence raster-dimension proxy.
- No OCR token padding is added, avoiding obvious double counting.
"""
)

st.caption(
    "Professional Tasks estimator forked from the original Spidey Context Budget Estimator."
)

st.markdown(
    """
    <div style="
        text-align: center;
        color: #888;
        font-size: 0.85rem;
        margin-top: 2rem;
        padding-bottom: 1rem;
    ">
        Kenneth Pedersen · 2026
    </div>
    """,
    unsafe_allow_html=True,
)
