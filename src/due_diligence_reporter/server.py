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

# ─────────────────────────────────────────────────────────────────────────────
# E-OCCUPANCY SKILL DATA
# ─────────────────────────────────────────────────────────────────────────────

# (base_score, label, keywords) — keyword matching picks the longest keyword hit
_EOCCUPANCY_BUILDING_TYPES: list[tuple[int, str, list[str]]] = [
    (100, "Current K-12 school", ["k-12", "elementary school", "middle school", "high school", "existing school"]),
    (95, "Daycare / childcare", ["daycare", "childcare", "preschool", "pre-k"]),
    (92, "Office 1–3 stories", [
        "1-story office", "2-story office", "3-story office",
        "1 story office", "2 story office", "3 story office",
        "low-rise office", "single story office", "single-story office",
    ]),
    (90, "Gym / fitness center", ["gym", "fitness center", "health club", "yoga studio", "crossfit"]),
    (88, "Flex / light industrial (with HVAC)", [
        "flex space", "light industrial with hvac", "warehouse office",
        "flex industrial", "light industrial (with hvac)",
    ]),
    (85, "Retail strip — individual unit", ["strip mall unit", "retail unit", "individual retail"]),
    (82, "Office — general", ["office building", "professional office", "corporate office", "general office"]),
    (78, "Small / mid-size church", ["small church", "community church", "chapel"]),
    (75, "Medical office / retail strip center", [
        "medical office", "dental office", "dental", "clinic", "urgent care",
        "retail strip center", "strip mall", "shopping center", "strip center",
    ]),
    (58, "Warehouse with HVAC", [
        "warehouse with hvac", "conditioned warehouse",
        "climate controlled warehouse", "heated warehouse",
    ]),
    (55, "Small assembly venue", ["event space", "banquet hall", "small assembly", "small theater", "assembly venue"]),
    (42, "High-rise 4–6 stories (cap)", [
        "4-story", "5-story", "6-story", "4 story", "5 story", "6 story",
    ]),
    (38, "Large church / worship center", [
        "large church", "megachurch", "cathedral", "temple", "mosque",
        "worship center", "church",
    ]),
    (35, "Warehouse without HVAC", ["warehouse", "cold shell", "distribution center"]),
    (32, "Nightclub / large bar", ["nightclub", "large bar", "night club", "lounge"]),
    (30, "Historic / landmark building", ["historic building", "landmark", "national register", "shpo"]),
    (28, "Large assembly / cold storage", [
        "theater", "concert hall", "auditorium", "cinema",
        "cold storage", "freezer storage", "movie theater",
    ]),
    (25, "Data center", ["data center", "server farm"]),
    (22, "Big box retail (100k+ SF)", ["big box", "walmart", "target", "costco", "mall anchor", "anchor store"]),
    (20, "High-rise 7+ stories (cap)", [
        "7-story", "8-story", "9-story", "10-story",
        "7 story", "8 story", "9 story", "10 story",
        "high-rise", "high rise", "skyscraper", "tower",
    ]),
    (18, "Hospital / nursing home", [
        "hospital", "medical center", "surgical center", "nursing home", "assisted living",
    ]),
    (15, "Bank", ["bank branch", "credit union", "bank"]),
    (12, "Restaurant", ["restaurant", "cafe", "diner", "bistro", "grill", "food service"]),
    (0, "Do not pursue", [
        "gas station", "fuel station", "petroleum", "fueling station",
        "dry cleaner", "dry clean", "perchloroethylene",
        "auto body", "collision repair", "body shop", "paint shop",
        "heavy manufacturing", "manufacturing plant", "industrial plant", "fabrication plant",
        "chemical storage", "hazmat storage",
        "mortuary", "funeral home", "crematorium",
        "adult entertainment", "strip club",
        "jail", "prison", "detention center", "correctional",
    ]),
]

_EOCCUPANCY_TENANT_DEDUCTIONS: dict[str, int] = {
    "shared_hvac": -5,
    "shared_egress": -5,
    "building_management_approval_required": -5,
    "no_dedicated_entrance": -5,
    "no_outdoor_space": -5,
    "shared_parking": -3,
    "incompatible_tenants": -5,
}

_EOCCUPANCY_TIMELINES: list[tuple[int, int, str]] = [
    (100, 100, "Ready to proceed"),
    (90, 99, "3–6 months"),
    (70, 89, "6–9 months"),
    (50, 69, "9–12 months"),
    (30, 49, "12–18 months"),
    (15, 29, "18–24+ months"),
    (1, 14, "24+ months"),
    (0, 0, "N/A — do not pursue"),
]

_EOCCUPANCY_TIER_LABELS: dict[int, str] = {
    1: "Tier 1 — Do Not Pursue",
    2: "Tier 2 — Complex",
    3: "Tier 3 — Moderate",
    4: "Tier 4 — Easy-Moderate",
    5: "Tier 5 — Very Easy",
}


def _match_building_type(description: str) -> tuple[int, str]:
    """Return (base_score, label) for a free-form building type description.

    Uses longest-keyword-match so more specific terms win over generic ones
    (e.g. "small church" beats "church").
    """
    desc = description.lower()
    best_score: int | None = None
    best_label = ""
    best_len = 0

    for score, label, keywords in _EOCCUPANCY_BUILDING_TYPES:
        for kw in keywords:
            if kw in desc and len(kw) > best_len:
                best_len = len(kw)
                best_score = score
                best_label = label

    if best_score is None:
        return 75, "Office — general (default)"
    return best_score, best_label


def _e_occupancy_timeline(score: int) -> str:
    for low, high, tl in _EOCCUPANCY_TIMELINES:
        if low <= score <= high:
            return tl
    return "Unknown"


def _e_occupancy_tier(score: int) -> int:
    if score == 0:
        return 1
    if score <= 42:
        return 2
    if score <= 69:
        return 3
    if score <= 89:
        return 4
    return 5


# ─────────────────────────────────────────────────────────────────────────────
# SCHOOL APPROVAL SKILL DATA
# ─────────────────────────────────────────────────────────────────────────────

# state -> (score, approval_type, gating, timeline_days)
_STATE_APPROVAL_TABLE: dict[str, tuple[int, str, bool, int]] = {
    "TX": (95, "NONE", False, 7),
    "ID": (92, "NONE", False, 7),
    "AK": (90, "NONE", False, 7),
    "OK": (90, "REGISTRATION_SIMPLE", False, 30),
    "WY": (90, "NONE", False, 7),
    "MT": (88, "NONE", False, 7),
    "MO": (88, "NONE", False, 7),
    "IN": (87, "NONE", False, 7),
    "IL": (86, "NONE", False, 7),
    "KS": (86, "NONE", False, 7),
    "NE": (86, "NONE", False, 7),
    "AL": (85, "NONE", False, 7),
    "AZ": (82, "REGISTRATION_SIMPLE", False, 30),
    "CO": (80, "REGISTRATION_SIMPLE", False, 30),
    "FL": (78, "REGISTRATION_SIMPLE", False, 30),
    "GA": (78, "REGISTRATION_SIMPLE", False, 30),
    "NC": (78, "REGISTRATION_SIMPLE", False, 30),
    "TN": (78, "REGISTRATION_SIMPLE", False, 30),
    "UT": (78, "REGISTRATION_SIMPLE", False, 30),
    "AR": (76, "REGISTRATION_SIMPLE", False, 30),
    "LA": (76, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "SC": (76, "REGISTRATION_SIMPLE", False, 30),
    "VA": (75, "REGISTRATION_SIMPLE", False, 30),
    "WI": (75, "REGISTRATION_SIMPLE", False, 30),
    "MI": (74, "REGISTRATION_SIMPLE", False, 30),
    "MN": (74, "REGISTRATION_SIMPLE", False, 30),
    "OH": (74, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "NM": (72, "REGISTRATION_SIMPLE", False, 30),
    "NV": (72, "LICENSE_REQUIRED", True, 150),
    "WA": (72, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "OR": (70, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "DE": (68, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "KY": (68, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "WV": (68, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "HI": (65, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "IA": (65, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "NH": (65, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "CT": (62, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "ME": (62, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "VT": (62, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "CA": (60, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "NJ": (60, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "PA": (60, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "MA": (58, "LOCAL_APPROVAL_REQUIRED", True, 120),
    "MD": (55, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "RI": (55, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "NY": (45, "COMPLEX_OR_OVERSIGHT", True, 365),
    "ND": (42, "COMPLEX_OR_OVERSIGHT", True, 365),
    "DC": (40, "COMPLEX_OR_OVERSIGHT", True, 365),
}

_SCHOOL_APPROVAL_STEPS: dict[str, str] = {
    "NONE": (
        "1. File Articles of Incorporation or LLC formation documents\n"
        "2. Obtain standard local business license\n"
        "3. Notify local fire marshal and schedule occupancy inspection\n"
        "4. Post required notices and begin operations"
    ),
    "REGISTRATION_SIMPLE": (
        "1. Register private school with state Department of Education (online portal)\n"
        "2. Submit curriculum overview and student enrollment plan\n"
        "3. Obtain standard local business license\n"
        "4. Pass health and fire inspections\n"
        "5. Begin operations after registration confirmation"
    ),
    "CERTIFICATE_OR_APPROVAL_REQUIRED": (
        "1. Apply for private school Certificate of Approval from state education department\n"
        "2. Submit detailed curriculum, staff credentials, and facility documentation\n"
        "3. Schedule and pass state facility inspection\n"
        "4. Obtain certificate of approval before opening (gating requirement)\n"
        "5. Maintain annual compliance reporting and renewal"
    ),
    "LICENSE_REQUIRED": (
        "1. Apply for private school license with state education department\n"
        "2. Demonstrate compliance with state educational standards and staffing requirements\n"
        "3. Undergo facility inspection and health review\n"
        "4. Obtain license before opening (gating requirement)\n"
        "5. Maintain license with annual renewal"
    ),
    "LOCAL_APPROVAL_REQUIRED": (
        "1. Submit application to local school committee or board of education\n"
        "2. Present curriculum, educational plan, and facility details at public hearing\n"
        "3. Obtain local board approval (gating requirement)\n"
        "4. Register with state Department of Education after local approval\n"
        "5. Pass health and fire inspections\n"
        "6. Maintain annual reporting requirements"
    ),
    "COMPLEX_OR_OVERSIGHT": (
        "1. Retain legal counsel specializing in state education law\n"
        "2. Submit comprehensive application to state Board of Education\n"
        "3. Undergo curriculum review and staff credential verification\n"
        "4. Complete facility inspections and compliance review\n"
        "5. Attend state board review hearing\n"
        "6. Obtain state approval before opening (gating requirement — 12+ months)\n"
        "7. Maintain ongoing state oversight and annual reporting"
    ),
}


def _school_zone(score: int) -> str:
    if score >= 80:
        return "GREEN"
    if score >= 41:
        return "YELLOW"
    return "RED"


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
async def apply_e_occupancy_skill(
    building_type_description: str,
    stories: int,
    floor_level: int = 1,
    shared_hvac: bool = False,
    shared_egress: bool = False,
    building_management_approval_required: bool = False,
    no_dedicated_entrance: bool = False,
    no_outdoor_space: bool = False,
    shared_parking: bool = False,
    incompatible_tenants: bool = False,
) -> dict[str, Any]:
    """Apply the E-Occupancy Skill to score a building for educational use conversion.

    Evaluates the building's current use against the Alpha School E-Occupancy scoring
    matrix and returns a complete structured assessment. Call this tool in Step 4 after
    identifying the building's current use from source documents or the Wrike record.

    Args:
        building_type_description: Free-form description of current building use
            (e.g., "3-story medical office building", "retail strip center", "gym").
        stories: Total number of stories in the building.
        floor_level: Floor level of the tenant space (1 = ground floor). Defaults to 1.
        shared_hvac: True if HVAC is shared with other tenants.
        shared_egress: True if building egress / entrance is shared.
        building_management_approval_required: True if landlord/mgmt approval needed.
        no_dedicated_entrance: True if no dedicated street-level entrance exists.
        no_outdoor_space: True if no access to outdoor space for students.
        shared_parking: True if parking is shared with other tenants.
        incompatible_tenants: True if other tenants are incompatible with school use.

    Returns:
        Dict with score, zone, tier, timeline, confidence, and ready-to-use
        report_data_fields for q2.e_occupancy_*.
    """
    logger.info(
        "Tool called: apply_e_occupancy_skill — building_type=%s, stories=%d",
        building_type_description,
        stories,
    )

    base_score, matched_type = _match_building_type(building_type_description)

    # Apply height override (absolute ceiling, even on score-0 types this is a no-op)
    if base_score > 0:
        if stories >= 7:
            base_score = min(base_score, 20)
        elif stories >= 4:
            base_score = min(base_score, 42)

    # Apply tenant deductions only for floors 1–3 (floors 4+ already capped by height rules)
    score = base_score
    deductions: list[str] = []

    if base_score > 0 and floor_level <= 3:
        if shared_hvac:
            score += _EOCCUPANCY_TENANT_DEDUCTIONS["shared_hvac"]
            deductions.append("shared HVAC (−5)")
        if shared_egress:
            score += _EOCCUPANCY_TENANT_DEDUCTIONS["shared_egress"]
            deductions.append("shared egress (−5)")
        if building_management_approval_required:
            score += _EOCCUPANCY_TENANT_DEDUCTIONS["building_management_approval_required"]
            deductions.append("building management approval required (−5)")
        if no_dedicated_entrance:
            score += _EOCCUPANCY_TENANT_DEDUCTIONS["no_dedicated_entrance"]
            deductions.append("no dedicated entrance (−5)")
        if no_outdoor_space:
            score += _EOCCUPANCY_TENANT_DEDUCTIONS["no_outdoor_space"]
            deductions.append("no outdoor space (−5)")
        if shared_parking:
            score += _EOCCUPANCY_TENANT_DEDUCTIONS["shared_parking"]
            deductions.append("shared parking (−3)")
        if incompatible_tenants:
            score += _EOCCUPANCY_TENANT_DEDUCTIONS["incompatible_tenants"]
            deductions.append("incompatible tenants (−5)")
        # Never below 1 unless environmental hazard (score 0 from type matching)
        if score < 1:
            score = 1

    score = max(0, min(100, score))

    zone = "GREEN" if score == 100 else ("RED" if score == 0 else "YELLOW")
    tier = _e_occupancy_tier(score)
    tier_label = _EOCCUPANCY_TIER_LABELS.get(tier, str(tier))
    timeline = _e_occupancy_timeline(score)

    # Confidence: HIGH if type matched clearly, MEDIUM if deductions applied, LOW if default
    if matched_type.endswith("(default)"):
        confidence = "LOW"
    elif deductions:
        confidence = "MEDIUM"
    else:
        confidence = "HIGH"

    deduction_note = f"Deductions: {', '.join(deductions)}." if deductions else "No tenant deductions."

    return {
        "status": "success",
        "matched_building_type": matched_type,
        "base_score": base_score,
        "deductions_applied": deductions,
        "final_score": score,
        "zone": zone,
        "tier": tier_label,
        "timeline": timeline,
        "confidence": confidence,
        "report_data_fields": {
            "q2.e_occupancy_score": str(score),
            "q2.e_occupancy_zone": zone,
            "q2.e_occupancy_tier": tier_label,
            "q2.e_occupancy_timeline": timeline,
            "q2.e_occupancy_confidence": confidence,
        },
        "message": (
            f"E-Occupancy: {score}/100 — {zone} ({tier_label}, {timeline}). "
            f"Matched building type: {matched_type}. {deduction_note}"
        ),
    }


@mcp.tool()
async def apply_school_approval_skill(
    state: str,
) -> dict[str, Any]:
    """Apply the School Approval Skill to determine registration requirements for a state.

    Looks up the state in the Alpha School private school approval difficulty table and
    returns all registration requirements needed for Q1 — State School Registration.
    Call this tool in Step 5 using the state extracted from the site address.

    Args:
        state: Two-letter US state abbreviation (e.g., "TX", "CA", "FL").
            Use "DC" for Washington D.C.

    Returns:
        Dict with approval_type, gating, timeline, steps, summary, and ready-to-use
        report_data_fields for q1.state_school_registration, school_approval_type,
        school_approval_gating, school_approval_timeline_days, steps_to_allow_operation.
    """
    logger.info("Tool called: apply_school_approval_skill — state=%s", state)

    state_upper = state.strip().upper()

    if state_upper in _STATE_APPROVAL_TABLE:
        score, approval_type, gating, timeline_days = _STATE_APPROVAL_TABLE[state_upper]
        confidence = "HIGH"
    else:
        score, approval_type, gating, timeline_days = 70, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90
        confidence = "LOW"
        logger.warning("State '%s' not in approval table — using default values", state_upper)

    zone = _school_zone(score)
    steps = _SCHOOL_APPROVAL_STEPS.get(
        approval_type, _SCHOOL_APPROVAL_STEPS["CERTIFICATE_OR_APPROVAL_REQUIRED"]
    )

    if zone == "GREEN":
        summary = (
            f"{state_upper} has minimal private school requirements "
            f"({approval_type.replace('_', ' ').title()}). "
            f"Timeline: {timeline_days} days."
        )
    elif zone == "YELLOW":
        gating_note = " This is a gating requirement before opening." if gating else ""
        summary = (
            f"{state_upper} requires {approval_type.replace('_', ' ').title()} "
            f"for private schools. Timeline: {timeline_days} days.{gating_note}"
        )
    else:
        summary = (
            f"{state_upper} has complex private school oversight requirements. "
            f"Gating approval required; timeline: {timeline_days}+ days. "
            "Engage legal counsel early."
        )

    return {
        "status": "success",
        "state": state_upper,
        "score": score,
        "zone": zone,
        "approval_type": approval_type,
        "gating": gating,
        "timeline_days": timeline_days,
        "confidence": confidence,
        "steps_to_allow_operation": steps,
        "state_school_registration_summary": summary,
        "report_data_fields": {
            "q1.state_school_registration": summary,
            "q1.school_approval_type": approval_type,
            "q1.school_approval_gating": str(gating).lower(),
            "q1.school_approval_timeline_days": str(timeline_days),
            "q1.steps_to_allow_operation": steps,
        },
        "message": (
            f"School approval for {state_upper}: {zone} (score {score}/100), "
            f"{approval_type}, {timeline_days}-day timeline."
        ),
    }


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
