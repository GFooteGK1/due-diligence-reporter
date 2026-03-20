"""Tests for the DD report template schema, alias map, and normalization."""

from __future__ import annotations

import pytest

from due_diligence_reporter.report_schema import (
    AGENT_KEY_ALIASES,
    AGENT_KEY_ALIASES_V2,
    LINK_TOKENS_V1,
    LINK_TOKENS_V2,
    TEMPLATE_TOKEN_SET,
    TEMPLATE_TOKEN_V2_SET,
    TEMPLATE_TOKENS,
    TEMPLATE_TOKENS_V2,
    compute_v2_deltas,
    normalize_report_data,
    normalize_report_data_v2,
)


# ---------------------------------------------------------------------------
# Schema integrity
# ---------------------------------------------------------------------------

def test_no_duplicate_tokens():
    """Every token in TEMPLATE_TOKENS must appear exactly once."""
    seen: set[str] = set()
    dupes: list[str] = []
    for token in TEMPLATE_TOKENS:
        if token in seen:
            dupes.append(token)
        seen.add(token)
    assert dupes == [], f"Duplicate template tokens: {dupes}"


def test_set_matches_list():
    """TEMPLATE_TOKEN_SET must contain exactly the same items as TEMPLATE_TOKENS."""
    assert TEMPLATE_TOKEN_SET == frozenset(TEMPLATE_TOKENS)


def test_all_aliases_point_to_valid_tokens():
    """Every alias target must exist in TEMPLATE_TOKEN_SET."""
    bad = {
        alias: target
        for alias, target in AGENT_KEY_ALIASES.items()
        if target not in TEMPLATE_TOKEN_SET
    }
    assert bad == {}, f"Aliases pointing to invalid tokens: {bad}"


def test_no_alias_is_also_a_template_token():
    """An alias key should not itself be a canonical template token.

    If it were, the alias would never fire (the key already matches directly).
    This isn't a hard error, just a sign the alias is pointless.
    """
    overlap = {k for k in AGENT_KEY_ALIASES if k in TEMPLATE_TOKEN_SET}
    assert overlap == set(), f"Alias keys that are also template tokens (pointless): {overlap}"


# ---------------------------------------------------------------------------
# normalize_report_data — direct match
# ---------------------------------------------------------------------------

def test_normalize_direct_match():
    """Keys that exactly match template tokens pass through unchanged."""
    report_data = {
        "meta": {"site_name": "Alpha Keller"},
        "q1": {"zoning_designation": "C-2 Commercial"},
        "q3": {"structural_low": "24,000"},
    }
    replacements, unmatched, unfilled = normalize_report_data(
        report_data, site_name="Alpha Keller", report_date="03/03/2026",
    )

    assert replacements["meta.site_name"] == "Alpha Keller"
    assert replacements["q1.zoning_designation"] == "C-2 Commercial"
    assert replacements["q3.structural_low"] == "24,000"
    assert replacements["meta.report_date"] == "03/03/2026"


def test_normalize_alias():
    """Known agent key variations are aliased to canonical template tokens."""
    report_data = {
        "site": {"name": "Alpha Austin"},
        "q1": {"zoning": "MU-1"},
        "q2": {"floorplan_viability": "80/100"},
    }
    replacements, unmatched, unfilled = normalize_report_data(
        report_data, site_name="Alpha Austin", report_date="03/03/2026",
    )

    assert replacements.get("meta.site_name") == "Alpha Austin"
    assert replacements.get("q1.zoning_designation") == "MU-1"
    assert replacements.get("q2.template_match") == "80/100"


def test_alias_does_not_overwrite_canonical():
    """If both the alias key and canonical key are present, canonical wins."""
    report_data = {
        "q1": {
            "zoning": "WRONG — alias version",
            "zoning_designation": "C-3 Correct",
        },
    }
    replacements, _, _ = normalize_report_data(
        report_data, site_name="Test", report_date="01/01/2026",
    )

    assert replacements["q1.zoning_designation"] == "C-3 Correct"


def test_unmatched_keys_reported():
    """Keys that match no template token and no alias appear in unmatched."""
    report_data = {
        "q99": {"made_up_field": "bogus"},
    }
    _, unmatched, _ = normalize_report_data(
        report_data, site_name="Test", report_date="01/01/2026",
    )

    assert "q99.made_up_field" in unmatched


def test_unfilled_tokens_reported():
    """Template tokens that receive no value appear in unfilled."""
    report_data = {}  # empty — everything unfilled
    _, _, unfilled = normalize_report_data(
        report_data, site_name="Test", report_date="01/01/2026",
    )

    # meta.site_name and meta.report_date are injected automatically
    assert "meta.site_name" not in unfilled
    assert "meta.report_date" not in unfilled
    # But everything else should be unfilled
    assert "q1.zoning_designation" in unfilled
    assert "q3.structural_low" in unfilled


# ---------------------------------------------------------------------------
# Cost estimate fields match template
# ---------------------------------------------------------------------------

# These are the 23 keys returned by get_cost_estimate's report_data_fields.
COST_ESTIMATE_FIELDS = [
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
]


def test_cost_estimate_fields_match_template():
    """All 24 keys from get_cost_estimate.report_data_fields must be valid template tokens."""
    missing = [f for f in COST_ESTIMATE_FIELDS if f not in TEMPLATE_TOKEN_SET]
    assert missing == [], f"Cost estimate fields not in template: {missing}"


# E-Occupancy skill fields
E_OCCUPANCY_FIELDS = [
    "q2.e_occupancy_score",
    "q2.e_occupancy_zone",
    "q2.e_occupancy_tier",
    "q2.e_occupancy_timeline",
    "q2.e_occupancy_confidence",
]


def test_e_occupancy_fields_match_template():
    """All E-Occupancy skill report_data_fields must be valid template tokens."""
    missing = [f for f in E_OCCUPANCY_FIELDS if f not in TEMPLATE_TOKEN_SET]
    assert missing == [], f"E-Occupancy fields not in template: {missing}"


# School approval skill fields
SCHOOL_APPROVAL_FIELDS = [
    "q1.state_school_registration",
    "q1.school_approval_type",
    "q1.school_approval_gating",
    "q1.school_approval_timeline_days",
    "q1.steps_to_allow_operation",
]


def test_school_approval_fields_match_template():
    """All School Approval skill report_data_fields must be valid template tokens."""
    missing = [f for f in SCHOOL_APPROVAL_FIELDS if f not in TEMPLATE_TOKEN_SET]
    assert missing == [], f"School approval fields not in template: {missing}"


# ---------------------------------------------------------------------------
# Nested Q4 milestone alias scenario
# ---------------------------------------------------------------------------

def test_q4_milestone_schedule_aliases():
    """Agent-sent q4.milestone_schedule.* keys alias to q4.*_date tokens."""
    report_data = {
        "q4": {
            "milestone_schedule": {
                "acquire": "2026-04-15",
                "permits": "2026-06-01",
                "construction_lock": "2026-08-01",
                "co": "2026-12-01",
                "ready_to_open": "2027-01-15",
            },
        },
    }
    replacements, _, _ = normalize_report_data(
        report_data, site_name="Test", report_date="01/01/2026",
    )

    assert replacements.get("q4.acquire_property_date") == "2026-04-15"
    assert replacements.get("q4.obtain_permits_date") == "2026-06-01"
    assert replacements.get("q4.construction_locked_date") == "2026-08-01"
    assert replacements.get("q4.co_date") == "2026-12-01"
    assert replacements.get("q4.ready_to_open_date") == "2027-01-15"


# ===========================================================================
# V2 Report Schema Tests
# ===========================================================================


class TestV2SchemaIntegrity:
    """Schema integrity tests for V2 template tokens and aliases."""

    def test_v2_no_duplicate_tokens(self):
        """Every token in TEMPLATE_TOKENS_V2 must appear exactly once."""
        seen: set[str] = set()
        dupes: list[str] = []
        for token in TEMPLATE_TOKENS_V2:
            if token in seen:
                dupes.append(token)
            seen.add(token)
        assert dupes == [], f"Duplicate V2 template tokens: {dupes}"

    def test_v2_set_matches_list(self):
        """TEMPLATE_TOKEN_V2_SET must contain exactly the same items as TEMPLATE_TOKENS_V2."""
        assert TEMPLATE_TOKEN_V2_SET == frozenset(TEMPLATE_TOKENS_V2)

    def test_v2_all_aliases_point_to_valid_tokens(self):
        """Every V2 alias target must exist in TEMPLATE_TOKEN_V2_SET."""
        bad = {
            alias: target
            for alias, target in AGENT_KEY_ALIASES_V2.items()
            if target not in TEMPLATE_TOKEN_V2_SET
        }
        assert bad == {}, f"V2 aliases pointing to invalid tokens: {bad}"

    def test_v2_no_alias_is_also_a_token(self):
        """A V2 alias key should not itself be a canonical V2 template token."""
        overlap = {k for k in AGENT_KEY_ALIASES_V2 if k in TEMPLATE_TOKEN_V2_SET}
        assert overlap == set(), f"V2 alias keys that are also template tokens: {overlap}"

    def test_v2_token_count(self):
        """V2 template has exactly 26 tokens — guards accidental additions/removals."""
        assert len(TEMPLATE_TOKENS_V2) == 26, (
            f"Expected 26 V2 tokens, got {len(TEMPLATE_TOKENS_V2)}"
        )


class TestV2Normalization:
    """Normalization tests for V2 report data."""

    def test_v2_normalize_direct_match(self):
        """exec.*, sources.*, meta.* keys pass through unchanged."""
        report_data = {
            "exec": {
                "c_answer": "YES",
                "e_mvp_capacity": "36",
                "e_ideal_capacity": "54",
                "e_mvp_cost": "$185,000",
                "e_ideal_cost": "$290,000",
                "f_mvp_ready": "01/27",
                "f_ideal_ready": "04/27",
            },
            "sources": {
                "sir_link": "https://example.com/sir",
                "e_occupancy_link": "https://example.com/eocc",
            },
            "meta": {"site_name": "Alpha Test"},
        }
        replacements, unmatched, unfilled, sources = normalize_report_data_v2(
            report_data, site_name="Alpha Test", report_date="03/19/2026",
        )
        assert replacements["exec.c_answer"] == "YES"
        assert replacements["exec.e_mvp_capacity"] == "36"
        assert replacements["exec.e_ideal_capacity"] == "54"
        assert replacements["exec.e_mvp_cost"] == "$185,000"
        assert replacements["exec.e_ideal_cost"] == "$290,000"
        assert replacements["exec.f_mvp_ready"] == "01/27"
        assert replacements["exec.f_ideal_ready"] == "04/27"
        assert replacements["sources.sir_link"] == "https://example.com/sir"
        assert replacements["sources.e_occupancy_link"] == "https://example.com/eocc"
        assert replacements["meta.site_name"] == "Alpha Test"

    def test_v2_normalize_alias(self):
        """V2 aliases resolve correctly (appendix.* → sources.*, etc.)."""
        report_data = {
            "appendix": {
                "sir_link": "https://example.com/sir",
                "inspection_link": "https://example.com/insp",
                "isp_link": "https://example.com/isp",
            },
        }
        replacements, _, _, _ = normalize_report_data_v2(
            report_data, site_name="Test", report_date="01/01/2026",
        )
        assert replacements.get("sources.sir_link") == "https://example.com/sir"
        assert replacements.get("sources.inspection_link") == "https://example.com/insp"
        assert replacements.get("sources.isp_link") == "https://example.com/isp"

    def test_v2_alias_no_overwrite(self):
        """Canonical V2 token wins when both alias and canonical are present."""
        report_data = {
            "sources": {"sir_link": "CANONICAL"},
            "appendix": {"sir_link": "ALIAS"},
        }
        replacements, _, _, _ = normalize_report_data_v2(
            report_data, site_name="Test", report_date="01/01/2026",
        )
        assert replacements["sources.sir_link"] == "CANONICAL"

    def test_v2_unmatched_v1_keys(self):
        """V1-only keys (q1.*, q3.*) appear in the unmatched list for V2."""
        report_data = {
            "q1": {"zoning_designation": "C-2"},
            "q3": {"structural_low": "24,000"},
        }
        _, unmatched, _, _ = normalize_report_data_v2(
            report_data, site_name="Test", report_date="01/01/2026",
        )
        assert "q1.zoning_designation" in unmatched
        assert "q3.structural_low" in unmatched

    def test_v2_unfilled_tokens(self):
        """Empty data → all V2 tokens minus defaults are unfilled."""
        _, _, unfilled, _ = normalize_report_data_v2(
            {}, site_name="Test", report_date="01/01/2026",
        )
        # meta.site_name and meta.report_date are auto-injected
        assert "meta.site_name" not in unfilled
        assert "meta.report_date" not in unfilled
        # Everything else should be unfilled
        assert "exec.c_answer" in unfilled
        assert "exec.e_mvp_capacity" in unfilled
        assert "exec.f_mvp_ready" in unfilled
        assert "exec.delta_capacity" in unfilled
        assert "exec.delta_cost" in unfilled
        assert "exec.delta_ready" in unfilled
        assert "sources.sir_link" in unfilled
        assert "sources.e_occupancy_link" in unfilled
        assert "sources.school_approval_link" in unfilled

    def test_v2_meta_defaults(self):
        """site_name and report_date are auto-injected into V2 replacements."""
        replacements, _, _, _ = normalize_report_data_v2(
            {}, site_name="Alpha Metro", report_date="03/19/2026",
        )
        assert replacements["meta.site_name"] == "Alpha Metro"
        assert replacements["meta.report_date"] == "03/19/2026"

    def test_v2_pick_menu_tokens_pass_through(self):
        """Pick-menu dimension tokens (c_zoning, c_edreg, c_occupancy) pass through."""
        report_data = {
            "exec": {
                "c_zoning": "Permitted by right",
                "c_edreg": "Not required",
                "c_occupancy": "Has E-Occupancy",
            },
        }
        replacements, _, _, _ = normalize_report_data_v2(
            report_data, site_name="Test", report_date="01/01/2026",
        )
        assert replacements["exec.c_zoning"] == "Permitted by right"
        assert replacements["exec.c_edreg"] == "Not required"
        assert replacements["exec.c_occupancy"] == "Has E-Occupancy"

    def test_v2_backward_compat_timeline_alias(self):
        """Old exec.f_ready_mm_yy aliases to exec.f_mvp_ready."""
        report_data = {
            "exec": {"f_ready_mm_yy": "09/27"},
        }
        replacements, _, _, _ = normalize_report_data_v2(
            report_data, site_name="Test", report_date="01/01/2026",
        )
        assert replacements.get("exec.f_mvp_ready") == "09/27"

    def test_v2_typo_alias_ideal_capacity(self):
        """Typo alias exec.e_ideal_capcity → exec.e_ideal_capacity."""
        report_data = {
            "exec": {"e_ideal_capcity": "54"},
        }
        replacements, _, _, _ = normalize_report_data_v2(
            report_data, site_name="Test", report_date="01/01/2026",
        )
        assert replacements.get("exec.e_ideal_capacity") == "54"

    def test_v2_token_sources(self):
        """token_sources tracks where each token value came from."""
        report_data = {
            "exec": {
                "c_answer": "YES",
                "c_zoning": "Permitted",
                "f_ready_mm_yy": "09/27",  # alias → exec.f_mvp_ready
            },
            "appendix": {
                "sir_link": "https://example.com/sir",  # alias → sources.sir_link
            },
        }
        _, _, _, sources = normalize_report_data_v2(
            report_data, site_name="Test Site", report_date="03/20/2026",
        )
        # Agent direct
        assert sources["exec.c_answer"] == "agent"
        assert sources["exec.c_zoning"] == "agent"
        # Defaults
        assert sources["meta.site_name"] == "default"
        assert sources["meta.report_date"] == "default"
        # Aliases
        assert sources["exec.f_mvp_ready"] == "alias:exec.f_ready_mm_yy"
        assert sources["sources.sir_link"] == "alias:appendix.sir_link"
        # Unfilled
        assert sources["exec.e_mvp_capacity"] == "unfilled"
        assert sources["exec.delta_capacity"] == "unfilled"


class TestV2DeltaComputation:
    """Tests for server-computed delta column values."""

    def test_all_deltas_computed(self):
        """Given valid MVP and Ideal values, all 3 deltas are computed."""
        replacements = {
            "exec.e_mvp_capacity": "36",
            "exec.e_ideal_capacity": "54",
            "exec.e_mvp_cost": "$185,000",
            "exec.e_ideal_cost": "$290,000",
            "exec.f_mvp_ready": "01/27",
            "exec.f_ideal_ready": "04/27",
        }
        compute_v2_deltas(replacements)

        assert replacements["exec.delta_capacity"] == "+18"
        assert replacements["exec.delta_cost"] == "+$105,000"
        assert replacements["exec.delta_ready"] == "+3 mo"

    def test_zero_delta(self):
        """When MVP and Ideal are equal, deltas show zero."""
        replacements = {
            "exec.e_mvp_capacity": "36",
            "exec.e_ideal_capacity": "36",
            "exec.e_mvp_cost": "$185,000",
            "exec.e_ideal_cost": "$185,000",
            "exec.f_mvp_ready": "01/27",
            "exec.f_ideal_ready": "01/27",
        }
        compute_v2_deltas(replacements)

        assert replacements["exec.delta_capacity"] == "0"
        assert replacements["exec.delta_cost"] == "$0"
        assert replacements["exec.delta_ready"] == "0 mo"

    def test_negative_cost_delta(self):
        """If ideal is cheaper than MVP (unlikely but possible), delta is negative."""
        replacements = {
            "exec.e_mvp_cost": "$290,000",
            "exec.e_ideal_cost": "$185,000",
        }
        compute_v2_deltas(replacements)

        assert replacements["exec.delta_cost"] == "-$105,000"

    def test_missing_values_no_delta(self):
        """If one side is missing, delta is not injected."""
        replacements = {
            "exec.e_mvp_capacity": "36",
            # ideal_capacity missing
            "exec.e_mvp_cost": "$185,000",
            # ideal_cost missing
        }
        compute_v2_deltas(replacements)

        assert "exec.delta_capacity" not in replacements
        assert "exec.delta_cost" not in replacements
        assert "exec.delta_ready" not in replacements

    def test_unparseable_values_no_delta(self):
        """If values can't be parsed, delta is skipped gracefully."""
        replacements = {
            "exec.e_mvp_capacity": "thirty-six",
            "exec.e_ideal_capacity": "54",
            "exec.e_mvp_cost": "unknown",
            "exec.e_ideal_cost": "$290,000",
            "exec.f_mvp_ready": "Jan 2027",
            "exec.f_ideal_ready": "04/27",
        }
        compute_v2_deltas(replacements)

        assert "exec.delta_capacity" not in replacements
        assert "exec.delta_cost" not in replacements
        assert "exec.delta_ready" not in replacements

    def test_existing_delta_not_overwritten(self):
        """If a delta value already exists, it is not overwritten."""
        replacements = {
            "exec.e_mvp_capacity": "36",
            "exec.e_ideal_capacity": "54",
            "exec.delta_capacity": "MANUAL",
        }
        compute_v2_deltas(replacements)

        assert replacements["exec.delta_capacity"] == "MANUAL"

    def test_cross_year_timeline_delta(self):
        """Timeline delta works across year boundaries."""
        replacements = {
            "exec.f_mvp_ready": "11/26",
            "exec.f_ideal_ready": "02/27",
        }
        compute_v2_deltas(replacements)

        assert replacements["exec.delta_ready"] == "+3 mo"


class TestLinkTokenSets:
    """Verify link token sets are valid subsets of their template token sets."""

    def test_v1_link_tokens_are_valid(self):
        """Every V1 link token must be in TEMPLATE_TOKEN_SET."""
        bad = LINK_TOKENS_V1 - TEMPLATE_TOKEN_SET
        assert bad == set(), f"V1 link tokens not in template: {bad}"

    def test_v2_link_tokens_are_valid(self):
        """Every V2 link token must be in TEMPLATE_TOKEN_V2_SET."""
        bad = LINK_TOKENS_V2 - TEMPLATE_TOKEN_V2_SET
        assert bad == set(), f"V2 link tokens not in template: {bad}"


class TestV2PipelineToolDefinitions:
    """Verify save_skill_report is registered in the pipeline."""

    def test_save_skill_report_tool_exists(self):
        """save_skill_report must be in TOOL_DEFINITIONS."""
        from due_diligence_reporter.report_pipeline import TOOL_DEFINITIONS

        tool_names = [t["name"] for t in TOOL_DEFINITIONS]
        assert "save_skill_report" in tool_names
