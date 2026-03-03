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
    _classify_document_type,
)
from due_diligence_reporter.utils import (
    extract_folder_id_from_url,
    post_google_chat_message,
    send_email,
)
from due_diligence_reporter.wrike import (
    _get_all_site_records,
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
        "description": "List all files in the site's Google Drive folder and its 01_Due Diligence subfolder. Each file includes a doc_type field.",
        "input_schema": {
            "type": "object",
            "properties": {
                "drive_folder_url": {"type": "string", "description": "Google Drive folder URL"},
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
        "description": "Estimate renovation costs using the Building Optimizer API.",
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
        "description": "Create a completed DD report Google Doc.",
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

def _check_site_readiness_direct(
    gc: GoogleClient,
    drive_folder_url: str,
) -> dict[str, Any]:
    """Check site document readiness directly without going through MCP."""
    folder_id = extract_folder_id_from_url(drive_folder_url)
    if not folder_id:
        return {"sir_found": False, "isp_found": False, "report_exists": False, "error": "bad_url"}

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

    all_files = root_files + dd_files
    types_found = {f.get("doc_type") for f in all_files}

    return {
        "sir_found": "sir" in types_found,
        "isp_found": "isp" in types_found,
        "report_exists": "dd_report" in types_found,
        "all_files": all_files,
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

        # Stop if we have a report
        if response.stop_reason == "end_turn" and doc_id:
            break

    if doc_id:
        return {"success": True, "doc_id": doc_id, "doc_url": doc_url}
    return {"success": False, "error": "Agent completed without creating a report"}


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
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

    results: list[dict[str, Any]] = []

    for record in all_records:
        site_title = record.get("title", "Unknown")
        drive_folder_url = extract_google_folder_from_record(record)

        if not drive_folder_url:
            logger.debug("Skipping '%s' — no Drive folder URL", site_title)
            continue

        logger.info("Checking site: %s", site_title)

        try:
            readiness = _check_site_readiness_direct(gc, drive_folder_url)
        except Exception as e:
            logger.error("Failed to check readiness for '%s': %s", site_title, e)
            results.append({"site": site_title, "status": "error", "error": str(e)})
            continue

        sir_found = readiness.get("sir_found", False)
        isp_found = readiness.get("isp_found", False)
        report_exists = readiness.get("report_exists", False)

        # Case 1: Missing required documents → alert
        if not sir_found or not isp_found:
            missing = []
            if not sir_found:
                missing.append("SIR")
            if not isp_found:
                missing.append("ISP")

            msg_lines = [
                f"🔍 DD Check – {site_title}",
                "Status: WAITING ON DOCUMENTS",
                f"  {'✅' if sir_found else '❌'} SIR {'found' if sir_found else 'not found'}",
                f"  {'✅' if isp_found else '❌'} ISP {'found' if isp_found else 'not found'}",
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

    # Summary
    print("\n" + "=" * 60)
    print(f"Daily DD Check — {len(results)} sites processed")
    print("=" * 60)
    for r in results:
        status = r.get("status", "?")
        site = r.get("site", "?")
        if status == "report_created":
            print(f"  ✅ {site} — report created ({r.get('pending_count', 0)} pending fields)")
        elif status == "waiting_on_docs":
            print(f"  ⏳ {site} — waiting on: {', '.join(r.get('missing', []))}")
        elif status == "report_exists":
            print(f"  ℹ️  {site} — report already exists")
        elif status == "report_incomplete":
            print(f"  ⚠️  {site} — report incomplete ({len(r.get('unresolved_tokens', []))} unfilled tokens)")
        elif status == "generation_failed":
            print(f"  ❌ {site} — generation failed: {r.get('error')}")
        else:
            print(f"  ?  {site} — {status}")
    print("=" * 60)


if __name__ == "__main__":
    main()
