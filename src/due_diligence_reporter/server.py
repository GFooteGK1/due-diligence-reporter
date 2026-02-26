"""MCP server for Alpha School Due Diligence Report generation."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from mcp.server import FastMCP

from .config import get_settings
from .google_client import GoogleClient
from .utils import (
    build_replace_all_text_requests,
    extract_folder_id_from_url,
    extract_text_from_pdf_bytes,
    flatten_report_data_for_replacement,
)
from .wrike import build_site_summary, find_site_record

# Load environment variables from the project-root .env if present
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),  # stderr for MCP protocol compatibility
    ],
)
logger = logging.getLogger(__name__)
logger.info("=" * 80)
logger.info("Due Diligence Reporter MCP server starting")

mcp = FastMCP("dd-reporter")

# MIME type for Google Docs
GOOGLE_DOCS_MIME = "application/vnd.google-apps.document"
# MIME type for Google Sheets
GOOGLE_SHEETS_MIME = "application/vnd.google-apps.spreadsheet"
# MIME type for PDF
PDF_MIME = "application/pdf"
# Name of the due diligence subfolder inside every site Drive folder
DUE_DILIGENCE_SUBFOLDER = "01_Due Diligence"

# Google Workspace MIME types that can be exported as plain text
EXPORTABLE_MIME_TYPES: set[str] = {
    GOOGLE_DOCS_MIME,
    "application/vnd.google-apps.presentation",
    GOOGLE_SHEETS_MIME,
}


def _make_google_client() -> GoogleClient:
    """Initialise and return a GoogleClient using settings from config."""
    settings = get_settings()
    return GoogleClient.from_oauth_config(
        client_config_path=str(settings.get_client_config_path()),
        token_file_path=str(settings.get_token_file_path()),
        oauth_port=settings.oauth_port,
        scopes=settings.google_scopes,
    )


@mcp.tool()
async def get_site_record(site_name_or_id: str) -> dict[str, Any]:
    """Fetch a Wrike Site Record by name or ID.

    Searches for the site record matching the given name or Wrike ID. Returns
    address, school type, current stage, Drive folder URL, and all DD-relevant
    custom field metadata stored in Wrike.

    Args:
        site_name_or_id: Site name (e.g., "Alpha Austin Demo"), Wrike record ID,
            or Wrike permalink URL.

    Returns:
        Dict with site metadata, or error dict if not found.
    """
    logger.info("Tool called: get_site_record")
    logger.info("get_site_record params: site_name_or_id=%s", site_name_or_id)

    if not site_name_or_id or not site_name_or_id.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "site_name_or_id must be a non-empty string",
        }

    try:
        record = find_site_record(site_name_or_id=site_name_or_id)

        if not record:
            logger.warning("No Site Record found for: %s", site_name_or_id)
            return {
                "status": "error",
                "error": "Site record not found",
                "message": (
                    f"Could not find a Wrike Site Record matching '{site_name_or_id}'. "
                    "Try using the exact site name, a Wrike ID, or a Wrike permalink."
                ),
            }

        summary = build_site_summary(record)
        logger.info(
            "Found Site Record: %s (id=%s, stage=%s)",
            summary.get("title"),
            summary.get("id"),
            summary.get("stage"),
        )

        return {
            "status": "success",
            "site": summary,
            "message": f"Found Site Record: {summary.get('title')}",
        }

    except Exception as e:
        logger.error("Failed to fetch Site Record: %s", e)
        return {
            "status": "error",
            "error": "Wrike API error",
            "message": str(e),
        }


@mcp.tool()
async def list_drive_documents(drive_folder_url: str) -> dict[str, Any]:
    """List all files in the site's Google Drive folder and its 01_Due Diligence subfolder.

    Returns file name, ID, MIME type, and modified date for each file found.
    Use the returned file IDs and names with read_drive_document to read content.

    Args:
        drive_folder_url: Google Drive folder URL (from the site's Wrike record).

    Returns:
        Dict with lists of files found in the root folder and DD subfolder.
    """
    logger.info("Tool called: list_drive_documents")
    logger.info("list_drive_documents params: drive_folder_url=%s", drive_folder_url)

    if not drive_folder_url or not drive_folder_url.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "drive_folder_url must be a non-empty string",
        }

    folder_id = extract_folder_id_from_url(drive_folder_url)
    if not folder_id:
        return {
            "status": "error",
            "error": "Invalid folder URL",
            "message": (
                f"Could not extract a Google Drive folder ID from: {drive_folder_url}. "
                "Expected a URL like https://drive.google.com/drive/folders/FOLDER_ID"
            ),
        }

    try:
        gc = _make_google_client()

        # List files in root folder
        root_files = gc.list_files_in_folder(folder_id)
        logger.info("Found %d files in root folder %s", len(root_files), folder_id)

        # Find and list files in 01_Due Diligence subfolder
        dd_subfolder = gc.find_subfolder_by_name(folder_id, DUE_DILIGENCE_SUBFOLDER)
        dd_files: list[dict[str, Any]] = []
        dd_subfolder_id: str | None = None

        if dd_subfolder:
            dd_subfolder_id = dd_subfolder.get("id")
            if dd_subfolder_id:
                dd_files = gc.list_files_in_folder(dd_subfolder_id)
                logger.info(
                    "Found %d files in %s subfolder", len(dd_files), DUE_DILIGENCE_SUBFOLDER
                )
        else:
            logger.info("No %s subfolder found", DUE_DILIGENCE_SUBFOLDER)

        total_files = len(root_files) + len(dd_files)

        return {
            "status": "success",
            "folder_id": folder_id,
            "drive_folder_url": drive_folder_url,
            "root_folder_files": root_files,
            "due_diligence_subfolder_id": dd_subfolder_id,
            "due_diligence_files": dd_files,
            "total_file_count": total_files,
            "message": (
                f"Found {len(root_files)} files in root folder and "
                f"{len(dd_files)} files in {DUE_DILIGENCE_SUBFOLDER} subfolder "
                f"({total_files} total)"
            ),
        }

    except Exception as e:
        logger.error("Failed to list Drive documents: %s", e)
        return {
            "status": "error",
            "error": "Google Drive API error",
            "message": str(e),
        }


@mcp.tool()
async def read_drive_document(file_id: str, file_name: str) -> dict[str, Any]:
    """Read and return the full text content of a Google Drive file.

    Supports:
    - Google Docs: exported as plain text via Drive API
    - PDFs: downloaded and text extracted using pypdf
    - Plain text files: downloaded directly

    Args:
        file_id: Google Drive file ID (from list_drive_documents).
        file_name: File name (used to determine how to extract text and for logging).

    Returns:
        Dict with the extracted text content of the document.
    """
    logger.info("Tool called: read_drive_document")
    logger.info(
        "read_drive_document params: file_id=%s, file_name=%s", file_id, file_name
    )

    if not file_id or not file_id.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "file_id must be a non-empty string",
        }

    try:
        gc = _make_google_client()

        # Determine MIME type by fetching file metadata
        logger.info("Fetching metadata for file: %s", file_id)
        try:
            file_metadata: dict[str, Any] = (
                gc.drive_service.files()
                .get(
                    fileId=file_id,
                    fields="id,name,mimeType,size",
                    supportsAllDrives=True,
                )
                .execute()
            )
        except Exception as meta_err:
            logger.warning(
                "Could not fetch metadata for %s, inferring type from name: %s",
                file_id,
                meta_err,
            )
            file_metadata = {"mimeType": _infer_mime_from_name(file_name)}

        mime_type: str = file_metadata.get("mimeType", "")
        logger.info("File %s has MIME type: %s", file_id, mime_type)

        text_content: str = ""

        if mime_type in EXPORTABLE_MIME_TYPES:
            # Google Workspace file — export as plain text
            text_content = gc.export_google_doc_as_text(file_id)

        elif mime_type == PDF_MIME or file_name.lower().endswith(".pdf"):
            # PDF — download bytes then extract text
            pdf_bytes = gc.download_file_bytes(file_id)
            text_content = extract_text_from_pdf_bytes(pdf_bytes)
            if not text_content:
                logger.warning(
                    "PDF text extraction returned empty for %s — may be image-only", file_id
                )
                text_content = (
                    "[PDF text extraction returned no text. "
                    "This may be an image-only PDF that requires OCR.]"
                )

        elif mime_type.startswith("text/") or file_name.lower().endswith(
            (".txt", ".md", ".csv")
        ):
            # Plain text file — download directly
            raw_bytes = gc.download_file_bytes(file_id)
            text_content = raw_bytes.decode("utf-8", errors="replace")

        else:
            logger.warning(
                "Unsupported MIME type %s for file %s — attempting generic download",
                mime_type,
                file_id,
            )
            try:
                raw_bytes = gc.download_file_bytes(file_id)
                text_content = raw_bytes.decode("utf-8", errors="replace")
            except Exception as dl_err:
                logger.error("Could not download file %s: %s", file_id, dl_err)
                text_content = (
                    f"[Could not extract text from file with MIME type: {mime_type}]"
                )

        logger.info(
            "read_drive_document: extracted %d characters from %s", len(text_content), file_name
        )

        return {
            "status": "success",
            "file_id": file_id,
            "file_name": file_name,
            "mime_type": mime_type,
            "character_count": len(text_content),
            "text": text_content,
            "message": f"Successfully read {len(text_content)} characters from '{file_name}'",
        }

    except Exception as e:
        logger.error("Failed to read Drive document %s: %s", file_id, e)
        return {
            "status": "error",
            "error": "Failed to read document",
            "file_id": file_id,
            "file_name": file_name,
            "message": str(e),
        }


def _infer_mime_from_name(file_name: str) -> str:
    """Infer MIME type from file name extension."""
    name_lower = file_name.lower()
    if name_lower.endswith(".pdf"):
        return PDF_MIME
    if name_lower.endswith((".doc", ".docx")):
        return "application/msword"
    if name_lower.endswith(".txt"):
        return "text/plain"
    return "application/octet-stream"


@mcp.tool()
async def create_dd_report(
    site_name: str,
    drive_folder_url: str,
    report_data: dict[str, Any],
) -> dict[str, Any]:
    """Create a completed DD report Google Doc for a site.

    Copies the master DD report template to the site's Drive folder, names it
    "[Site Name] DD Report - [MM/DD/YYYY]", then fills all {{PLACEHOLDER}} tokens
    using Google Docs API replaceAllText in a single batchUpdate call.

    The report_data dict should follow the full DD report schema with nested sections:
    meta, exec_summary, q1, q2, q3, q4, appendix. All nested keys are flattened using
    dot notation for placeholder matching (e.g., q1.rating -> {{q1.rating}}).

    Args:
        site_name: Site name used for the report document title.
        drive_folder_url: Google Drive folder URL for the site (report is saved here).
        report_data: Nested dict with all report sections and field values.

    Returns:
        Dict with the URL of the newly created DD report Google Doc.
    """
    logger.info("Tool called: create_dd_report")
    logger.info(
        "create_dd_report params: site_name=%s, drive_folder_url=%s",
        site_name,
        drive_folder_url,
    )

    if not site_name or not site_name.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "site_name must be a non-empty string",
        }

    if not drive_folder_url or not drive_folder_url.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "drive_folder_url must be a non-empty string",
        }

    folder_id = extract_folder_id_from_url(drive_folder_url)
    if not folder_id:
        return {
            "status": "error",
            "error": "Invalid folder URL",
            "message": (
                f"Could not extract a Google Drive folder ID from: {drive_folder_url}"
            ),
        }

    settings = get_settings()
    template_id = settings.dd_template_google_doc_id

    if not template_id:
        return {
            "status": "error",
            "error": "Missing configuration",
            "message": (
                "DD_TEMPLATE_GOOGLE_DOC_ID is not configured. "
                "Set this environment variable to the Google Doc template ID."
            ),
        }

    # Build the document name
    today_str = datetime.now().strftime("%m/%d/%Y")
    doc_name = f"{site_name.strip()} DD Report - {today_str}"

    logger.info("Creating DD report: %s", doc_name)

    try:
        gc = _make_google_client()

        # Step 1: Copy the template to the site's Drive folder
        logger.info(
            "Copying template %s to folder %s as '%s'", template_id, folder_id, doc_name
        )
        copied_doc = gc.copy_document(
            template_id=template_id,
            name=doc_name,
            parent_folder_id=folder_id,
        )

        doc_id = copied_doc.get("id")
        doc_url = copied_doc.get("webViewLink")

        if not doc_id or not isinstance(doc_id, str):
            raise RuntimeError("Invalid document ID returned from copy operation")

        logger.info("Copied template to new document: %s (id=%s)", doc_name, doc_id)

        # Step 2: Flatten report_data into placeholder -> value mapping
        flat_data = flatten_report_data_for_replacement(report_data)

        # Add top-level convenience placeholders
        flat_data.setdefault("site_name", site_name.strip())
        flat_data.setdefault("report_date", today_str)
        flat_data.setdefault("doc_url", doc_url or "")

        logger.info("Prepared %d placeholder replacements", len(flat_data))

        # Step 3: Build and apply replaceAllText batch update
        replace_requests = build_replace_all_text_requests(flat_data)

        if replace_requests:
            gc.batch_update_document(doc_id, replace_requests)
            logger.info(
                "Applied %d text replacements to document %s", len(replace_requests), doc_id
            )
        else:
            logger.warning("No placeholder replacements to apply — report_data may be empty")

        logger.info("DD report created successfully: %s", doc_url)

        return {
            "status": "success",
            "document": {
                "id": doc_id,
                "name": doc_name,
                "url": doc_url,
            },
            "replacements_applied": len(replace_requests),
            "message": f"DD report created: {doc_url}",
        }

    except Exception as e:
        logger.error("Failed to create DD report: %s", e)
        return {
            "status": "error",
            "error": "Failed to create DD report",
            "message": str(e),
        }


def main() -> None:
    """Main entry point for the MCP server."""
    logger.info("Starting Due Diligence Reporter MCP server (stdio transport)")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
