# Session Summary — 2026-03-10

**Scope:** Doc detection reliability, MCP Hive OAuth fix, email routing, multi-space notifications
**Commits:** `0c8dab6` → `cce06a4` (7 commits to main)

---

## Issues Found & Resolved

### 1. ISP and Building Inspection skipped during report generation
The interactive agent's prompt (v1.0.0, external system) and the repo's `prompt.md` (v1.1.0) both told the agent that ISP and Building Inspection sections were "pending until those tools are available." This caused the agent to skip reading these documents even when they existed in the shared Drive folders.

**Fix:** Replaced "Pending Sections (by design)" with an explicit 8-step "Report Generation Workflow" in `prompt.md` v1.2.0. The agent now must call `check_site_readiness`, present a found/missing summary, and read ALL found documents before generating.

### 2. Building inspection PDFs in subfolders not found
`_find_site_docs_in_shared_folders()` only searched top-level files. The Building Inspection folder has per-city subfolders (La Jolla, Boca Raton, etc.) that sometimes contain PDF reports alongside inspection photos.

**Fix:** After top-level search, if `building_inspection` not found, the function now searches one level of subfolders for matching PDFs.

### 3. No document discovery summary before report generation
The agent went straight to generating without telling the user what was found or missing.

**Fix:** `check_site_readiness` now returns a multi-line message with filenames. The prompt instructs the agent to present this summary and wait for confirmation before proceeding.

### 4. MCP Hive deploy missing most environment variables
`publish-to-mcp-hive.yml` only shipped 4 of 15+ required env vars in the `.env` file. This meant the deployed MCP server had no access to `PRICING_API_KEY`, shared folder IDs, email config, or Google Chat webhook.

**Fix:** Added all env vars to the publish workflow's `.env` step and secret verification.

### 5. MCP Hive OAuth `invalid_scope` error on all Drive API calls
**Root cause chain:**
1. `publish-to-mcp-hive.yml` declared only 2 OAuth scopes (drive + documents), missing `gmail.modify`
2. MCP Hive's OAuth flow minted a refresh token with limited scopes
3. After adding `OAUTH_REFRESH_TOKEN` to the `.env` file, `setup.sh` still couldn't see it because `.env` was never sourced into the shell environment
4. The `if [ -n "$OAUTH_REFRESH_TOKEN" ]` check always failed, falling through to the broken MCP Hive OAuth path

**Fix (3 iterations):**
- First: Added scopes + `token_uri` to `setup.sh` token JSON — didn't help (MCP Hive token was scope-locked)
- Second: Added `OAUTH_*` vars to `.env` and rewrote `setup.sh` to build token from env vars (same as cron workflows) — didn't help (`.env` not sourced)
- Third: Added `set -a; . ./.env; set +a` to `setup.sh` before the OAuth check — **fixed it**

### 6. DD report emails only went to a static recipient list
`send_dd_report_email` sent to `DD_REPORT_EMAIL_RECIPIENTS` only. The P1 Assignee from Wrike was not included.

**Fix:** Added `get_contact_email()` and `extract_p1_email_from_record()` to `wrike.py` to resolve the Wrike contact ID to an email via the Contacts API. `send_dd_report_email` now accepts `additional_recipients`. `check_site_readiness` returns `p1_assignee_email`. Both the interactive (MCP tool) and automated (cron/inbox) paths now include the P1 Assignee.

### 7. Inbox scan cron window off by 1 hour due to DST
The cron `*/15 13-23 * * 1-5` was set for UTC-5 (CST). After DST started March 9 (UTC-6/CDT), the window shifted to 7 AM–5 PM instead of 8 AM–6 PM.

**Fix:** Widened to `*/15 13-23,0 * * 1-5` to cover both CST and CDT.

### 8. Pipeline notifications only posted to one Google Chat space
`post_pipeline_result` accepted a single webhook URL.

**Fix:** Updated to support comma-separated URLs. Both the existing and new space now receive all pipeline results. `GOOGLE_CHAT_WEBHOOK_URL` GitHub secret updated with both URLs.

---

## Files Modified

| File | Changes |
|---|---|
| `prompt.md` | v1.2.0 — 8-step workflow, removed pending sections, auto-email, P1 recipient |
| `src/due_diligence_reporter/server.py` | Subfolder search, readiness filenames, `p1_assignee_email`, `additional_recipients` on email |
| `src/due_diligence_reporter/wrike.py` | `get_contact_email()`, `extract_p1_email_from_record()` |
| `src/due_diligence_reporter/report_pipeline.py` | `p1_email` param, multi-webhook posting |
| `src/due_diligence_reporter/config.py` | Webhook URL description updated for comma-separated |
| `setup.sh` | Source `.env`, build token from env vars (bypass MCP Hive OAuth) |
| `.github/workflows/publish-to-mcp-hive.yml` | All 15 env vars, `gmail.modify` scope, full secret checks |
| `.github/workflows/inbox-scan.yml` | Widened cron window for DST |
| `scripts/daily_dd_check.py` | Extract and pass P1 email to pipeline |
| `scripts/scan_inbox.py` | Extract and pass P1 email to pipeline |

---

## Lessons Learned

### 1. Shell scripts don't see `.env` files automatically
Python's `dotenv` loads `.env` at runtime, but shell scripts need explicit sourcing (`. ./.env`). Use `set -a` to auto-export when sourcing. This was the final blocker on the OAuth fix — three iterations to find it.

### 2. OAuth refresh tokens are scope-locked at consent time
Adding scopes to config or metadata doesn't retroactively grant them on existing tokens. A new OAuth consent flow is required to mint a refresh token with additional scopes. When possible, bypass platform OAuth flows and use a known-good refresh token directly.

### 3. Keep deploy workflows in sync with cron workflows
The cron workflows had all the right env vars and token construction logic. The publish workflow diverged over time as features were added. When adding a new env var or secret, update ALL workflows that need it — not just the one you're working on.

### 4. Agent prompts must be explicit about workflow steps
Telling an agent that data sources are "pending" causes it to skip them entirely. Explicit numbered steps ("call X, then call Y, then present results") are far more reliable than descriptive guidance. The agent follows literal instructions.

### 5. Test the deployed path, not just local
Local testing worked perfectly because the local OAuth token had all scopes. The deployed MCP Hive environment used a completely different token path that was broken. Always verify the deployed credential flow separately.

### 6. DST breaks hardcoded UTC cron schedules
GitHub Actions crons are UTC-only. When DST changes the offset, the effective local window shifts. Use a wider UTC window that covers both standard and daylight time, or accept a 1-hour shift twice a year.

---

## GitHub Secrets Updated
- `GOOGLE_CHAT_WEBHOOK_URL` — now contains two comma-separated webhook URLs

## Verification Performed
- Local `check_site_readiness("Alpha Houston")` — found all 3 docs with filenames
- Local `check_site_readiness("Alpha Norwalk")` — found all 3 docs with filenames
- Building inspection subfolder test — confirmed 6 subfolders enumerated, La Jolla PDF found
- MCP Hive deploy — `invalid_scope` resolved after `.env` sourcing fix
- Wrike Contacts API — resolved P1 contact ID `KUAWDEOX` to `andrea.ewalefo@trilogy.com`
- All 42 unit tests passing
