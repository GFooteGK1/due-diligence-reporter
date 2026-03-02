# Due Diligence Reporter — Roadmap

## Status: Active Development

Last updated: 03/02/2026

---

## Completed

- [x] Initial MCP server implementation (4 tools: get_site_record, list_drive_documents, read_drive_document, create_dd_report)
- [x] E-Occupancy + School Approval skills embedded in prompt.md
- [x] Google Doc template with {{placeholder}} tokens
- [x] CI/CD deploy to MCP Hive via GitHub Actions
- [x] First report generated: Alpha Brentwood 307 (02/27/2026)
- [x] Quality fixes from first report review (commit 9b0ebf7):
  - Skill → schema field mappings in prompt.md
  - Exec summary must-be-in-report_data rule
  - Q4 milestone formulas (CO/Ready-to-Open = N/A in preliminary)
  - ISP fallback (`[Pending ISP]` when no ISP doc exists)
  - List formatting rule (newline-separated strings)
  - 6 q4.*_confidence fields added to schema
  - utils.py list join `", "` → `"\n"`

---

## In Progress

### 1. Exec Summary Validation
**Problem:** First report had exec_summary fields left as `{{exec_summary.*}}` tokens — agent wrote the summary in chat but didn't include it in report_data before calling create_dd_report.
**Fix applied:** Added IMPORTANT rule to prompt.md.
**Needs:** Run a test report and confirm all 5 exec_summary fields land in the doc.

### 2. Skill Execution Audit
**Problem:** Agent may be acknowledging the embedded skills without running through the scoring logic step by step (approximating rather than computing).
**Suspicion:** Skills are embedded as text in prompt.md — agent could be skipping or summarizing them, especially if context is long.
**Options to investigate:**
- Option A: Keep embedded in prompt.md but add explicit "show your work" instructions before calling create_dd_report
- Option B: Convert each skill to an MCP tool call (`apply_e_occupancy_skill(building_type, stories)` → returns score/zone/tier/timeline) — forces explicit invocation and returns structured output
- Option C: Add a `read_skill_file(skill_name)` tool so the agent reads the skill on demand (lazy load vs. always in context)

### 3. Building Optimizer Integration
**Goal:** Populate Q3 cost estimates programmatically from the Building Optimizer instead of relying on a manual ISP document.
**Unknown:** What form is the Building Optimizer? (MCP server / REST API / Google Sheet / internal tool)
**Next step:** Clarify with user, then design integration approach.

---

## Backlog

- [ ] Template docx: manually add `{{q4.*_confidence}}` tokens to confidence column in Q4 schedule table, re-upload to Drive
- [ ] Renderings section: agent currently skips `renderings_links` — needs guidance on where renderings live in Drive
- [ ] Multi-site batch mode: run DD reports for a list of sites in one agent session
- [ ] LiDAR data extraction: `lidar_summary` and `as_built_links` often left as [TBD]

---

## Known Issues

| Issue | Impact | Fix |
|---|---|---|
| Confidence column in Q4 table has hardcoded "M" not tokens | Confidence values not replaced | Manual docx edit needed (add `{{q4.*_confidence}}` tokens) |
| exec_summary may still land in chat not doc | Report lacks executive summary | Prompt fix deployed; needs validation |
| Skills may be approximated not computed | Inaccurate scores | Under investigation |
