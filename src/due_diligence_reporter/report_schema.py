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
