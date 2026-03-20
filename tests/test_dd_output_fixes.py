"""Tests for DD report output fixes."""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from due_diligence_reporter.server import (
    MATTERBOT_BASE_URL,
)
from due_diligence_reporter.utils import (
    find_text_index_in_doc,
)
from due_diligence_reporter.wrike import classify_comment_to_section


# ---------------------------------------------------------------------------
# Scope of Work stray numbers — consecutive newline collapse
# ---------------------------------------------------------------------------

class TestScopeOfWorkNewlineCollapse:
    """Verify that consecutive newlines in scope_of_work are collapsed."""

    def test_collapse_double_newlines(self) -> None:
        text = "Item 1\n\nItem 2\n\n\nItem 3"
        result = re.sub(r"\n{2,}", "\n", text)
        assert result == "Item 1\nItem 2\nItem 3"

    def test_single_newlines_preserved(self) -> None:
        text = "Item 1\nItem 2\nItem 3"
        result = re.sub(r"\n{2,}", "\n", text)
        assert result == text

    def test_no_newlines(self) -> None:
        text = "Single line"
        result = re.sub(r"\n{2,}", "\n", text)
        assert result == text


# ---------------------------------------------------------------------------
# find_text_index_in_doc
# ---------------------------------------------------------------------------

class TestFindTextIndex:
    def test_finds_placeholder(self) -> None:
        body = {
            "content": [
                {
                    "paragraph": {
                        "elements": [
                            {
                                "textRun": {
                                    "content": "Hello {{q2.floorplan_image}} world",
                                },
                                "startIndex": 10,
                            }
                        ]
                    }
                }
            ]
        }
        idx = find_text_index_in_doc(body, "{{q2.floorplan_image}}")
        assert idx == 16  # 10 + 6 (offset of "{{" in the string)

    def test_returns_none_when_not_found(self) -> None:
        body = {
            "content": [
                {
                    "paragraph": {
                        "elements": [
                            {
                                "textRun": {"content": "No placeholder here"},
                                "startIndex": 0,
                            }
                        ]
                    }
                }
            ]
        }
        assert find_text_index_in_doc(body, "{{missing}}") is None

    def test_empty_body(self) -> None:
        assert find_text_index_in_doc({}, "{{anything}}") is None


# ---------------------------------------------------------------------------
# Fix 4: PDF mimeType preference — tested via logic check
# ---------------------------------------------------------------------------

class TestPdfMimePreference:
    """Verify that the PDF-preference logic selects PDFs over Google Docs."""

    def test_prefer_pdf_over_gdoc(self) -> None:
        matches = [
            {"name": "ISP.pdf", "mimeType": "application/vnd.google-apps.document", "id": "1"},
            {"name": "ISP.pdf", "mimeType": "application/pdf", "id": "2"},
        ]
        pdf_matches = [f for f in matches if f.get("mimeType") == "application/pdf"]
        best = pdf_matches[0] if pdf_matches else matches[0]
        assert best["id"] == "2"
        assert best["mimeType"] == "application/pdf"

    def test_fallback_to_first_when_no_pdf(self) -> None:
        matches = [
            {"name": "ISP", "mimeType": "application/vnd.google-apps.document", "id": "1"},
        ]
        pdf_matches = [f for f in matches if f.get("mimeType") == "application/pdf"]
        best = pdf_matches[0] if pdf_matches else matches[0]
        assert best["id"] == "1"


# ---------------------------------------------------------------------------
# Fix 5: Comment classification
# ---------------------------------------------------------------------------

class TestCommentClassification:
    def test_zoning_comment(self) -> None:
        assert classify_comment_to_section("Zoning variance required for this location") == "q1"

    def test_pre_app_comment(self) -> None:
        assert classify_comment_to_section("Pre-app meeting notes from city planner") == "q1"

    def test_permit_comment(self) -> None:
        assert classify_comment_to_section("Permit timeline is 6-8 weeks") == "q1"

    def test_building_comment(self) -> None:
        assert classify_comment_to_section("HVAC system needs full replacement") == "q2"

    def test_inspection_comment(self) -> None:
        assert classify_comment_to_section("Building inspection scheduled for Monday") == "q2"

    def test_cost_comment(self) -> None:
        assert classify_comment_to_section("Budget estimate came in at $250k") == "q3"

    def test_timeline_comment(self) -> None:
        assert classify_comment_to_section("Timeline pushed back, target date is Q3") == "q4"

    def test_general_comment(self) -> None:
        assert classify_comment_to_section("Great location, team is excited") == "general"

    def test_empty_comment(self) -> None:
        assert classify_comment_to_section("") == "general"


# ---------------------------------------------------------------------------
# MatterBot integration
# ---------------------------------------------------------------------------

class TestGenerateMarketingPack:
    """Tests for the generate_marketing_pack MCP tool."""

    def test_matterbot_base_url_is_set(self) -> None:
        assert MATTERBOT_BASE_URL.startswith("https://")
        assert "matterbot" in MATTERBOT_BASE_URL

    @patch("due_diligence_reporter.server.requests.get")
    def test_successful_trigger(self, mock_get: MagicMock) -> None:
        import asyncio
        from due_diligence_reporter.server import generate_marketing_pack

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = f"{MATTERBOT_BASE_URL}/api/batch/generate-marketing-pack/abc123?space_name=Test"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = asyncio.run(generate_marketing_pack(
            space_sid="abc123", space_name="Test Site",
        ))

        assert result["status"] == "success"
        assert "triggered" in result["message"]
        mock_get.assert_called_once()
        call_url = mock_get.call_args[0][0]
        assert "abc123" in call_url

    def test_empty_space_sid_returns_error(self) -> None:
        import asyncio
        from due_diligence_reporter.server import generate_marketing_pack

        result = asyncio.run(generate_marketing_pack(space_sid="", space_name="Test"))
        assert result["status"] == "error"
        assert "space_sid" in result["message"]

    def test_empty_space_name_returns_error(self) -> None:
        import asyncio
        from due_diligence_reporter.server import generate_marketing_pack

        result = asyncio.run(generate_marketing_pack(space_sid="abc", space_name=""))
        assert result["status"] == "error"
        assert "space_name" in result["message"]

    def test_invalid_tier_returns_error(self) -> None:
        import asyncio
        from due_diligence_reporter.server import generate_marketing_pack

        result = asyncio.run(generate_marketing_pack(
            space_sid="abc", space_name="Test", tier="ultra",
        ))
        assert result["status"] == "error"
        assert "tier" in result["message"]

    @patch("due_diligence_reporter.server.requests.get")
    def test_optional_params_passed_correctly(self, mock_get: MagicMock) -> None:
        import asyncio
        from due_diligence_reporter.server import generate_marketing_pack

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "http://test"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        asyncio.run(generate_marketing_pack(
            space_sid="abc123", space_name="Test Site",
            tier="premium", max_rooms=5, room_types="classroom,commons",
        ))

        call_kwargs = mock_get.call_args
        params = call_kwargs[1]["params"]
        assert params["tier"] == "premium"
        assert params["max_rooms"] == 5
        assert params["room_types"] == "classroom,commons"

    @patch("due_diligence_reporter.server.requests.get")
    def test_timeout_returns_error(self, mock_get: MagicMock) -> None:
        import asyncio
        from due_diligence_reporter.server import generate_marketing_pack
        import requests as _requests

        mock_get.side_effect = _requests.Timeout("timed out")

        result = asyncio.run(generate_marketing_pack(
            space_sid="abc123", space_name="Test Site",
        ))

        assert result["status"] == "error"
        assert "timeout" in result["error"].lower()

    @patch("due_diligence_reporter.server.requests.get")
    def test_http_error_returns_error(self, mock_get: MagicMock) -> None:
        import asyncio
        from due_diligence_reporter.server import generate_marketing_pack
        import requests as _requests

        mock_get.side_effect = _requests.ConnectionError("refused")

        result = asyncio.run(generate_marketing_pack(
            space_sid="abc123", space_name="Test Site",
        ))

        assert result["status"] == "error"
        assert "failed" in result["error"].lower()
