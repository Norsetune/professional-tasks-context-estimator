#!/usr/bin/env python3
"""
Professional Tasks Context Range Estimator.

Estimates source-file text tokens and lower-confidence visual/image tokens,
checks the Professional Tasks minimum reading requirement, and separately
checks an editable maximum context ceiling.

Project defaults:
- Minimum required files: 10
- Minimum required source context: 256,000 tokens
- Maximum selected context: 1,000,000 tokens (provisional/editable)

The 256k minimum is evaluated on required source files only. Prompt tokens do
not count toward that minimum. The maximum check uses the full uploaded source
set plus prompt tokens as a conservative/worst-case estimate.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, List, Optional, Sequence

from bs4 import BeautifulSoup
from pptx import Presentation


TEXT_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".csv",
    ".txt",
    ".md",
    ".html",
    ".htm",
    ".xml",
    ".json",
    ".log",
}

IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}

SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | IMAGE_EXTENSIONS
UPLOAD_EXTENSIONS = SUPPORTED_EXTENSIONS | {".zip"}

DEFAULT_MIN_FILES = 10
DEFAULT_MIN_SOURCE_TOKENS = 256_000
DEFAULT_MAX_CONTEXT_TOKENS = 1_000_000

# Generic tiled-vision proxy. These constants intentionally live in one place
# so they can be changed if the project confirms a model-specific image rule.
VISION_BASE_TOKENS = 85
VISION_TILE_TOKENS = 170
VISION_TILE_SIZE = 512
VISION_MAX_DIMENSION = 2048
VISION_TARGET_SHORT_SIDE = 768
MIN_RASTER_DIMENSION = 64

MAX_ZIP_MEMBERS = 2_000
MAX_ZIP_UNCOMPRESSED_BYTES = 3 * 1024 * 1024 * 1024


@dataclass
class FileEstimate:
    file: str
    extension: str
    size_mb: float
    characters: int
    words: int
    text_tokens: int
    image_tokens: int
    estimated_tokens: int
    image_count: int
    image_estimate_confidence: str
    maximum_risk: str
    extraction_notes: str


def clean_text(text: str) -> str:
    """Normalize extracted text before estimating tokens."""
    if not text:
        return ""
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def estimate_text_tokens(text: str) -> int:
    """Estimate text tokens using tiktoken when available, else a heuristic."""
    text = text or ""
    if not text:
        return 0

    try:
        import tiktoken  # type: ignore

        encoder = tiktoken.get_encoding("cl100k_base")
        return len(encoder.encode(text))
    except Exception:
        characters = len(text)
        words = len(re.findall(r"\S+", text))
        return int(max(characters / 4.0, words * 1.35))


# Backward-compatible alias for code that imported estimate_tokens.
estimate_tokens = estimate_text_tokens


def _normalized_visual_dimensions(width: int, height: int) -> tuple[int, int]:
    """Normalize raster dimensions for the generic tiled-vision proxy."""
    width = max(int(width), 1)
    height = max(int(height), 1)

    max_side = max(width, height)
    if max_side > VISION_MAX_DIMENSION:
        scale = VISION_MAX_DIMENSION / max_side
        width = max(1, int(round(width * scale)))
        height = max(1, int(round(height * scale)))

    short_side = min(width, height)
    if short_side > VISION_TARGET_SHORT_SIDE:
        scale = VISION_TARGET_SHORT_SIDE / short_side
        width = max(1, int(round(width * scale)))
        height = max(1, int(round(height * scale)))

    return width, height


def estimate_image_tokens_from_dimensions(width: int, height: int) -> int:
    """
    Estimate visual tokens from raster dimensions.

    This is deliberately a lower-confidence proxy, not a claim about the
    target model's exact vision tokenizer. It approximates tiled high-detail
    processing after conservative image normalization.
    """
    if width < MIN_RASTER_DIMENSION or height < MIN_RASTER_DIMENSION:
        return 0

    width, height = _normalized_visual_dimensions(width, height)
    tiles = math.ceil(width / VISION_TILE_SIZE) * math.ceil(height / VISION_TILE_SIZE)
    return VISION_BASE_TOKENS + tiles * VISION_TILE_TOKENS


def _image_dimensions_from_bytes(data: bytes) -> Optional[tuple[int, int]]:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            return int(image.width), int(image.height)
    except Exception:
        return None


def _office_media_estimate(path: Path) -> tuple[int, int, str]:
    """Estimate raster images stored inside DOCX/PPTX/XLSX packages."""
    tokens = 0
    count = 0
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                member = info.filename.lower()
                if "/media/" not in member:
                    continue
                if Path(member).suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                try:
                    dimensions = _image_dimensions_from_bytes(archive.read(info))
                except Exception:
                    dimensions = None
                if not dimensions:
                    continue
                image_tokens = estimate_image_tokens_from_dimensions(*dimensions)
                if image_tokens:
                    tokens += image_tokens
                    count += 1
    except Exception as exc:
        return 0, 0, f"Embedded-image scan failed: {exc}"

    if count:
        return tokens, count, f"Embedded raster images estimated: {count}"
    return 0, 0, "No qualifying embedded raster images detected"


def _pdf_media_estimate(path: Path) -> tuple[int, int, str]:
    """Estimate raster images embedded in a PDF, deduplicating image xrefs."""
    try:
        import fitz
    except Exception as exc:
        return 0, 0, f"PDF image scan skipped: PyMuPDF unavailable ({exc})"

    tokens = 0
    count = 0
    seen_xrefs: set[int] = set()

    try:
        with fitz.open(path) as document:
            for page in document:
                for image in page.get_images(full=True):
                    xref = int(image[0])
                    if xref in seen_xrefs:
                        continue
                    seen_xrefs.add(xref)

                    try:
                        info = document.extract_image(xref)
                        width = int(info.get("width") or image[2] or 0)
                        height = int(info.get("height") or image[3] or 0)
                    except Exception:
                        width = int(image[2] or 0)
                        height = int(image[3] or 0)

                    image_tokens = estimate_image_tokens_from_dimensions(width, height)
                    if image_tokens:
                        tokens += image_tokens
                        count += 1
    except Exception as exc:
        return 0, 0, f"PDF image scan failed: {exc}"

    if count:
        return tokens, count, f"Embedded PDF raster images estimated: {count}"
    return 0, 0, "No qualifying embedded PDF raster images detected"


def estimate_visual_tokens(path: Path) -> tuple[int, int, str, str]:
    """Return image tokens, image count, note, and confidence label."""
    extension = path.suffix.lower()

    if extension in IMAGE_EXTENSIONS:
        try:
            from PIL import Image

            with Image.open(path) as image:
                tokens = estimate_image_tokens_from_dimensions(image.width, image.height)
                count = 1 if tokens else 0
                return tokens, count, f"Standalone raster: {image.width}×{image.height}", "LOWER"
        except Exception as exc:
            return 0, 0, f"Standalone image estimate failed: {exc}", "LOWER"

    if extension == ".pdf":
        tokens, count, note = _pdf_media_estimate(path)
        return tokens, count, note, "LOWER"

    if extension in {".docx", ".pptx", ".xlsx"}:
        tokens, count, note = _office_media_estimate(path)
        return tokens, count, note, "LOWER"

    return 0, 0, "Visual estimate not applicable for this format", "N/A"


def extract_pdf(path: Path, max_pages: Optional[int] = None) -> tuple[str, str]:
    """Extract text from a PDF."""
    try:
        import fitz
    except Exception as exc:
        return "", f"PDF skipped: PyMuPDF not installed ({exc})"

    notes: list[str] = []
    chunks: list[str] = []

    try:
        with fitz.open(path) as document:
            page_count = len(document)
            pages_to_read = page_count if max_pages is None else min(page_count, max_pages)

            for page_index in range(pages_to_read):
                chunks.append(document.load_page(page_index).get_text("text"))

            notes.append(f"PDF pages read: {pages_to_read}/{page_count}")
            if max_pages is not None and page_count > max_pages:
                notes.append("PDF truncated by max-pages option")
    except Exception as exc:
        return "", f"PDF extraction failed: {exc}"

    return clean_text("\n".join(chunks)), "; ".join(notes)


def extract_docx(path: Path) -> tuple[str, str]:
    """Extract paragraphs and tables from a DOCX file."""
    try:
        import docx
    except Exception as exc:
        return "", f"DOCX skipped: python-docx not installed ({exc})"

    chunks: list[str] = []
    try:
        document = docx.Document(str(path))
        for paragraph in document.paragraphs:
            if paragraph.text:
                chunks.append(paragraph.text)
        for table in document.tables:
            for row in table.rows:
                chunks.append("\t".join(cell.text for cell in row.cells))

        notes = f"DOCX paragraphs: {len(document.paragraphs)}, tables: {len(document.tables)}"
        return clean_text("\n".join(chunks)), notes
    except Exception as exc:
        return "", f"DOCX extraction failed: {exc}"


def extract_pptx_text(path: Path) -> tuple[str, str]:
    """Extract slide text and speaker notes from a PPTX file."""
    try:
        presentation = Presentation(str(path))
        parts: list[str] = []
        for slide_index, slide in enumerate(presentation.slides, start=1):
            parts.append(f"\n--- Slide {slide_index} ---\n")
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text:
                    parts.append(shape.text)
            if slide.has_notes_slide:
                try:
                    notes_text_frame = slide.notes_slide.notes_text_frame
                    if notes_text_frame and notes_text_frame.text:
                        parts.append(f"\n[Speaker notes]\n{notes_text_frame.text}")
                except Exception:
                    pass
        return clean_text("\n".join(parts)), f"PPTX slides read: {len(presentation.slides)}"
    except Exception as exc:
        return "", f"PPTX extraction failed: {exc}"


def extract_xlsx(path: Path, max_cells: Optional[int] = None) -> tuple[str, str]:
    """Extract displayed cell values and formulas from an XLSX file."""
    try:
        from openpyxl import load_workbook
    except Exception as exc:
        return "", f"XLSX skipped: openpyxl not installed ({exc})"

    chunks: list[str] = []
    cell_count = 0
    try:
        workbook = load_workbook(filename=path, read_only=True, data_only=False)
        sheet_count = len(workbook.sheetnames)

        for worksheet in workbook.worksheets:
            chunks.append(f"\n[SHEET: {worksheet.title}]\n")
            for row in worksheet.iter_rows():
                values: list[str] = []
                for cell in row:
                    if cell.value is not None:
                        values.append(str(cell.value))
                        cell_count += 1
                        if max_cells is not None and cell_count >= max_cells:
                            if values:
                                chunks.append("\t".join(values))
                            workbook.close()
                            notes = (
                                f"XLSX sheets: {sheet_count}; cells read: {cell_count}; "
                                "truncated by max-cells option"
                            )
                            return clean_text("\n".join(chunks)), notes
                if values:
                    chunks.append("\t".join(values))

        workbook.close()
        return clean_text("\n".join(chunks)), (
            f"XLSX sheets: {sheet_count}; non-empty cells read: {cell_count}"
        )
    except Exception as exc:
        return "", f"XLSX extraction failed: {exc}"


def extract_csv(path: Path, max_rows: Optional[int] = None) -> tuple[str, str]:
    """Extract rows from a CSV or delimited-text file."""
    chunks: list[str] = []
    row_count = 0
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as file:
            sample = file.read(4096)
            file.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample)
            except Exception:
                dialect = csv.excel

            reader = csv.reader(file, dialect)
            for row in reader:
                row_count += 1
                chunks.append("\t".join(row))
                if max_rows is not None and row_count >= max_rows:
                    return clean_text("\n".join(chunks)), (
                        f"CSV rows read: {row_count}; truncated by max-rows option"
                    )

        return clean_text("\n".join(chunks)), f"CSV rows read: {row_count}"
    except Exception as exc:
        return "", f"CSV extraction failed: {exc}"


def extract_html_text(path: Path) -> tuple[str, str]:
    """Extract visible text from an HTML file."""
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return clean_text(soup.get_text(separator="\n", strip=True)), (
            "HTML visible text extracted; scripts/styles removed"
        )
    except Exception as exc:
        return "", f"HTML extraction failed: {exc}"


def extract_xml_text(path: Path) -> tuple[str, str]:
    """Extract tags and text values from XML."""
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        return "", f"XML read failed: {exc}"

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return clean_text(raw), "XML parse failed; raw text used"

    parts: list[str] = []

    def walk(node, depth: int = 0) -> None:
        if node.tag:
            parts.append(f"{'  ' * depth}<{node.tag}>")
        if node.text and node.text.strip():
            parts.append(node.text.strip())
        for child in node:
            walk(child, depth + 1)
        if node.tail and node.tail.strip():
            parts.append(node.tail.strip())

    walk(root)
    return clean_text("\n".join(parts)), "XML text extracted from element tree"


def extract_json_text(path: Path) -> tuple[str, str]:
    """Read JSON as normalized textual content without changing values."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
            text = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
            return clean_text(text), "JSON parsed and normalized"
        except Exception:
            return clean_text(raw), "JSON parse failed; raw text used"
    except Exception as exc:
        return "", f"JSON extraction failed: {exc}"


def extract_text(path: Path) -> tuple[str, str]:
    """Route a file to the appropriate text extraction function."""
    extension = path.suffix.lower()

    if extension == ".pdf":
        return extract_pdf(path)
    if extension == ".docx":
        return extract_docx(path)
    if extension == ".pptx":
        return extract_pptx_text(path)
    if extension == ".xlsx":
        return extract_xlsx(path)
    if extension == ".csv":
        return extract_csv(path)
    if extension in {".txt", ".md", ".log"}:
        try:
            return clean_text(path.read_text(encoding="utf-8", errors="replace")), (
                "Plain text/Markdown/log read"
            )
        except Exception as exc:
            return "", f"Text extraction failed: {exc}"
    if extension in {".html", ".htm"}:
        return extract_html_text(path)
    if extension == ".xml":
        return extract_xml_text(path)
    if extension == ".json":
        return extract_json_text(path)
    if extension in IMAGE_EXTENSIONS:
        return "", "Standalone image: no OCR text added"

    return "", f"Unsupported extension: {extension}"


def maximum_risk_band(tokens: int, maximum_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS) -> str:
    """Assign a maximum-context risk label."""
    if maximum_tokens <= 0:
        raise ValueError("The maximum context limit must be greater than zero.")

    ratio = tokens / maximum_tokens
    if ratio >= 1.0:
        return "OVER_MAXIMUM"
    if ratio >= 0.95:
        return "CRITICAL"
    if ratio >= 0.85:
        return "NEAR_MAXIMUM"
    if ratio >= 0.70:
        return "HIGH_CONTEXT_LOAD"
    return "COMFORTABLY_WITHIN_MAXIMUM"


def estimate_file(
    path: Path,
    maximum_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    display_name: Optional[str] = None,
) -> FileEstimate:
    """Extract and estimate text + visual token usage for one file."""
    text, text_notes = extract_text(path)
    visual_tokens, image_count, visual_note, confidence = estimate_visual_tokens(path)

    characters = len(text)
    words = len(re.findall(r"\S+", text))
    text_tokens = estimate_text_tokens(text)
    total_tokens = text_tokens + visual_tokens

    try:
        size_mb = path.stat().st_size / (1024 * 1024)
    except Exception:
        size_mb = 0.0

    notes = f"{text_notes}; {visual_note}" if visual_note else text_notes

    return FileEstimate(
        file=display_name or str(path),
        extension=path.suffix.lower(),
        size_mb=round(size_mb, 2),
        characters=characters,
        words=words,
        text_tokens=text_tokens,
        image_tokens=visual_tokens,
        estimated_tokens=total_tokens,
        image_count=image_count,
        image_estimate_confidence=confidence,
        maximum_risk=maximum_risk_band(total_tokens, maximum_tokens),
        extraction_notes=notes,
    )


def estimate_prompt(prompt: str) -> FileEstimate:
    """Estimate prompt text separately; prompt tokens never satisfy the 256k source minimum."""
    text = prompt or ""
    characters = len(text)
    words = len(re.findall(r"\S+", text))
    tokens = estimate_text_tokens(text)

    return FileEstimate(
        file="[PROMPT_TEXT]",
        extension="prompt",
        size_mb=0.0,
        characters=characters,
        words=words,
        text_tokens=tokens,
        image_tokens=0,
        estimated_tokens=tokens,
        image_count=0,
        image_estimate_confidence="N/A",
        maximum_risk="N/A",
        extraction_notes="Prompt text supplied manually; excluded from source minimum",
    )


def summarize_project(
    required_estimates: Sequence[FileEstimate],
    all_estimates: Optional[Sequence[FileEstimate]] = None,
    prompt_estimate: Optional[FileEstimate] = None,
    min_files: int = DEFAULT_MIN_FILES,
    min_source_tokens: int = DEFAULT_MIN_SOURCE_TOKENS,
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
) -> dict:
    """Summarize minimum compliance and maximum-context safety separately."""
    if min_files <= 0:
        raise ValueError("Minimum file count must be greater than zero.")
    if min_source_tokens <= 0:
        raise ValueError("Minimum source tokens must be greater than zero.")
    if max_context_tokens <= 0:
        raise ValueError("Maximum context tokens must be greater than zero.")
    if max_context_tokens <= min_source_tokens:
        raise ValueError("Maximum context must be greater than the minimum source-token threshold.")

    required_estimates = list(required_estimates)
    all_estimates = list(all_estimates if all_estimates is not None else required_estimates)

    required_file_count = len(required_estimates)
    uploaded_file_count = len(all_estimates)
    required_text_tokens = sum(item.text_tokens for item in required_estimates)
    required_image_tokens = sum(item.image_tokens for item in required_estimates)
    required_source_tokens = required_text_tokens + required_image_tokens

    all_text_tokens = sum(item.text_tokens for item in all_estimates)
    all_image_tokens = sum(item.image_tokens for item in all_estimates)
    all_source_tokens = all_text_tokens + all_image_tokens
    prompt_tokens = prompt_estimate.estimated_tokens if prompt_estimate else 0

    required_turn_tokens = required_source_tokens + prompt_tokens
    full_uploaded_turn_tokens = all_source_tokens + prompt_tokens

    files_met = required_file_count >= min_files
    source_met = required_source_tokens >= min_source_tokens
    minimum_met = files_met and source_met

    minimum_ratio = required_source_tokens / min_source_tokens
    if not files_met and not source_met:
        minimum_status = "BELOW_MINIMUM_FILES_AND_CONTEXT"
    elif not files_met:
        minimum_status = "BELOW_MINIMUM_FILES"
    elif not source_met:
        minimum_status = "BELOW_MINIMUM_CONTEXT"
    elif minimum_ratio < 1.10:
        minimum_status = "MEETS_MINIMUM_NARROW_MARGIN"
    else:
        minimum_status = "COMFORTABLY_ABOVE_MINIMUM"

    max_status = maximum_risk_band(full_uploaded_turn_tokens, max_context_tokens)
    if not minimum_met:
        overall_status = "BELOW_PROJECT_MINIMUM"
    elif max_status == "OVER_MAXIMUM":
        overall_status = "OVER_SELECTED_MAXIMUM"
    elif max_status in {"CRITICAL", "NEAR_MAXIMUM"}:
        overall_status = "MEETS_MINIMUM_BUT_NEAR_MAXIMUM"
    else:
        overall_status = "MEETS_PROJECT_REQUIREMENTS"

    required_file_shortfall = max(0, min_files - required_file_count)
    source_token_shortfall = max(0, min_source_tokens - required_source_tokens)
    remaining_to_maximum = max(0, max_context_tokens - full_uploaded_turn_tokens)

    utilization_pct = (
        required_file_count / uploaded_file_count * 100 if uploaded_file_count else 0.0
    )
    environment_utilization_advisory_met = (
        required_file_count >= 20 or utilization_pct >= 50.0
    ) if uploaded_file_count else False

    largest_required = sorted(
        required_estimates, key=lambda item: item.estimated_tokens, reverse=True
    )[:10]
    largest_uploaded = sorted(
        all_estimates, key=lambda item: item.estimated_tokens, reverse=True
    )[:10]

    return {
        "min_files": min_files,
        "min_source_tokens": min_source_tokens,
        "max_context_tokens": max_context_tokens,
        "required_file_count": required_file_count,
        "uploaded_file_count": uploaded_file_count,
        "files_met": files_met,
        "required_file_shortfall": required_file_shortfall,
        "required_text_tokens": required_text_tokens,
        "required_image_tokens": required_image_tokens,
        "required_source_tokens": required_source_tokens,
        "source_met": source_met,
        "source_token_shortfall": source_token_shortfall,
        "minimum_status": minimum_status,
        "minimum_met": minimum_met,
        "all_text_tokens": all_text_tokens,
        "all_image_tokens": all_image_tokens,
        "all_source_tokens": all_source_tokens,
        "prompt_tokens": prompt_tokens,
        "required_turn_tokens": required_turn_tokens,
        "full_uploaded_turn_tokens": full_uploaded_turn_tokens,
        "percent_of_maximum": round(full_uploaded_turn_tokens / max_context_tokens * 100, 1),
        "remaining_to_maximum": remaining_to_maximum,
        "maximum_status": max_status,
        "overall_status": overall_status,
        "required_file_utilization_pct": round(utilization_pct, 1),
        "environment_utilization_advisory_met": environment_utilization_advisory_met,
        "largest_required_contributors": [asdict(item) for item in largest_required],
        "largest_uploaded_contributors": [asdict(item) for item in largest_uploaded],
    }


# Backward-compatible summary for simple callers: all files are required.
def summarize(
    estimates: List[FileEstimate],
    context_limit: int = DEFAULT_MAX_CONTEXT_TOKENS,
) -> dict:
    return summarize_project(
        required_estimates=estimates,
        all_estimates=estimates,
        max_context_tokens=context_limit,
    )


def iter_files(paths: Iterable[Path]) -> Iterable[Path]:
    """Yield supported non-ZIP files from individual paths or folders."""
    for path in paths:
        if path.is_dir():
            for candidate in sorted(path.rglob("*")):
                if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_EXTENSIONS:
                    yield candidate
        elif path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield path


def extract_supported_from_zip(zip_path: Path, destination: Path) -> list[tuple[Path, str]]:
    """
    Safely extract supported files from a ZIP.

    Returns (local_path, archive_relative_name) pairs. Unsupported members,
    directories, path traversal entries, and macOS metadata are ignored.
    """
    extracted: list[tuple[Path, str]] = []
    destination.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_MEMBERS:
            raise ValueError(f"ZIP has {len(infos)} members; limit is {MAX_ZIP_MEMBERS}.")

        total_uncompressed = sum(max(0, int(info.file_size)) for info in infos)
        if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
            raise ValueError("ZIP uncompressed size exceeds the safety limit.")

        for info in infos:
            if info.is_dir():
                continue

            pure = PurePosixPath(info.filename)
            if pure.is_absolute() or ".." in pure.parts:
                continue
            if "__MACOSX" in pure.parts or pure.name.startswith("._"):
                continue

            extension = Path(pure.name).suffix.lower()
            if extension not in SUPPORTED_EXTENSIONS:
                continue

            target = destination.joinpath(*pure.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                output.write(source.read())

            extracted.append((target, str(pure)))

    return extracted


def print_table(
    estimates: Sequence[FileEstimate],
    summary: dict,
) -> None:
    """Print a CLI report."""
    print("\nProfessional Tasks context range estimate")
    print("=" * 112)
    print(
        f"{'Total':>11}  {'Text':>11}  {'Image':>10}  {'Words':>10}  "
        f"{'MB':>8}  {'Max risk':<29}  File"
    )
    print("-" * 112)

    for estimate in sorted(estimates, key=lambda item: item.estimated_tokens, reverse=True):
        print(
            f"{estimate.estimated_tokens:>11,}  "
            f"{estimate.text_tokens:>11,}  "
            f"{estimate.image_tokens:>10,}  "
            f"{estimate.words:>10,}  "
            f"{estimate.size_mb:>8.2f}  "
            f"{estimate.maximum_risk:<29}  "
            f"{estimate.file}"
        )

    print("-" * 112)
    print(
        f"REQUIRED SOURCE: {summary['required_source_tokens']:,} / "
        f"{summary['min_source_tokens']:,} tokens | "
        f"FILES: {summary['required_file_count']} / {summary['min_files']}"
    )
    print(
        f"FULL UPLOADED TURN: {summary['full_uploaded_turn_tokens']:,} / "
        f"{summary['max_context_tokens']:,} tokens "
        f"({summary['percent_of_maximum']}%)"
    )
    print(f"OVERALL STATUS: {summary['overall_status']}")
    print(
        "\nImage-token estimates are a lower-confidence raster-dimension proxy. "
        "They are reported separately from text tokens and are not OCR-derived."
    )


def _collect_cli_files(paths: Sequence[Path], tmp_root: Path) -> list[tuple[Path, str]]:
    collected: list[tuple[Path, str]] = []
    for path in paths:
        if path.is_file() and path.suffix.lower() == ".zip":
            zip_dest = tmp_root / path.stem
            for local, relative in extract_supported_from_zip(path, zip_dest):
                collected.append((local, f"{path.name}::{relative}"))
        elif path.is_dir():
            for candidate in iter_files([path]):
                try:
                    display = str(candidate.relative_to(path))
                except Exception:
                    display = candidate.name
                collected.append((candidate, display))
        elif path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            collected.append((path, path.name))
    return collected


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Estimate Professional Tasks source-context depth and maximum context risk. "
            "All CLI input files are treated as required files."
        )
    )
    parser.add_argument("paths", nargs="+", help="Files, folders, and/or ZIP archives to scan.")
    parser.add_argument("--prompt-file", help="Optional prompt text file.")
    parser.add_argument("--min-files", type=int, default=DEFAULT_MIN_FILES)
    parser.add_argument("--min-tokens", type=int, default=DEFAULT_MIN_SOURCE_TOKENS)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_CONTEXT_TOKENS)
    parser.add_argument("--json-out", help="Optional JSON report path.")
    args = parser.parse_args()

    if args.min_files <= 0:
        parser.error("--min-files must be greater than zero")
    if args.min_tokens <= 0:
        parser.error("--min-tokens must be greater than zero")
    if args.max_tokens <= args.min_tokens:
        parser.error("--max-tokens must be greater than --min-tokens")

    prompt_estimate: Optional[FileEstimate] = None
    if args.prompt_file:
        prompt_path = Path(args.prompt_file)
        if not prompt_path.is_file():
            parser.error(f"Prompt file not found: {prompt_path}")
        prompt_estimate = estimate_prompt(
            prompt_path.read_text(encoding="utf-8", errors="replace")
        )

    with tempfile.TemporaryDirectory(prefix="professional_tasks_context_") as tmpdir:
        collected = _collect_cli_files([Path(value) for value in args.paths], Path(tmpdir))
        estimates = [
            estimate_file(local, args.max_tokens, display_name=display)
            for local, display in collected
        ]

    if not estimates:
        print("No supported files were found.")
        return

    summary = summarize_project(
        required_estimates=estimates,
        all_estimates=estimates,
        prompt_estimate=prompt_estimate,
        min_files=args.min_files,
        min_source_tokens=args.min_tokens,
        max_context_tokens=args.max_tokens,
    )
    print_table(estimates, summary)

    if args.json_out:
        report = {
            "summary": summary,
            "files": [asdict(item) for item in estimates],
            "prompt": asdict(prompt_estimate) if prompt_estimate else None,
        }
        output_path = Path(args.json_out)
        output_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nJSON report written to: {output_path}")


if __name__ == "__main__":
    main()
