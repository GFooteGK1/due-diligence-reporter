"""DD Report template token schema, agent key aliases, and normalization.

This module is the single source of truth for the 105 {{token}} placeholders
in the Google Doc DD report template.  It also provides an alias map that
translates commonly-observed agent key variations to their canonical template
token, and a ``normalize_report_data`` function used by ``create_dd_report``
to maximise the number of placeholders that get filled.
"""

from __future__ import annotations

import logging
from typing import Any

from .utils import flatten_report_data_for_replacement

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical template tokens (match the {{token}} placeholders in the template)
# ---------------------------------------------------------------------------

TEMPLATE_TOKENS: list[str] = [
    # ── meta ──────────────────────────────────────────────────────────────
    "meta.site_name",
    "meta.city_state_zip",
    "meta.school_type",
    "meta.marketing_name",
    "meta.report_date",
    "meta.prepared_by",
    "meta.drive_folder_url",
    # ── exec_summary ──────────────────────────────────────────────────────
    "exec_summary.q1_summary",
    "exec_summary.q2_summary",
    "exec_summary.q3_summary",
    "exec_summary.q4_summary",
    "exec_summary.acquisition_conditions",
    # ── q1: zoning / permits / school registration ────────────────────────
    "q1.zoning_designation",
    "q1.schools_permitted_as",
    "q1.ahj_building_dept",
    "q1.ahj_fire_dept",
    "q1.ibc_edition",
    "q1.permits_required",
    "q1.pre_app_meeting",
    "q1.health_dept_requirements",
    "q1.rating",
    "q1.state_school_registration",
    "q1.school_approval_type",
    "q1.school_approval_gating",
    "q1.school_approval_timeline_days",
    "q1.steps_to_allow_operation",
    # ── q2: building assessment / E-Occupancy / hazards ───────────────────
    "q2.year_built",
    "q2.construction_type",
    "q2.stories",
    "q2.gba_sf",
    "q2.total_sf",
    "q2.current_use",
    "q2.classroom_count",
    "q2.common_areas",
    "q2.bathrooms",
    "q2.exits",
    "q2.egress",
    "q2.corridor_width",
    "q2.sprinklers",
    "q2.fire_alarm",
    "q2.ada_compliance",
    "q2.scope_of_work",
    "q2.template_match",
    "q2.e_occupancy_score",
    "q2.e_occupancy_zone",
    "q2.e_occupancy_tier",
    "q2.e_occupancy_timeline",
    "q2.e_occupancy_confidence",
    "q2.flood_zone",
    "q2.tornado_zone",
    "q2.seismic_design_category",
    "q2.storm_shelter",
    "q2.historic_district",
    "q2.environmental_contamination",
    "q2.asbestos_lead_risk",
    "q2.ust_database",
    "q2.as_built_links",
    "q2.lidar_summary",
    "q2.renderings_link",
    "q2.floorplan_image",
    # ── q3: cost estimate (23 fields from get_cost_estimate + 1) ──────────
    "q3.structural_low",
    "q3.structural_high",
    "q3.mep_low",
    "q3.mep_high",
    "q3.sprinkler_low",
    "q3.sprinkler_high",
    "q3.fire_alarm_low",
    "q3.fire_alarm_high",
    "q3.ada_low",
    "q3.ada_high",
    "q3.bathrooms_low",
    "q3.bathrooms_high",
    "q3.finish_work_low",
    "q3.finish_work_high",
    "q3.ffe_low",
    "q3.ffe_high",
    "q3.contingency_low",
    "q3.contingency_high",
    "q3.total_low",
    "q3.total_high",
    "q3.calculated_budget",
    "q3.budget_formula",
    "q3.budget_status",
    "q3.key_cost_risks",
    # ── q4: schedule / milestones ─────────────────────────────────────────
    "q4.acquire_property_date",
    "q4.acquire_property_confidence",
    "q4.obtain_permits_date",
    "q4.obtain_permits_confidence",
    "q4.construction_locked_date",
    "q4.construction_locked_confidence",
    "q4.education_regulatory_date",
    "q4.education_regulatory_confidence",
    "q4.co_date",
    "q4.co_confidence",
    "q4.ready_to_open_date",
    "q4.ready_to_open_confidence",
    "q4.permit_timeline_weeks",
    "q4.pre_app_required",
    "q4.schedule_risks",
    "q4.sequential_or_concurrent",
    "q4.opening_target_semester",
    # ── appendix ──────────────────────────────────────────────────────────
    "appendix.sir_link",
    "appendix.inspection_link",
    "appendix.floorplan_viability_link",
    "appendix.phase1_esa_link",
    "appendix.lidar_link",
    "appendix.as_built_link",
    "appendix.pre_app_notes_link",
    "appendix.school_registration_link",
    "appendix.permit_history_link",
    "appendix.other_reports_links",
]

TEMPLATE_TOKEN_SET: frozenset[str] = frozenset(TEMPLATE_TOKENS)

# ---------------------------------------------------------------------------
# Agent key aliases → canonical template token
# ---------------------------------------------------------------------------
# Only semantically correct renames.  If the canonical token already has a
# value in the flattened data the alias is silently skipped (no overwrite).

# ---------------------------------------------------------------------------
# V2 template tokens (executive one-pager — 17+ tokens)
# ---------------------------------------------------------------------------

TEMPLATE_TOKENS_V2: list[str] = [
    # ── meta (same as V1) ─────────────────────────────────────────────────
    "meta.site_name",
    "meta.city_state_zip",
    "meta.school_type",
    "meta.marketing_name",
    "meta.report_date",
    "meta.prepared_by",
    "meta.drive_folder_url",
    # ── exec: "Can we do this?" card (pick-menu dimensions) ───────────────
    "exec.c_answer",
    "exec.c_edreg",
    "exec.c_occupancy",
    "exec.c_zoning",
    # ── exec: cost / capacity / timeline grid (bare values) ───────────────
    "exec.e_mvp_capacity",
    "exec.e_ideal_capacity",
    "exec.e_mvp_cost",
    "exec.e_ideal_cost",
    "exec.f_mvp_ready",
    "exec.f_ideal_ready",
    # ── exec: delta column (server-computed, NOT agent-filled) ──────────
    "exec.delta_capacity",
    "exec.delta_cost",
    "exec.delta_ready",
    # ── exec: conditions ──────────────────────────────────────────────────
    "exec.acquisition_conditions",
    # ── sources (document links — 5 rows) ─────────────────────────────────
    "sources.sir_link",
    "sources.inspection_link",
    "sources.isp_link",
    "sources.e_occupancy_link",
    "sources.school_approval_link",
]

TEMPLATE_TOKEN_V2_SET: frozenset[str] = frozenset(TEMPLATE_TOKENS_V2)

# ---------------------------------------------------------------------------
# Link tokens — values that should render as clickable hyperlinks in the doc
# ---------------------------------------------------------------------------

LINK_TOKENS_V1: frozenset[str] = frozenset({
    "appendix.sir_link",
    "appendix.inspection_link",
    "appendix.floorplan_viability_link",
    "appendix.phase1_esa_link",
    "appendix.lidar_link",
    "appendix.as_built_link",
    "appendix.pre_app_notes_link",
    "appendix.school_registration_link",
    "appendix.permit_history_link",
    "appendix.other_reports_links",
    "q2.renderings_link",
    "meta.drive_folder_url",
})

LINK_TOKENS_V2: frozenset[str] = frozenset({
    "sources.sir_link",
    "sources.inspection_link",
    "sources.isp_link",
    "sources.e_occupancy_link",
    "sources.school_approval_link",
    "meta.drive_folder_url",
})

# Display labels for link tokens — shown in the doc instead of raw URLs.
# Tokens not listed here fall back to the raw URL as display text.

LINK_DISPLAY_LABELS_V1: dict[str, str] = {
    "appendix.sir_link":                "View SIR",
    "appendix.inspection_link":         "View Inspection",
    "appendix.floorplan_viability_link": "View ISP",
    "appendix.phase1_esa_link":         "View Phase I ESA",
    "appendix.lidar_link":              "View LiDAR",
    "appendix.as_built_link":           "View As-Built",
    "appendix.pre_app_notes_link":      "View Pre-App Notes",
    "appendix.school_registration_link": "View School Registration",
    "appendix.permit_history_link":     "View Permit History",
    "appendix.other_reports_links":     "View Other Reports",
    "q2.renderings_link":              "View Renderings",
    "meta.drive_folder_url":           "View Site Folder",
}

LINK_DISPLAY_LABELS_V2: dict[str, str] = {
    "sources.sir_link":             "View SIR",
    "sources.inspection_link":      "View Inspection",
    "sources.isp_link":             "View ISP",
    "sources.e_occupancy_link":     "View E-Occupancy",
    "sources.school_approval_link": "View School Approval",
    "meta.drive_folder_url":        "View Site Folder",
}

# V2 aliases — map V1-style agent keys to V2 canonical tokens
AGENT_KEY_ALIASES_V2: dict[str, str] = {
    # ── top-level / site.* → meta.* ──────────────────────────────────────
    "site_name":            "meta.site_name",
    "report_date":          "meta.report_date",
    "doc_url":              "meta.drive_folder_url",
    "site.name":            "meta.site_name",
    "site.city_state_zip":  "meta.city_state_zip",
    "site.address":         "meta.city_state_zip",
    "site.school_type":     "meta.school_type",
    "site.marketing_name":  "meta.marketing_name",
    "site.prepared_by":     "meta.prepared_by",
    # ── exec_summary.* → exec.* (V1 agent output → V2 tokens) ───────────
    "exec_summary.acquisition_conditions": "exec.acquisition_conditions",
    # ── backward compat: old token names → new names ────────────────────
    "exec.c_permitting":                  "exec.c_edreg",
    "exec.f_ready_mm_yy":                 "exec.f_mvp_ready",
    # ── typo alias ────────────────────────────────────────────────────────
    "exec.e_ideal_capcity":               "exec.e_ideal_capacity",
    # ── appendix.* → sources.* ───────────────────────────────────────────
    "appendix.sir_link":                   "sources.sir_link",
    "appendix.inspection_link":            "sources.inspection_link",
    "appendix.building_inspection_link":   "sources.inspection_link",
    "appendix.floorplan_viability_link":   "sources.isp_link",
    "appendix.isp_link":                   "sources.isp_link",
}

# ---------------------------------------------------------------------------
# V1 Agent key aliases → canonical template token
# ---------------------------------------------------------------------------

AGENT_KEY_ALIASES: dict[str, str] = {
    # ── top-level convenience keys → meta ─────────────────────────────────
    "site_name":            "meta.site_name",
    "report_date":          "meta.report_date",
    "doc_url":              "meta.drive_folder_url",
    # ── site.* → meta.* (agent nests under "site") ───────────────────────
    "site.name":            "meta.site_name",
    "site.city_state_zip":  "meta.city_state_zip",
    "site.address":         "meta.city_state_zip",
    "site.school_type":     "meta.school_type",
    "site.marketing_name":  "meta.marketing_name",
    "site.prepared_by":     "meta.prepared_by",
    # ── Q1 variations ────────────────────────────────────────────────────
    "q1.zoning":                  "q1.zoning_designation",
    "q1.ahj_contact":             "q1.ahj_building_dept",
    "q1.ahj":                     "q1.ahj_building_dept",
    "q1.permit_timeline":         "q4.permit_timeline_weeks",
    # ── Q2 variations ────────────────────────────────────────────────────
    "q2.building_overview":           "q2.scope_of_work",
    "q2.building_condition_summary":  "q2.scope_of_work",
    "q2.scope_of_work_summary":      "q2.scope_of_work",
    "q2.floorplan_viability":         "q2.template_match",
    "q2.ada_score":                   "q2.ada_compliance",
    "q2.matterport_link":             "q2.lidar_summary",
    "q2.e_occupancy":                 "q2.e_occupancy_score",
    "q2.square_footage":              "q2.gba_sf",
    "q2.sf":                          "q2.gba_sf",
    "q2.mep_condition":               "q2.fire_alarm",
    "q2.structural_condition":        "q2.construction_type",
    # ── Q3 nested variations (agent sends q3.cost_estimate_table.*) ──────
    "q3.cost_estimate_table.structural":  "q3.structural_low",
    "q3.cost_estimate_table.mep":         "q3.mep_low",
    "q3.cost_estimate_table.sprinkler":   "q3.sprinkler_low",
    "q3.cost_estimate_table.fire_alarm":  "q3.fire_alarm_low",
    "q3.cost_estimate_table.ada":         "q3.ada_low",
    "q3.cost_estimate_table.bathrooms":   "q3.bathrooms_low",
    "q3.cost_estimate_table.finish_work": "q3.finish_work_low",
    "q3.cost_estimate_table.ffe":         "q3.ffe_low",
    "q3.cost_estimate_table.contingency": "q3.contingency_low",
    "q3.cost_estimate_table.total":       "q3.total_low",
    "q3.cost_risks":                      "q3.key_cost_risks",
    # ── Q4 nested variations (agent sends q4.milestone_schedule.*) ───────
    "q4.milestone_schedule.acquire":              "q4.acquire_property_date",
    "q4.milestone_schedule.permits":              "q4.obtain_permits_date",
    "q4.milestone_schedule.construction_lock":    "q4.construction_locked_date",
    "q4.milestone_schedule.regulatory_approval":  "q4.education_regulatory_date",
    "q4.milestone_schedule.co":                   "q4.co_date",
    "q4.milestone_schedule.ready_to_open":        "q4.ready_to_open_date",
    "q4.timeline":                                "q4.permit_timeline_weeks",
    # ── appendix variations ──────────────────────────────────────────────
    "appendix.building_inspection_link":  "appendix.inspection_link",
    "appendix.isp_link":                  "appendix.floorplan_viability_link",
    "appendix.matterport_link":           "appendix.lidar_link",
    "appendix.phase_i_esa_link":          "appendix.phase1_esa_link",
    "appendix.drive_folder_link":         "meta.drive_folder_url",
    "appendix.site_folder_link":          "meta.drive_folder_url",
    # ── Q2 renderings / M1 folder alias ──────────────────────────────────
    "q2.m1_folder_link":                  "q2.renderings_link",
}


# ---------------------------------------------------------------------------
# normalize_report_data
# ---------------------------------------------------------------------------

def normalize_report_data(
    report_data: dict[str, Any],
    site_name: str,
    report_date: str,
) -> tuple[dict[str, str], list[str], list[str]]:
    """Normalize agent output into template-ready replacements.

    Steps:
        1. Flatten the nested ``report_data`` dict using dot-separated keys.
        2. Inject ``meta.site_name`` and ``meta.report_date`` defaults.
        3. Apply :data:`AGENT_KEY_ALIASES` — only when the canonical token
           does *not* already have a value (no overwrite).
        4. Filter to keys present in :data:`TEMPLATE_TOKEN_SET`.

    Returns:
        ``(replacements, unmatched_keys, unfilled_tokens)``

        - *replacements* — ``{token: value}`` ready for ``replaceAllText``.
        - *unmatched_keys* — agent keys that matched no template token (even
          after aliasing).  Useful for diagnostics / future alias expansion.
        - *unfilled_tokens* — template tokens that received no value.
    """
    # 1. Flatten
    flat = flatten_report_data_for_replacement(report_data)

    # 2. Inject defaults (only if not already present)
    flat.setdefault("meta.site_name", site_name.strip())
    flat.setdefault("meta.report_date", report_date)

    # 3. Apply aliases (skip when canonical already has a value)
    for alias, canonical in AGENT_KEY_ALIASES.items():
        if alias in flat and canonical not in flat:
            flat[canonical] = flat[alias]

    # 4. Filter → only template tokens
    replacements: dict[str, str] = {}
    unmatched_keys: list[str] = []

    for key, value in flat.items():
        if key in TEMPLATE_TOKEN_SET:
            replacements[key] = value
        else:
            unmatched_keys.append(key)

    unfilled_tokens = [t for t in TEMPLATE_TOKENS if t not in replacements]

    logger.info(
        "normalize_report_data: %d replacements, %d unmatched agent keys, %d unfilled tokens",
        len(replacements),
        len(unmatched_keys),
        len(unfilled_tokens),
    )
    if unmatched_keys:
        logger.info("Unmatched agent keys: %s", sorted(unmatched_keys))
    if unfilled_tokens:
        logger.debug("Unfilled template tokens: %s", sorted(unfilled_tokens))

    return replacements, sorted(unmatched_keys), sorted(unfilled_tokens)


def normalize_report_data_v2(
    report_data: dict[str, Any],
    site_name: str,
    report_date: str,
) -> tuple[dict[str, str], list[str], list[str], dict[str, str]]:
    """Normalize agent output into V2 template-ready replacements.

    Same logic as :func:`normalize_report_data` but uses the V2 token set
    and V2 alias map (executive one-pager format).

    Returns:
        ``(replacements, unmatched_keys, unfilled_tokens, token_sources)``

        - *token_sources* — ``{token: source}`` for every V2 template token.
          Source values: ``"agent"``, ``"alias:{original_key}"``,
          ``"default"``, or ``"unfilled"``.  Deltas are marked ``"computed"``
          by the caller after :func:`compute_v2_deltas`.
    """
    # 1. Flatten
    flat = flatten_report_data_for_replacement(report_data)
    token_sources: dict[str, str] = {}

    # Track which V2 tokens the agent provided directly
    for key in flat:
        if key in TEMPLATE_TOKEN_V2_SET:
            token_sources[key] = "agent"

    # 2. Inject defaults (only if agent didn't provide them)
    if "meta.site_name" not in flat:
        flat["meta.site_name"] = site_name.strip()
        token_sources["meta.site_name"] = "default"
    if "meta.report_date" not in flat:
        flat["meta.report_date"] = report_date
        token_sources["meta.report_date"] = "default"

    # 3. Apply V2 aliases
    for alias, canonical in AGENT_KEY_ALIASES_V2.items():
        if alias in flat and canonical not in flat:
            flat[canonical] = flat[alias]
            if canonical in TEMPLATE_TOKEN_V2_SET:
                token_sources[canonical] = f"alias:{alias}"

    # 4. Filter → only V2 template tokens
    replacements: dict[str, str] = {}
    unmatched_keys: list[str] = []

    for key, value in flat.items():
        if key in TEMPLATE_TOKEN_V2_SET:
            replacements[key] = value
        else:
            unmatched_keys.append(key)

    unfilled_tokens = [t for t in TEMPLATE_TOKENS_V2 if t not in replacements]

    # Mark unfilled tokens in sources
    for token in unfilled_tokens:
        token_sources[token] = "unfilled"

    logger.info(
        "normalize_report_data_v2: %d replacements, %d unmatched, %d unfilled",
        len(replacements),
        len(unmatched_keys),
        len(unfilled_tokens),
    )
    if unmatched_keys:
        logger.info("Unmatched agent keys (V2): %s", sorted(unmatched_keys))
    if unfilled_tokens:
        logger.debug("Unfilled V2 tokens: %s", sorted(unfilled_tokens))

    return replacements, sorted(unmatched_keys), sorted(unfilled_tokens), token_sources


# ---------------------------------------------------------------------------
# V2 delta computation (server-side, not agent-filled)
# ---------------------------------------------------------------------------


def _parse_dollar(value: str) -> int | None:
    """Parse a dollar string like '$185,000' into an integer (185000)."""
    cleaned = value.replace("$", "").replace(",", "").strip()
    try:
        return int(cleaned)
    except ValueError:
        try:
            return int(float(cleaned))
        except ValueError:
            return None


def _format_dollar(amount: int) -> str:
    """Format an integer as a dollar string like '$105,000'."""
    if amount < 0:
        return f"-${abs(amount):,}"
    return f"${amount:,}"


def _parse_mm_yy(value: str) -> tuple[int, int] | None:
    """Parse 'MM/YY' into (month, year_2digit). Returns None on failure."""
    parts = value.strip().split("/")
    if len(parts) != 2:
        return None
    try:
        month, year = int(parts[0]), int(parts[1])
        if 1 <= month <= 12 and 0 <= year <= 99:
            return month, year
        return None
    except ValueError:
        return None


def _month_diff(mvp: tuple[int, int], ideal: tuple[int, int]) -> int:
    """Compute month difference (ideal - mvp) from (month, year_2digit) tuples."""
    mvp_total = mvp[1] * 12 + mvp[0]
    ideal_total = ideal[1] * 12 + ideal[0]
    return ideal_total - mvp_total


def compute_v2_deltas(replacements: dict[str, str]) -> None:
    """Compute delta column values from MVP/Ideal pairs and inject into *replacements*.

    Computes:
        - ``exec.delta_capacity`` = ideal_capacity - mvp_capacity (integer)
        - ``exec.delta_cost`` = ideal_cost - mvp_cost (dollar formatted)
        - ``exec.delta_ready`` = ideal_ready - mvp_ready (e.g. "+3 mo")

    Only sets each delta if both source values are present and parseable.
    Existing values are not overwritten.
    """
    # Capacity delta
    mvp_cap = replacements.get("exec.e_mvp_capacity", "").strip()
    ideal_cap = replacements.get("exec.e_ideal_capacity", "").strip()
    if mvp_cap and ideal_cap and "exec.delta_capacity" not in replacements:
        try:
            delta = int(ideal_cap) - int(mvp_cap)
            sign = "+" if delta > 0 else ""
            replacements["exec.delta_capacity"] = f"{sign}{delta}"
        except ValueError:
            logger.debug("Could not compute capacity delta: mvp=%s, ideal=%s", mvp_cap, ideal_cap)

    # Cost delta
    mvp_cost = replacements.get("exec.e_mvp_cost", "").strip()
    ideal_cost = replacements.get("exec.e_ideal_cost", "").strip()
    if mvp_cost and ideal_cost and "exec.delta_cost" not in replacements:
        mvp_val = _parse_dollar(mvp_cost)
        ideal_val = _parse_dollar(ideal_cost)
        if mvp_val is not None and ideal_val is not None:
            delta = ideal_val - mvp_val
            sign = "+" if delta > 0 else ""
            replacements["exec.delta_cost"] = f"{sign}{_format_dollar(delta)}"
        else:
            logger.debug("Could not parse cost values: mvp=%s, ideal=%s", mvp_cost, ideal_cost)

    # Timeline delta
    mvp_ready = replacements.get("exec.f_mvp_ready", "").strip()
    ideal_ready = replacements.get("exec.f_ideal_ready", "").strip()
    if mvp_ready and ideal_ready and "exec.delta_ready" not in replacements:
        mvp_parsed = _parse_mm_yy(mvp_ready)
        ideal_parsed = _parse_mm_yy(ideal_ready)
        if mvp_parsed and ideal_parsed:
            diff = _month_diff(mvp_parsed, ideal_parsed)
            sign = "+" if diff > 0 else ""
            replacements["exec.delta_ready"] = f"{sign}{diff} mo"
        else:
            logger.debug("Could not parse timeline values: mvp=%s, ideal=%s", mvp_ready, ideal_ready)
