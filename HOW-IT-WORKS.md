# Due Diligence Reporter — How It Works

**Version:** 3.0.0
**Team:** EDU Ops Intelligence
**Last Updated:** 2026-03-03

---

## Overview

The Due Diligence Reporter is an AI agent powered by Claude that generates Site Due Diligence (DD) Reports for potential Alpha School locations. It operates in three modes:

1. **Interactive** — A human gives it a site name in chat via MCP Hive. The agent gathers data, runs analytical skills, and produces an executive-ready Google Doc.
2. **Event-Driven (Inbox Scan — every 15 min)** — A scheduled script scans the `edu.ops@trilogy.com` inbox for new SIR and Building Inspection PDFs, classifies them with GPT-5.2, uploads to the correct shared Drive folder, then immediately checks if the site is ready for report generation.
3. **Daily Sweep (Safety Net — 9 AM)** — A scheduled script scans all Wrike Site Records in active DD stages. When a site has all three required documents (SIR, ISP, Building Inspection) and no existing report, it triggers full report generation. This catches anything the 15-min scan missed.

The agent gathers facts. It does not make recommendations. The decision belongs to the leadership team.

---

## Architecture at a Glance

```
                              ┌──────────────────────────┐
                              │  Inbox Scan (every 15min) │
                              │  scan_inbox.py            │
                              │  GPT-5.2 classification   │
    ┌─────────────────────┐   │  + report pipeline        │
    │  Daily Sweep (9 AM) │   └────────────┬─────────────┘
    │  daily_dd_check.py  │                │
    │  (active stages only)│                │
    └──────────┬──────────┘                │
               │                           │
Human (chat)   │       report_pipeline.py  │  shared pipeline module
    │          │          ┌────────────────┘
    │          │          │
    │  prompt.md          │  same tools, same prompt
    ▼          ▼          ▼
Claude AI Agent ◄─────────┘
    │
    │  calls tools via MCP protocol (stdio) or direct Python
    ▼
┌──────────────────────────────────────────────────────────┐
│  MCP Server  (FastMCP / Python)                          │
│  dd-reporter — 10 tools                                  │
│                                                          │
│  Tools:                                                  │
│  ├─ get_site_record          (Wrike lookup)              │
│  ├─ list_drive_documents     (Drive + shared folders)    │
│  ├─ read_drive_document      (Drive file reader)         │
│  ├─ apply_e_occupancy_skill  (E-Occ scoring)             │
│  ├─ apply_school_approval_skill (State registration)     │
│  ├─ get_cost_estimate        (Building Optimizer API)    │
│  ├─ create_dd_report         (Template copy + fill)      │
│  ├─ check_site_readiness     (Doc presence gate)         │
│  ├─ check_report_completeness (Token scan)               │
│  └─ send_dd_report_email     (Gmail SMTP)                │
│                                                          │
│  Report Schema:                                          │
│  └─ report_schema.py         (108 tokens + alias map)    │
└──────────────┬───────────────────────────────────────────┘
               │
    ┌──────────┼──────────────────────────┐
    ▼          ▼              ▼           ▼
 Wrike API  Google          Building    Gmail SMTP +
 (v4)       Drive/Docs/     Optimizer   Google Chat
            Gmail (OAuth)   Pricing API Webhook
```

---

## Inbox Scanner — Event-Driven Pipeline

**Script:** `scripts/scan_inbox.py`
**Module:** `src/due_diligence_reporter/inbox_scanner.py`
**Schedule:** Every 15 minutes, 8 AM–6 PM Central, Monday–Friday (GitHub Actions cron: `*/15 13-23 * * 1-5` UTC)
**Workflow:** `.github/workflows/inbox-scan.yml`

The inbox scanner is the primary trigger for report generation. When a vendor emails a SIR or Building Inspection to `edu.ops@trilogy.com`, the scanner picks it up within 15 minutes.

### Phase 1 — Scan, Classify, Upload

```
For each unprocessed email with PDF attachments:
  1. Extract email metadata (subject, sender, body snippet, attachments)
  2. Classify each PDF with GPT-5.2:
     - doc_type: "sir", "building_inspection", or "unknown"
     - matched_site_id: which Wrike site record this belongs to
     - confidence: 0.0–1.0
  3. If confidence >= 0.7 and doc_type is supported:
     a. Generate standardized filename (e.g., "Mar 03 2026 - Alpha Keller SIR.pdf")
     b. Check for duplicates in target shared folder
     c. Upload to correct shared Drive folder (SIR → SIR folder, BI → BI folder)
  4. If confidence < 0.7 → flag for manual review in Google Chat
  5. Mark email as processed (DD-Processed label)
```

**Building Inspection naming convention:** Reports are titled `[Brand] [City] Building Inspection Report` (e.g., "Alpha Keller Building Inspection Report"), which helps GPT-5.2 match them to the correct site record.

**Supported doc types:** `sir`, `building_inspection`. ISPs arrive through a separate channel and are placed manually.

### Phase 2 — Per-Site Pipeline

After all uploads complete, the scanner identifies which sites received new documents and runs the report pipeline for each:

```
For each unique site that received an upload:
  1. Look up Wrike record → get Drive folder URL → build match terms
  2. Refresh shared folder cache (picks up just-uploaded files)
  3. Run process_site_pipeline():
     a. Check readiness (SIR + ISP + Inspection present?)
     b. If missing docs → post Google Chat alert with checklist
     c. If report exists → skip
     d. If ready → trigger Claude agent loop → check completeness → email
  4. Post result to Google Chat
```

**Flags:**
- `--scan-only` — Run Phase 1 only (inbox scan), skip the pipeline
- `--dry-run` — Classify and match without uploading or marking emails

---

## Document Detection — Dual Strategy

The system must find three source documents before generating a report. Documents can live in two places:

### Primary: Shared Drive Folders

Three shared folders hold documents across all sites:

| Doc Type | Folder | Config Key |
|----------|--------|------------|
| SIR (Site Investigation Report) | `1TTjxOEfjeJZoXMAeGueJ1QbVBzXBDE4C` | `SIR_FOLDER_ID` |
| ISP (Instant School Plan) | `1E9RXgVeKxeITUdFw5lvyolCx6CJLEFUg` | `ISP_FOLDER_ID` |
| Building Inspection | `15dfKaAnic9VRKhp_-vFSpTr7uPk_hhKo` | `BUILDING_INSPECTION_FOLDER_ID` |

Files are matched by checking if any of the site's match terms (site title, city name, street number) appear as a case-insensitive substring in the filename.

### Fallback: Site's Own Drive Folder

If a document isn't found in the shared folders, the system checks the site's root folder and its `01_Due Diligence` subfolder. Files are classified by keyword matching on the filename (`_classify_document_type`).

### Readiness Gate

A report is only generated when all three conditions are met:

```
ready_for_report = sir_found AND isp_found AND inspection_found AND NOT report_exists
```

---

## Step-by-Step Workflow

### Step 1 — Receive Request (Interactive) or Trigger (Automated)

**Interactive mode:** Human provides a site name in chat. The agent begins the workflow.

**Inbox scan mode:** `scan_inbox.py` detects a new upload and triggers the pipeline for that specific site.

**Daily sweep mode:** `daily_dd_check.py` fetches all Wrike Site Records, filters to active DD stages only ("2. Evaluating Potential Site (LOI)" and "3. Site Chosen, FTO in Progress"), pre-fetches the three shared Drive folders once, then checks each site's readiness.

---

### Step 2 — Look Up the Wrike Site Record

**Tool:** `get_site_record(site_name_or_id)`

**Activity:**
1. Checks if input is a direct Wrike ID or permalink URL — fetches directly
2. Otherwise fetches all Site Records from Wrike space `IEAGN6I6I5RFSYZI` in batches of 100
3. Uses **GPT-5.2** to fuzzy-match the query against all record titles and addresses
4. Enriches the matched record with human-readable custom field names

**Output:** Site title, Wrike ID, address, school type, stage, Google Drive folder URL, and all custom fields.

---

### Step 3 — Index the Site's Drive Folder

**Tool:** `list_drive_documents(drive_folder_url, site_name)`

**Activity:**
1. Lists files in the root site folder and `01_Due Diligence` subfolder
2. Classifies each file by `doc_type` (sir, isp, building_inspection, phase_i_esa, matterport, dd_report, unknown)
3. Searches the three shared Drive folders for matching documents using the site's match terms
4. Merges results — shared folder matches take priority

**Output:** Combined file list with `doc_type`, file ID, name, MIME type, and Drive link for each file.

---

### Step 4 — Read Source Documents

**Tool:** `read_drive_document(file_id, file_name)` — called once per document

**Activity per file:**
- **Google Docs / Sheets / Slides** — exported as plain text via Drive API
- **PDFs** — downloaded as bytes, text extracted with `pypdf` (large docs truncated to ~15,000 chars for context)
- **Plain text** — downloaded directly

**Key documents and what the agent extracts:**

| Document | Extracted Data |
|----------|---------------|
| **SIR** | Zoning, AHJ contacts, permits required, pre-app meeting, permit timeline, cost risks, schedule risks |
| **Building Inspection** | Year built, construction type, stories, SF, sprinklers, fire alarm, ADA deficiencies, egress, restrooms, scope of work, conversion risk level |
| **ISP (Program Fit Analysis)** | Room list with types/sqft, program fit score, classroom count, ADA pre-check score, optimization proposals |
| **Phase I ESA** | Environmental contamination findings, UST database results |
| **Matterport** | 3D scan link |

---

### Step 5 — Run E-Occupancy Skill

**Tool:** `apply_e_occupancy_skill(building_type_description, stories, ...)`

**Activity:**
1. Matches building type against a scoring matrix using longest-keyword-match
2. Applies height ceiling (4-6 stories capped at 42; 7+ capped at 20)
3. Applies tenant deductions for shared HVAC (-5), shared egress (-5), no dedicated entrance (-5), no outdoor space (-5), building management approval (-5), shared parking (-3), incompatible tenants (-5)
4. Assigns confidence (HIGH/MEDIUM/LOW)

**Returns `report_data_fields`:**
```
q2.e_occupancy_score, q2.e_occupancy_zone, q2.e_occupancy_tier,
q2.e_occupancy_timeline, q2.e_occupancy_confidence
```

---

### Step 6 — Run School Approval Skill

**Tool:** `apply_school_approval_skill(state)`

**Activity:**
1. Looks up the two-letter state code in a built-in approval table (all 50 states + DC)
2. Returns approval type, gating status, timeline, and required steps

**Returns `report_data_fields`:**
```
q1.state_school_registration, q1.school_approval_type,
q1.school_approval_gating, q1.school_approval_timeline_days,
q1.steps_to_allow_operation
```

---

### Step 7 — Get Cost Estimate

**Tool:** `get_cost_estimate(total_building_sf, region, rooms, classroom_count)`

**Activity:**
1. Uses ISP room list if available; otherwise auto-generates rooms from SF and classroom count
2. Calls Building Optimizer Pricing API at two finish levels (Refresh = low, Alpha = high) to produce low/high ranges
3. Adds per-SF estimates for code items not in the API (structural $8-25/SF, sprinkler $3-7/SF, fire alarm $2-4/SF, ADA $2-8/SF)
4. Calculates contingency (15-20% of subtotal)

**Returns `report_data_fields` — 24 keys:**
```
q3.structural_low/high, q3.mep_low/high, q3.sprinkler_low/high,
q3.fire_alarm_low/high, q3.ada_low/high, q3.bathrooms_low/high,
q3.finish_work_low/high, q3.ffe_low/high, q3.contingency_low/high,
q3.total_low/high, q3.calculated_budget, q3.budget_formula,
q3.budget_status, q3.key_cost_risks
```

---

### Step 8 — Compile and Write the DD Report

**Tool:** `create_dd_report(site_name, drive_folder_url, report_data)`

This is where the report schema system ensures every template placeholder gets filled.

**Activity:**

1. **Copy template** — Copies the master Google Doc template (`DD_TEMPLATE_GOOGLE_DOC_ID`) into the site's Drive folder, named `"{site_name} DD Report - {MM/DD/YYYY}"`

2. **Normalize report_data** — The `normalize_report_data()` function from `report_schema.py`:
   - Flattens the nested `report_data` dict into dot-separated keys (e.g., `q1.zoning_designation`)
   - Injects defaults for `meta.site_name` and `meta.report_date`
   - Applies the **alias map** (~43 aliases) to translate known agent key variations to canonical token names (e.g., `site.name` -> `meta.site_name`, `q1.zoning` -> `q1.zoning_designation`)
   - Filters to only keys that match the 108 canonical template tokens
   - Returns diagnostics: how many replacements applied, unmatched agent keys, unfilled tokens

3. **Fill template** — Sends a single `batchUpdate` to the Docs API with one `replaceAllText` request per token

**Template token schema (108 tokens across 7 sections):**

| Section | Count | Examples |
|---------|-------|---------|
| **meta** | 7 | `meta.site_name`, `meta.city_state_zip`, `meta.report_date` |
| **exec_summary** | 5 | `exec_summary.q1_summary`, `exec_summary.acquisition_conditions` |
| **q1** (zoning/permits) | 14 | `q1.zoning_designation`, `q1.permits_required`, `q1.state_school_registration` |
| **q2** (building/E-Occ) | 31 | `q2.year_built`, `q2.sprinklers`, `q2.e_occupancy_score`, `q2.scope_of_work` |
| **q3** (cost) | 24 | `q3.structural_low`, `q3.total_high`, `q3.calculated_budget` |
| **q4** (schedule) | 17 | `q4.acquire_property_date`, `q4.permit_timeline_weeks`, `q4.schedule_risks` |
| **appendix** | 10 | `appendix.sir_link`, `appendix.inspection_link`, `appendix.phase1_esa_link` |

**How skill tool output flows into the report:**

The three skill tools (`apply_e_occupancy_skill`, `apply_school_approval_skill`, `get_cost_estimate`) each return a `report_data_fields` dict with keys that exactly match template tokens. The agent copies these directly into `report_data`:

```
cost_result = get_cost_estimate(total_building_sf=7277, rooms=[...])
for key, value in cost_result["report_data_fields"].items():
    report_data[key] = value  # e.g., report_data["q3.structural_low"] = "58,216"
```

**Alias safety net:**

If the agent sends a key that doesn't exactly match a template token (e.g., `q1.zoning` instead of `q1.zoning_designation`), the alias map in `report_schema.py` catches it and maps it to the correct token. This handles ~43 known variations including:
- `site.*` -> `meta.*` (agent nests under "site")
- `q3.cost_estimate_table.*` -> `q3.*_low` (agent incorrectly nests cost data)
- `q4.milestone_schedule.*` -> `q4.*_date` (agent incorrectly nests milestones)
- `appendix.isp_link` -> `appendix.floorplan_viability_link`

**Output:** Google Doc URL + diagnostics (replacements applied, unmatched keys, unfilled tokens).

---

### Step 9 — Check Completeness

**Tool:** `check_report_completeness(doc_id)`

**Activity:**
1. Exports the generated Google Doc as plain text
2. Scans for two patterns:
   - `{{token}}` — a template placeholder that was never filled (hard block — do not send)
   - `[Not found — ...]` — a sourced gap label where the agent tried but data wasn't available (acceptable)

**Decision:** If any `{{token}}` hard blocks remain, the report is flagged as incomplete. If only `[Not found — ...]` labels remain, the report is ready to send.

---

### Step 10 — Send Email Notification

**Tool:** `send_dd_report_email(site_name, report_url, key_findings)` (interactive mode)
**Direct SMTP** (automated modes)

**Activity:** Sends an HTML email to configured recipients with the site name, confirmation message, and a link to the Google Doc report.

---

## Shared Report Pipeline

**Module:** `src/due_diligence_reporter/report_pipeline.py`

The report pipeline module contains all shared logic used by both the inbox scanner and the daily sweep:

| Function | Purpose |
|----------|---------|
| `TOOL_DEFINITIONS` | 7-tool schema list for Claude API calls |
| `route_tool_call()` / `route_tool_call_sync()` | Async/sync tool router mapping to server.py functions |
| `list_shared_folders_once(gc)` | Pre-fetch SIR/ISP/Inspection shared folder files |
| `match_site_in_shared_cache(terms, cache)` | Find docs for a site in pre-fetched cache |
| `check_site_readiness_direct(gc, url, terms, cache)` | Readiness check bypassing MCP layer |
| `run_dd_report_agent(site_title, prompt)` | Claude agentic loop (up to 40 iterations) |
| `process_site_pipeline(gc, title, url, terms, cache, prompt, settings)` | Full pipeline: readiness -> report -> completeness -> email |
| `post_pipeline_result(webhook_url, result, url)` | Google Chat notification per result |
| `PipelineResult` | Dataclass with status, missing_docs, doc_id, doc_url, etc. |

**Pipeline statuses:** `waiting_on_docs`, `report_exists`, `report_created`, `report_incomplete`, `generation_failed`, `error`

---

## Daily Sweep (Safety Net)

**Script:** `scripts/daily_dd_check.py`
**Schedule:** 9 AM Central, Monday-Friday (GitHub Actions cron: `0 14 * * 1-5` UTC)
**Workflow:** `.github/workflows/daily-dd-check.yml`
**Agent model:** `claude-sonnet-4-6`

**Stage filter:** Only processes sites in these Overall Site Stages:
- `1. Looking for Site`
- `2. Evaluating Potential Site (LOI)`

Sites in later stages (FTO in progress, FTO signed, operational) are skipped.

**Flow per site:**

```
For each Wrike Site Record in active stages with a Drive folder:
  1. Check readiness (SIR + ISP + Inspection present, no report exists)
  2. If missing docs -> post Google Chat alert listing what's missing
  3. If report exists -> skip
  4. If ready -> run Claude agent loop:
     a. get_site_record -> list_drive_documents -> read all 3 docs
     b. apply_e_occupancy_skill + apply_school_approval_skill + get_cost_estimate
     c. create_dd_report (with normalize_report_data)
     d. check_report_completeness
     e. If complete -> send email to recipients
     f. If incomplete -> post Google Chat alert with unfilled tokens
```

**Optimization:** Shared folder file lists are fetched once at the start and reused for all sites, avoiding redundant API calls.

---

## Sourced Gap Labels

The bare word `[Pending]` is banned. When a field can't be filled, the agent uses a sourced gap label that names exactly what was checked:

```
[Not found — {source checked}]
```

Examples:
- `[Not found — SIR did not include AHJ contact]`
- `[Not found — ISP not yet in Drive folder]`
- `[Not found — Phase I ESA not in Drive folder]`
- `[Not found — building inspection did not state year built]`

This tells `check_report_completeness` and human reviewers exactly why each field is empty.

---

## What the Agent Will Not Do

- **Make lease or buy recommendations.** It presents data. The executive team decides.
- **Override skill scores.** E-Occupancy and School Approval scores are authoritative.
- **Fabricate system IDs.** Every Wrike ID, folder ID, and document ID comes from an actual API call.
- **Leave unsourced gap labels.** Every unfilled field names the source that was checked.

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `WRIKE_ACCESS_TOKEN` | Wrike API bearer token |
| `OPENAI_API_KEY` | GPT-5.2 for inbox classification and fuzzy site name matching |
| `ANTHROPIC_API_KEY` | Claude API for automated report generation agent |
| `DD_TEMPLATE_GOOGLE_DOC_ID` | Master DD report template Google Doc ID |
| `GOOGLE_DRIVE_ROOT_FOLDER_ID` | Parent Drive folder containing all site folders |
| `SIR_FOLDER_ID` | Shared SIR folder in Google Drive |
| `ISP_FOLDER_ID` | Shared ISP folder in Google Drive |
| `BUILDING_INSPECTION_FOLDER_ID` | Shared Building Inspection folder |
| `GOOGLE_CLIENT_CONFIG` | Path to OAuth client secrets JSON |
| `GOOGLE_TOKEN_FILE` | Path to saved OAuth token file |
| `PRICING_API_KEY` | Building Optimizer API key |
| `EMAIL_SENDER` | Gmail address for sending reports |
| `EMAIL_APP_PASSWORD` | Gmail App Password for the sender account |
| `DD_REPORT_EMAIL_RECIPIENTS` | Comma-separated recipient email addresses |
| `GOOGLE_CHAT_WEBHOOK_URL` | Google Chat incoming webhook for alerts |

---

## Key Files

| File | What It Is |
|------|-----------|
| `prompt.md` | Agent system prompt — behavior, doc extraction rules, full report data schema |
| `src/due_diligence_reporter/server.py` | MCP server — 10 tools + embedded skill logic |
| `src/due_diligence_reporter/report_pipeline.py` | Shared pipeline — readiness check, Claude agent loop, notifications |
| `src/due_diligence_reporter/report_schema.py` | Template token list (108), alias map (43), `normalize_report_data()` |
| `src/due_diligence_reporter/inbox_scanner.py` | Gmail inbox scan, GPT-5.2 classification, Drive upload |
| `src/due_diligence_reporter/wrike.py` | Wrike API client, site record search, LLM matching |
| `src/due_diligence_reporter/google_client.py` | Google Drive v3 + Docs v1 + Gmail API client (OAuth) |
| `src/due_diligence_reporter/config.py` | Pydantic settings loader |
| `src/due_diligence_reporter/utils.py` | PDF extraction, placeholder builder, email, Google Chat |
| `scripts/daily_dd_check.py` | Daily sweep — stage-filtered readiness check + report pipeline |
| `scripts/scan_inbox.py` | Inbox scan + per-site report pipeline trigger |
| `tests/test_report_pipeline.py` | Pipeline tests (11 tests) |
| `tests/test_inbox_scanner.py` | Inbox scanner tests (16 tests) |
| `tests/test_report_schema.py` | Schema integrity tests (13 tests) |
| `.github/workflows/publish-to-mcp-hive.yml` | CI/CD — push to `main` deploys to MCP Hive |
| `.github/workflows/inbox-scan.yml` | Inbox scan every 15 min, 8 AM-6 PM Central, Mon-Fri |
| `.github/workflows/daily-dd-check.yml` | Daily sweep at 9 AM Central, Mon-Fri |

---

## GitHub Secrets (17 total)

**Publish workflow (9):** `MCP_HIVE_API_KEY`, `MCP_HIVE_ID`, `WRIKE_ACCESS_TOKEN`, `OPENAI_API_KEY`, `DD_TEMPLATE_GOOGLE_DOC_ID`, `GOOGLE_DRIVE_ROOT_FOLDER_ID`, `OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`, `OAUTH_REFRESH_TOKEN`

**Cron + Inbox workflows (8 additional):** `ANTHROPIC_API_KEY`, `GOOGLE_CHAT_WEBHOOK_URL`, `DD_REPORT_EMAIL_RECIPIENTS`, `EMAIL_SENDER`, `EMAIL_APP_PASSWORD`, `SIR_FOLDER_ID`, `ISP_FOLDER_ID`, `BUILDING_INSPECTION_FOLDER_ID`
