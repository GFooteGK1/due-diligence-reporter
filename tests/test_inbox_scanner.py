"""Tests for the inbox scanner module."""

from __future__ import annotations

import re
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from due_diligence_reporter.inbox_scanner import (
    AUTO_FILE_CONFIDENCE,
    DOC_TYPE_FILENAME_TEMPLATES,
    SUPPORTED_DOC_TYPES,
    ClassificationResult,
    _fallback_classify,
    _generate_drive_filename,
    _walk_parts,
    process_email,
)


# ---------------------------------------------------------------------------
# Filename generation
# ---------------------------------------------------------------------------


class TestGenerateDriveFilename:
    """Generated filenames must work with existing _classify_document_type() patterns."""

    def test_sir_filename_contains_sir_keyword(self):
        name = _generate_drive_filename("Alpha Keller", "sir")
        assert "SIR" in name
        assert "Alpha Keller" in name
        assert name.endswith(".pdf")

    def test_building_inspection_filename_contains_inspection(self):
        name = _generate_drive_filename("Alpha Boca Raton", "building_inspection")
        assert "Inspection" in name
        assert "Alpha Boca Raton" in name
        assert name.endswith(".pdf")

    def test_filename_starts_with_date(self):
        name = _generate_drive_filename("Alpha Keller", "sir")
        today = datetime.now().strftime("%b %d %Y")
        assert name.startswith(today)

    def test_sir_matches_classify_document_type_pattern(self):
        """The generated SIR filename must be classified as 'sir' by the server's regex."""
        name = _generate_drive_filename("Alpha Southlake", "sir")
        # Mirrors server.py _classify_document_type SIR check
        assert re.search(r"\bsir\b", name.lower())

    def test_inspection_matches_classify_document_type_pattern(self):
        """The generated inspection filename must be classified as 'building_inspection'."""
        name = _generate_drive_filename("Alpha Norwalk", "building_inspection")
        # Mirrors server.py _classify_document_type Building Inspection check
        assert "inspection" in name.lower()

    def test_all_supported_doc_types_have_templates(self):
        for doc_type in SUPPORTED_DOC_TYPES:
            assert doc_type in DOC_TYPE_FILENAME_TEMPLATES


# ---------------------------------------------------------------------------
# LLM prompt / classification structure
# ---------------------------------------------------------------------------


class TestClassifyPromptStructure:
    """Ensure the LLM classification function produces well-structured results."""

    def test_fallback_classify_sir(self):
        result = _fallback_classify("Keller_SIR_2026.pdf", [])
        assert result.doc_type == "sir"
        assert result.confidence > 0

    def test_fallback_classify_inspection(self):
        result = _fallback_classify("Building_Inspection_Boca.pdf", [])
        assert result.doc_type == "building_inspection"
        assert result.confidence > 0

    def test_fallback_classify_unknown(self):
        result = _fallback_classify("random_document.pdf", [])
        assert result.doc_type == "unknown"
        assert result.confidence == 0.0

    def test_fallback_no_site_match(self):
        """Fallback never attempts site matching."""
        candidates = [{"id": "ABC123", "title": "Alpha Keller"}]
        result = _fallback_classify("Some_SIR.pdf", candidates)
        assert result.matched_site_id is None
        assert result.matched_site_title is None

    def test_confidence_threshold_is_reasonable(self):
        assert 0.5 <= AUTO_FILE_CONFIDENCE <= 0.9


# ---------------------------------------------------------------------------
# MIME part walking
# ---------------------------------------------------------------------------


class TestWalkParts:
    """Test recursive MIME part extraction."""

    def test_extracts_pdf_attachments(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"size": 100},
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "report.pdf",
                    "body": {"attachmentId": "att_123", "size": 50000},
                },
            ],
        }
        attachments: list = []
        _walk_parts(payload, attachments)
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "report.pdf"
        assert attachments[0]["attachment_id"] == "att_123"

    def test_skips_non_pdf_attachments(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "image/png",
                    "filename": "logo.png",
                    "body": {"attachmentId": "att_456", "size": 1000},
                },
            ],
        }
        attachments: list = []
        _walk_parts(payload, attachments)
        assert len(attachments) == 0

    def test_handles_nested_parts(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "application/pdf",
                            "filename": "nested.pdf",
                            "body": {"attachmentId": "att_789", "size": 30000},
                        },
                    ],
                },
            ],
        }
        attachments: list = []
        _walk_parts(payload, attachments)
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "nested.pdf"


# ---------------------------------------------------------------------------
# Idempotency — already-processed emails
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Emails with the DD-Processed label should not be re-processed."""

    def test_gmail_search_query_excludes_processed_label(self):
        """The scan query must exclude already-labeled messages."""
        from due_diligence_reporter.config import Settings

        settings = Settings()
        query = f"{settings.inbox_scan_query} -label:{settings.inbox_processed_label}"
        assert "-label:DD-Processed" in query

    @patch("due_diligence_reporter.inbox_scanner._classify_and_match_site")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_unknown_doc_type_skipped_and_email_marked(
        self, mock_extract, mock_classify
    ):
        """Emails where all attachments are 'unknown' should be marked processed."""
        mock_extract.return_value = MagicMock(
            message_id="msg_1",
            subject="Hello",
            sender="test@example.com",
            body_snippet="Some text",
            attachments=[{"filename": "notes.pdf", "attachment_id": "a1", "mime_type": "application/pdf"}],
        )
        mock_classify.return_value = ClassificationResult(
            doc_type="unknown",
            matched_site_id=None,
            matched_site_title=None,
            confidence=0.0,
            reasoning="Not a DD document",
        )

        gc = MagicMock()
        result = process_email(gc, "msg_1", [], MagicMock(), "label_123")

        assert result["skipped"] == 1
        assert len(result["uploaded"]) == 0
        # Email should still be marked (all attachments were handled, just skipped)
        assert result["marked"] is True
