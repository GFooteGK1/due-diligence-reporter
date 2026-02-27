"""Utility functions for text extraction and URL parsing."""

from __future__ import annotations

import logging
import re
from io import BytesIO
from typing import Any

logger = logging.getLogger("[utils]")


def extract_folder_id_from_url(url: str) -> str | None:
    """
    Extract the Google Drive folder ID from a Drive folder URL.

    Supports formats:
    - https://drive.google.com/drive/folders/FOLDER_ID
    - https://drive.google.com/drive/u/0/folders/FOLDER_ID

    Returns the folder ID string, or None if not parseable.
    """
    # Match /folders/<ID> anywhere in the URL
    match = re.search(r"/folders/([a-zA-Z0-9_-]+)", url)
    if match:
        folder_id = match.group(1)
        logger.debug("Extracted folder ID from URL: %s", folder_id)
        return folder_id

    logger.warning("Could not extract folder ID from URL: %s", url)
    return None


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """
    Extract plain text from PDF bytes using pypdf.

    Returns extracted text (may be empty for image-only PDFs).
    """
    try:
        from pypdf import PdfReader  # type: ignore[import-untyped]
    except ImportError:
        logger.error("pypdf not installed; cannot extract PDF text")
        return ""

    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        pages_text: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                pages_text.append(text)

        result = "\n\n".join(pages_text)
        logger.info(
            "Extracted %d characters from %d PDF pages", len(result), len(reader.pages)
        )
        return result
    except Exception as e:
        logger.error("Failed to extract text from PDF: %s", e)
        return ""


def flatten_report_data_for_replacement(
    report_data: dict[str, Any], prefix: str = ""
) -> dict[str, str]:
    """
    Flatten a nested report_data dict into a mapping of {{PLACEHOLDER}} -> value.

    Nested keys are joined with dots: report_data["q1"]["rating"] -> {{q1.rating}}
    All values are converted to strings. None values become empty strings.
    Lists are joined with ", ".
    """
    result: dict[str, str] = {}

    for key, value in report_data.items():
        full_key = f"{prefix}.{key}" if prefix else key

        if isinstance(value, dict):
            nested = flatten_report_data_for_replacement(value, prefix=full_key)
            result.update(nested)
        elif isinstance(value, list):
            result[full_key] = "\n".join(str(item) for item in value)
        elif value is None:
            result[full_key] = ""
        else:
            result[full_key] = str(value)

    return result


def build_replace_all_text_requests(
    replacements: dict[str, str],
) -> list[dict[str, Any]]:
    """
    Build a list of Google Docs API replaceAllText requests from a replacements mapping.

    Keys should NOT include the {{ }} delimiters — this function adds them.

    Args:
        replacements: dict of placeholder_key -> replacement_value

    Returns:
        List of replaceAllText request dicts for batchUpdate
    """
    requests_list: list[dict[str, Any]] = []

    for placeholder, value in replacements.items():
        requests_list.append(
            {
                "replaceAllText": {
                    "containsText": {
                        "text": f"{{{{{placeholder}}}}}",
                        "matchCase": True,
                    },
                    "replaceText": value,
                }
            }
        )

    return requests_list
