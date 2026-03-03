#!/usr/bin/env python3
"""
daily_dd_check.py — Standalone daily cron script for DD report readiness checking.

Scans every active Wrike Site Record that has a Google Drive folder, checks for
SIR + ISP documents, and:
  - Posts a Google Chat alert if required documents are missing.
  - Triggers a DD report generation via Claude API when all docs are present and
    no report exists yet.
  - Checks the generated report for completeness and sends an email if ready.

Run:
    uv run python scripts/daily_dd_check.py

Environment (from .env):
    WRIKE_ACCESS_TOKEN, GOOGLE_CLIENT_CONFIG, GOOGLE_TOKEN_FILE,
    ANTHROPIC_API_KEY, GOOGLE_CHAT_WEBHOOK_URL, DD_REPORT_EMAIL_RECIPIENTS,
    EMAIL_SENDER, EMAIL_APP_PASSWORD, DD_TEMPLATE_GOOGLE_DOC_ID,
    GOOGLE_DRIVE_ROOT_FOLDER_ID, OPENAI_API_KEY, PRICING_API_KEY
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Ensure project src is on path when running as a script
_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv

load_dotenv(_project_root / ".env")

import anthropic

from due_diligence_reporter.config import get_settings
from due_diligence_reporter.google_client import GoogleClient
from due_diligence_reporter.server import (
    DUE_DILIGENCE_SUBFOLDER,
    _build_site_match_terms,
    _classify_document_type,
    _find_site_docs_in_shared_folders,
)
from due_diligence_reporter.utils import (
    extract_folder_id_from_url,
    post_google_chat_message,
    send_email,
)
from due_diligence_reporter.wrike import (
    _get_all_site_records,
    extract_address_from_record,
    extract_google_folder_from_record,
    load_wrike_config,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("daily_dd_check")

# ─────────────────────────────────────────────────────────────────────────────
# Tool definitions for the Claude API call (mirrors the MCP tools)
# ─────────────────────────────────────────────────────────────────────────────

_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_site_record",
        "description": "Fetch a Wrike Site Record by name or ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_name_or_id": {"type": "string", "description": "Site name or Wrike ID"},
            },
            "required": ["site_name_or_id"],
        },
    },
    {
        "name": "list_drive_documents",
        "description": "List all files in the site's Google Drive folder, its 01_Due Diligence subfolder, and shared SIR/ISP/Building Inspection folders. Each file includes a doc_type field. Always pass site_name to find shared folder docs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "drive_folder_url": {"type": "string", "description": "Google Drive folder URL"},
                "site_name": {"type": "string", "description": "Site name from Wrike (used to match docs in shared folders)"},
            },
            "required": ["drive_folder_url"],
        },
    },
    {
        "name": "read_drive_document",
        "description": "Read and return the text content of a Google Drive file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "file_name": {"type": "string"},
            },
            "required": ["file_id", "file_name"],
        },
    },
    {
        "name": "apply_e_occupancy_skill",
        "description": "Apply E-Occupancy scoring to a building.",
        "input_schema": {
            "type": "object",
            "properties": {
                "building_type_description": {"type": "string"},
                "stories": {"type": "integer"},
                "floor_level": {"type": "integer", "default": 1},
                "shared_hvac": {"type": "boolean", "default": False},
                "shared_egress": {"type": "boolean", "default": False},
                "building_management_approval_required": {"type": "boolean", "default": False},
                "no_dedicated_entrance": {"type": "boolean", "default": False},
                "no_outdoor_space": {"type": "boolean", "default": False},
                "shared_parking": {"type": "boolean", "default": False},
                "incompatible_tenants": {"type": "boolean", "default": False},
            },
            "required": ["building_type_description", "stories"],
        },
    },
    {
        "name": "apply_school_approval_skill",
        "description": "Determine school registration requirements for a US state.",
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {"type": "string", "description": "Two-letter US state abbreviation"},
            },
            "required": ["state"],
        },
    },
    {
        "name": "get_cost_estimate",
        "description": "Estimate renovation costs using the Building Optimizer API. Returns report_data_fields with all q3.* template tokens — copy these directly into report_data as flat keys (e.g. report_data['q3.structural_low']). Do NOT nest under q3.cost_estimate_table.",
        "input_schema": {
            "type": "object",
            "properties": {
                "total_building_sf": {"type": "integer"},
                "region": {"type": "string", "default": "default"},
                "rooms": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "sqft": {"type": "integer"},
                        },
                    },
                },
                "classroom_count": {"type": "integer", "default": 0},
            },
            "required": ["total_building_sf"],
        },
    },
    {
        "name": "create_dd_report",
        "description": "Create a completed DD report Google Doc. The report_data dict must use exact template token keys (e.g. 'q1.zoning_designation', 'q3.structural_low'). Copy report_data_fields from skill tools directly into report_data. See prompt.md 'Report Data Schema' for the full token list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_name": {"type": "string"},
                "drive_folder_url": {"type": "string"},
                "report_data": {"type": "object"},
            },
            "required": ["site_name", "drive_folder_url", "report_data"],
        },
    },
    {
        "name": "check_report_completeness",
        "description": "Check a generated DD report for unresolved placeholders.",
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "string"},
            },
            "required": ["doc_id"],
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Tool router — calls the actual Python functions from the MCP server
# ─────────────────────────────────────────────────────────────────────────────

async def _route_tool_call(tool_name: str, tool_input: dict[str, Any]) -> Any:
    """Route a Claude API tool call to the corresponding Python function."""
    import asyncio

    # Import tools lazily to avoid circular import issues
    from due_diligence_reporter import server as srv

    tool_map = {
        "get_site_record": srv.get_site_record,
        "list_drive_documents": srv.list_drive_documents,
        "read_drive_document": srv.read_drive_document,
        "apply_e_occupancy_skill": srv.apply_e_occupancy_skill,
        "apply_school_approval_skill": srv.apply_school_approval_skill,
        "get_cost_estimate": srv.get_cost_estimate,
        "create_dd_report": srv.create_dd_report,
        "check_report_completeness": srv.check_report_completeness,
    }

    fn = tool_map.get(tool_name)
    if fn is None:
        return {"status": "error", "message": f"Unknown tool: {tool_name}"}

    return await fn(**tool_input)


def _route_tool_call_sync(tool_name: str, tool_input: dict[str, Any]) -> Any:
    """Synchronous wrapper for _route_tool_call."""
    import asyncio
    return asyncio.run(_route_tool_call(tool_name, tool_input))


# ─────────────────────────────────────────────────────────────────────────────
# Readiness check (without MCP layer)
# ─────────────────────────────────────────────────────────────────────────────

def _list_shared_folders_once(
    gc: GoogleClient,
) -> dict[str, list[dict[str, Any]]]:
    """List files in the three shared Drive folders once (cached per cron run).

    Returns {"sir": [...], "isp": [...], "building_inspection": [...]}.
    """
    settings = get_settings()
    folder_map = {
        "sir": settings.sir_folder_id,
        "isp": settings.isp_folder_id,
        "building_inspection": settings.building_inspection_folder_id,
    }
    result: dict[str, list[dict[str, Any]]] = {}
    for doc_type, folder_id in folder_map.items():
        if not folder_id:
            result[doc_type] = []
            continue
        try:
            result[doc_type] = gc.list_files_in_folder(folder_id)
        except Exception as e:
            logger.warning("Failed to list shared %s folder (%s): %s", doc_type, folder_id, e)
            result[doc_type] = []
    return result


def _match_site_in_shared_cache(
    match_terms: list[str],
    shared_cache: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any] | None]:
    """Find docs matching any of *match_terms* in the pre-fetched shared folder file lists."""
    needles = [t.lower() for t in match_terms if t]
    result: dict[str, dict[str, Any] | None] = {
        "sir": None,
        "isp": None,
        "building_inspection": None,
    }
    for doc_type, files in shared_cache.items():
        for f in files:
            fname = f.get("name", "").lower()
            if any(needle in fname for needle in needles):
                result[doc_type] = {**f, "doc_type": doc_type}
                break
    return result


def _check_site_readiness_direct(
    gc: GoogleClient,
    drive_folder_url: str,
    match_terms: list[str],
    shared_cache: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Check site document readiness directly without going through MCP."""
    folder_id = extract_folder_id_from_url(drive_folder_url)
    if not folder_id:
        return {
            "sir_found": False, "isp_found": False, "inspection_found": False,
            "report_exists": False, "error": "bad_url",
        }

    # 1. Match docs from pre-fetched shared folder cache
    shared_docs = _match_site_in_shared_cache(match_terms, shared_cache)

    # 2. List + classify site's own folder (fallback)
    root_files = [
        {**f, "doc_type": _classify_document_type(f.get("name", ""))}
        for f in gc.list_files_in_folder(folder_id)
    ]

    dd_files: list[dict[str, Any]] = []
    dd_subfolder = gc.find_subfolder_by_name(folder_id, DUE_DILIGENCE_SUBFOLDER)
    if dd_subfolder:
        dd_sub_id = dd_subfolder.get("id")
        if dd_sub_id:
            dd_files = [
                {**f, "doc_type": _classify_document_type(f.get("name", ""))}
                for f in gc.list_files_in_folder(dd_sub_id)
            ]

    all_site_files = root_files + dd_files

    # 3. Merge — shared folders take priority, site folder fills gaps
    files_by_type: dict[str, dict[str, Any] | None] = {
        "sir": shared_docs.get("sir"),
        "isp": shared_docs.get("isp"),
        "building_inspection": shared_docs.get("building_inspection"),
        "dd_report": None,
    }
    for f in all_site_files:
        dt = f.get("doc_type", "unknown")
        if dt in files_by_type and files_by_type[dt] is None:
            files_by_type[dt] = f

    return {
        "sir_found": files_by_type["sir"] is not None,
        "isp_found": files_by_type["isp"] is not None,
        "inspection_found": files_by_type["building_inspection"] is not None,
        "report_exists": files_by_type["dd_report"] is not None,
        "all_files": all_site_files,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Claude agentic loop — generates one DD report
# ─────────────────────────────────────────────────────────────────────────────

def _run_dd_report_agent(site_title: str, system_prompt: str) -> dict[str, Any]:
    """Run Claude as a tool-calling agent to generate one DD report.

    Returns a dict with keys: success, doc_id, doc_url, error.
    """
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not anthropic_api_key:
        return {"success": False, "error": "ANTHROPIC_API_KEY not set"}

    client = anthropic.Anthropic(api_key=anthropic_api_key)

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": f"Generate a DD Report for: {site_title}"},
    ]

    doc_id: str | None = None
    doc_url: str | None = None
    max_iterations = 40  # Safety limit

    for iteration in range(max_iterations):
        logger.info("Agent iteration %d for site: %s", iteration + 1, site_title)

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=system_prompt,
            tools=_TOOL_DEFINITIONS,
            messages=messages,
        )

        # Collect assistant message
        assistant_content: list[Any] = []
        tool_uses: list[Any] = []

        for block in response.content:
            assistant_content.append(block)
            if block.type == "tool_use":
                tool_uses.append(block)

        messages.append({"role": "assistant", "content": assistant_content})

        # If no tool calls, agent is done
        if not tool_uses:
            logger.info("Agent finished (no more tool calls) after %d iterations", iteration + 1)
            break

        # Execute tool calls and collect results
        tool_results: list[dict[str, Any]] = []
        for tool_use in tool_uses:
            logger.info("Executing tool: %s", tool_use.name)
            try:
                result = _route_tool_call_sync(tool_use.name, tool_use.input)
            except Exception as e:
                logger.error("Tool %s failed: %s", tool_use.name, e)
                result = {"status": "error", "message": str(e)}

            # Capture doc_id from create_dd_report
            if tool_use.name == "create_dd_report" and isinstance(result, dict):
                doc_data = result.get("document", {})
                if doc_data.get("id"):
                    doc_id = doc_data["id"]
                    doc_url = doc_data.get("url")
                    logger.info("Created DD report: %s", doc_url)

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": json.dumps(result),
            })

        messages.append({"role": "user", "content": tool_results})

        # Stop as soon as we have a report — completeness check happens separately
        if doc_id:
            logger.info("Report created, stopping agent loop after %d iterations", iteration + 1)
            break

    if doc_id:
        return {"success": True, "doc_id": doc_id, "doc_url": doc_url}
    return {"success": False, "error": "Agent completed without creating a report"}


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────

def main(site_filter: str | None = None) -> None:
    settings = get_settings()
    wrike_cfg = load_wrike_config()

    # Load the agent system prompt
    prompt_path = _project_root / "prompt.md"
    system_prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""

    # Init Google client
    gc = GoogleClient.from_oauth_config(
        client_config_path=str(settings.get_client_config_path()),
        token_file_path=str(settings.get_token_file_path()),
        oauth_port=settings.oauth_port,
        scopes=settings.google_scopes,
    )

    # Fetch all Wrike site records
    logger.info("Fetching all Wrike site records...")
    all_records = _get_all_site_records(cfg=wrike_cfg)
    logger.info("Found %d site records", len(all_records))

    # Pre-fetch shared folder file lists once for all sites
    logger.info("Listing shared Drive folders (SIR, ISP, Building Inspection)...")
    shared_cache = _list_shared_folders_once(gc)
    logger.info(
        "Shared folder files: SIR=%d, ISP=%d, Inspection=%d",
        len(shared_cache.get("sir", [])),
        len(shared_cache.get("isp", [])),
        len(shared_cache.get("building_inspection", [])),
    )

    results: list[dict[str, Any]] = []

    for record in all_records:
        site_title = record.get("title", "Unknown")

        if site_filter and site_filter.lower() not in site_title.lower():
            continue

        drive_folder_url = extract_google_folder_from_record(record)

        if not drive_folder_url:
            logger.debug("Skipping '%s' — no Drive folder URL", site_title)
            continue

        address = extract_address_from_record(record)
        match_terms = _build_site_match_terms(site_title, address)
        logger.info("Checking site: %s (match terms: %s)", site_title, match_terms)

        try:
            readiness = _check_site_readiness_direct(gc, drive_folder_url, match_terms, shared_cache)
        except Exception as e:
            logger.error("Failed to check readiness for '%s': %s", site_title, e)
            results.append({"site": site_title, "status": "error", "error": str(e)})
            continue

        sir_found = readiness.get("sir_found", False)
        isp_found = readiness.get("isp_found", False)
        inspection_found = readiness.get("inspection_found", False)
        report_exists = readiness.get("report_exists", False)

        # Case 1: Missing required documents → alert
        if not sir_found or not isp_found or not inspection_found:
            missing = []
            if not sir_found:
                missing.append("SIR")
            if not isp_found:
                missing.append("ISP")
            if not inspection_found:
                missing.append("Building Inspection")

            msg_lines = [
                f"🔍 DD Check – {site_title}",
                "Status: WAITING ON DOCUMENTS",
                f"  {'✅' if sir_found else '❌'} SIR {'found' if sir_found else 'not found'}",
                f"  {'✅' if isp_found else '❌'} ISP {'found' if isp_found else 'not found'}",
                f"  {'✅' if inspection_found else '❌'} Building Inspection {'found' if inspection_found else 'not found'}",
                f"Drive: {drive_folder_url}",
            ]
            chat_msg = "\n".join(msg_lines)

            if settings.google_chat_webhook_url:
                try:
                    post_google_chat_message(settings.google_chat_webhook_url, chat_msg)
                except Exception as e:
                    logger.error("Failed to post Chat message for '%s': %s", site_title, e)

            results.append({
                "site": site_title,
                "status": "waiting_on_docs",
                "missing": missing,
            })
            continue

        # Case 2: All docs present + report already exists → skip
        if report_exists:
            logger.info("'%s' — report already exists, skipping", site_title)
            results.append({"site": site_title, "status": "report_exists"})
            continue

        # Case 3: All docs present + no report yet → generate
        logger.info("'%s' — all docs present, generating report...", site_title)

        if settings.google_chat_webhook_url:
            try:
                post_google_chat_message(
                    settings.google_chat_webhook_url,
                    f"✅ DD Check – {site_title}\nAll documents present. Generating report now...",
                )
            except Exception as e:
                logger.error("Failed to post Chat message for '%s': %s", site_title, e)

        agent_result = _run_dd_report_agent(site_title, system_prompt)

        if not agent_result.get("success"):
            err = agent_result.get("error", "unknown error")
            logger.error("Report generation failed for '%s': %s", site_title, err)
            if settings.google_chat_webhook_url:
                try:
                    post_google_chat_message(
                        settings.google_chat_webhook_url,
                        f"❌ DD Report generation FAILED for {site_title}\nError: {err}",
                    )
                except Exception:
                    pass
            results.append({"site": site_title, "status": "generation_failed", "error": err})
            continue

        doc_id = agent_result["doc_id"]
        doc_url = agent_result.get("doc_url", "")

        # Check completeness before sending email
        import asyncio
        from due_diligence_reporter import server as srv
        completeness = asyncio.run(srv.check_report_completeness(doc_id))

        if not completeness.get("ready_to_send", False):
            unresolved = completeness.get("unresolved_tokens", [])
            alert = (
                f"⚠️ DD Report for {site_title} has {len(unresolved)} unfilled placeholder(s).\n"
                f"Tokens: {', '.join(unresolved[:10])}\n"
                f"Report: {doc_url}"
            )
            logger.warning("Report not ready to send for '%s': %s", site_title, alert)
            if settings.google_chat_webhook_url:
                try:
                    post_google_chat_message(settings.google_chat_webhook_url, alert)
                except Exception:
                    pass
            results.append({
                "site": site_title,
                "status": "report_incomplete",
                "unresolved_tokens": unresolved,
            })
            continue

        # Send email
        if (
            settings.email_sender
            and settings.email_app_password
            and settings.dd_report_email_recipients
        ):
            recipients = [
                r.strip()
                for r in settings.dd_report_email_recipients.split(",")
                if r.strip()
            ]
            pending_summary = completeness.get("summary", "")
            pending_labels = completeness.get("pending_sections", [])
            pending_block = ""
            if pending_labels:
                pending_block = (
                    "<h3>Pending Sections</h3>"
                    "<p>The following fields could not be filled because source data was not available:</p>"
                    "<ul>" + "".join(f"<li>{p}</li>" for p in pending_labels) + "</ul>"
                )

            html_body = f"""
<html><body>
<h2>Due Diligence Report — {site_title}</h2>
<p>A new Due Diligence report has been generated for <strong>{site_title}</strong>.</p>
<p><a href="{doc_url}" style="font-size:16px;font-weight:bold;">View Report in Google Docs</a></p>
{pending_block}
<p style="color:#888;font-size:12px;">Generated automatically by the Alpha DD Reporter.</p>
</body></html>
"""
            try:
                send_email(
                    sender=settings.email_sender,
                    app_password=settings.email_app_password,
                    recipients=recipients,
                    subject=f"DD Report Ready — {site_title}",
                    html_body=html_body,
                )
                logger.info("Email sent for '%s' to %s", site_title, recipients)
            except Exception as e:
                logger.error("Failed to send email for '%s': %s", site_title, e)

        results.append({
            "site": site_title,
            "status": "report_created",
            "doc_url": doc_url,
            "pending_count": completeness.get("pending_section_count", 0),
        })

    # Summary — use ASCII-safe markers to avoid encoding errors on Windows
    print("\n" + "=" * 60)
    print(f"Daily DD Check -- {len(results)} sites processed")
    print("=" * 60)
    for r in results:
        status = r.get("status", "?")
        site = r.get("site", "?")
        if status == "report_created":
            print(f"  [OK] {site} -- report created ({r.get('pending_count', 0)} pending fields)")
        elif status == "waiting_on_docs":
            print(f"  [..] {site} -- waiting on: {', '.join(r.get('missing', []))}")
        elif status == "report_exists":
            print(f"  [--] {site} -- report already exists")
        elif status == "report_incomplete":
            print(f"  [!!] {site} -- report incomplete ({len(r.get('unresolved_tokens', []))} unfilled tokens)")
        elif status == "generation_failed":
            print(f"  [XX] {site} -- generation failed: {r.get('error')}")
        else:
            print(f"  [??] {site} -- {status}")
    print("=" * 60)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Daily DD readiness check and report generation")
    parser.add_argument("--site", type=str, default=None, help="Run for a single site (substring match on title)")
    args = parser.parse_args()
    main(site_filter=args.site)
