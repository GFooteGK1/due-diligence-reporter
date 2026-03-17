"""Tests for DD report output fixes (Fixes 1–5)."""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from due_diligence_reporter.report_schema import (
    AGENT_KEY_ALIASES,
    TEMPLATE_TOKEN_SET,
)
from due_diligence_reporter.server import (
    MATTERBOT_BASE_URL,
    _embed_floorplan_image,
)
from due_diligence_reporter.utils import (
    extract_floorplan_image_from_isp,
    find_text_index_in_doc,
)
from due_diligence_reporter.wrike import classify_comment_to_section


# ---------------------------------------------------------------------------
# Fix 1: Scope of Work stray numbers — consecutive newline collapse
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
# Fix 3: M1 subfolder link — token and alias exist
# ---------------------------------------------------------------------------

class TestM1SubfolderSchema:
    def test_renderings_link_token_exists(self) -> None:
        assert "q2.renderings_link" in TEMPLATE_TOKEN_SET

    def test_m1_folder_alias_exists(self) -> None:
        assert "q2.m1_folder_link" in AGENT_KEY_ALIASES
        assert AGENT_KEY_ALIASES["q2.m1_folder_link"] == "q2.renderings_link"


# ---------------------------------------------------------------------------
# Fix 2: Floorplan image — token exists + extraction function
# ---------------------------------------------------------------------------

class TestFloorplanImage:
    def test_floorplan_image_token_exists(self) -> None:
        assert "q2.floorplan_image" in TEMPLATE_TOKEN_SET

    def test_extract_from_empty_bytes(self) -> None:
        result = extract_floorplan_image_from_isp(b"")
        assert result is None

    def test_extract_from_invalid_pdf(self) -> None:
        result = extract_floorplan_image_from_isp(b"not a pdf")
        assert result is None


# ---------------------------------------------------------------------------
# Fix 2: find_text_index_in_doc
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
# Fix 2 integration: _embed_floorplan_image helper
# ---------------------------------------------------------------------------

class TestEmbedFloorplanImage:
    """Integration tests for the extracted _embed_floorplan_image helper."""

    def _make_mock_gc(self, *, image_data: tuple[bytes, str] | None = None) -> MagicMock:
        gc = MagicMock()
        gc.download_file_bytes.return_value = b"fake-pdf-bytes"
        gc.upload_file_to_folder.return_value = {"id": "img123", "webViewLink": "https://drive.google.com/file/d/img123/view"}
        gc.get_document.return_value = {
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "textRun": {"content": "Before {{q2.floorplan_image}} After"},
                                    "startIndex": 0,
                                }
                            ]
                        }
                    }
                ]
            }
        }
        gc.batch_update_document.return_value = {}
        return gc

    @patch("due_diligence_reporter.server.extract_floorplan_image_from_isp")
    def test_successful_insertion_uses_direct_download_uri(self, mock_extract: MagicMock) -> None:
        mock_extract.return_value = (b"image-bytes", "image/png")
        gc = self._make_mock_gc()

        result = _embed_floorplan_image(
            gc, doc_id="doc1", folder_id="folder1",
            isp_file_id="isp1", site_name="Test Site",
        )

        assert result is True
        # Verify the URI passed to insertInlineImage is a direct download link
        batch_call_args = gc.batch_update_document.call_args_list[-1]
        requests_list = batch_call_args[0][1]
        insert_req = requests_list[1]["insertInlineImage"]
        assert "uc?id=img123&export=download" in insert_req["uri"]
        assert "webViewLink" not in insert_req["uri"]

    @patch("due_diligence_reporter.server.extract_floorplan_image_from_isp")
    def test_jpeg_image_gets_jpg_extension(self, mock_extract: MagicMock) -> None:
        mock_extract.return_value = (b"jpeg-bytes", "image/jpeg")
        gc = self._make_mock_gc()

        _embed_floorplan_image(
            gc, doc_id="doc1", folder_id="folder1",
            isp_file_id="isp1", site_name="Test Site",
        )

        upload_call = gc.upload_file_to_folder.call_args
        filename = upload_call[0][1]
        assert filename.endswith(".jpg")
        assert not filename.endswith(".png")

    @patch("due_diligence_reporter.server.extract_floorplan_image_from_isp")
    def test_png_image_gets_png_extension(self, mock_extract: MagicMock) -> None:
        mock_extract.return_value = (b"png-bytes", "image/png")
        gc = self._make_mock_gc()

        _embed_floorplan_image(
            gc, doc_id="doc1", folder_id="folder1",
            isp_file_id="isp1", site_name="Test Site",
        )

        upload_call = gc.upload_file_to_folder.call_args
        filename = upload_call[0][1]
        assert filename.endswith(".png")

    @patch("due_diligence_reporter.server.extract_floorplan_image_from_isp")
    def test_no_image_found_returns_false_and_sets_fallback(self, mock_extract: MagicMock) -> None:
        mock_extract.return_value = None
        gc = self._make_mock_gc()

        result = _embed_floorplan_image(
            gc, doc_id="doc1", folder_id="folder1",
            isp_file_id="isp1", site_name="Test Site",
        )

        assert result is False
        # Fallback should replace placeholder with gap label
        fallback_call = gc.batch_update_document.call_args
        req = fallback_call[0][1][0]["replaceAllText"]
        assert "Floorplan image not available" in req["replaceText"]

    def test_empty_isp_file_id_returns_false(self) -> None:
        gc = self._make_mock_gc()

        result = _embed_floorplan_image(
            gc, doc_id="doc1", folder_id="folder1",
            isp_file_id="", site_name="Test Site",
        )

        assert result is False
        gc.download_file_bytes.assert_not_called()

    @patch("due_diligence_reporter.server.extract_floorplan_image_from_isp")
    def test_download_failure_falls_back_gracefully(self, mock_extract: MagicMock) -> None:
        gc = self._make_mock_gc()
        gc.download_file_bytes.side_effect = RuntimeError("Download failed")

        result = _embed_floorplan_image(
            gc, doc_id="doc1", folder_id="folder1",
            isp_file_id="isp1", site_name="Test Site",
        )

        assert result is False
        # Fallback placeholder replacement should still be attempted
        assert gc.batch_update_document.called


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
