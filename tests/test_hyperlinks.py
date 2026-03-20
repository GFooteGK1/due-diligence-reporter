"""Tests for the hyperlink request builder."""

from __future__ import annotations

from due_diligence_reporter.utils import build_hyperlink_requests, find_text_index_in_doc


# Minimal Google Docs body structure for testing
def _make_doc_body(texts: list[tuple[int, str]]) -> dict:
    """Build a minimal doc body with one paragraph per tuple (single textRun each)."""
    elements = []
    for start_idx, content in texts:
        elements.append({
            "paragraph": {
                "elements": [
                    {
                        "startIndex": start_idx,
                        "textRun": {"content": content},
                    }
                ]
            }
        })
    return {"content": elements}


def _make_doc_body_split_runs(runs_per_para: list[list[tuple[int, str]]]) -> dict:
    """Build a doc body where each paragraph has multiple textRun elements."""
    elements = []
    for runs in runs_per_para:
        para_elements = []
        for start_idx, content in runs:
            para_elements.append({
                "startIndex": start_idx,
                "textRun": {"content": content},
            })
        elements.append({"paragraph": {"elements": para_elements}})
    return {"content": elements}


LINK_TOKENS = frozenset({"sources.sir_link", "sources.isp_link", "meta.drive_folder_url"})


class TestBuildHyperlinkRequests:
    """Tests for build_hyperlink_requests."""

    def test_produces_update_text_style_for_urls(self):
        """URL values produce updateTextStyle requests with link.url."""
        doc_body = _make_doc_body([
            (10, "https://drive.google.com/file/abc"),
        ])
        replacements = {
            "sources.sir_link": "https://drive.google.com/file/abc",
        }

        requests = build_hyperlink_requests(doc_body, replacements, LINK_TOKENS)

        assert len(requests) == 1
        req = requests[0]["updateTextStyle"]
        assert req["range"]["startIndex"] == 10
        assert req["range"]["endIndex"] == 10 + len("https://drive.google.com/file/abc")
        assert req["textStyle"]["link"]["url"] == "https://drive.google.com/file/abc"
        assert req["fields"] == "link"

    def test_skips_gap_labels(self):
        """Non-URL values (gap labels) are not hyperlinked."""
        doc_body = _make_doc_body([
            (5, "[Not found — SIR not yet in shared Drive folder]"),
        ])
        replacements = {
            "sources.sir_link": "[Not found — SIR not yet in shared Drive folder]",
        }

        requests = build_hyperlink_requests(doc_body, replacements, LINK_TOKENS)

        assert requests == []

    def test_skips_empty_values(self):
        """Empty string values are not hyperlinked."""
        doc_body = _make_doc_body([])
        replacements = {
            "sources.sir_link": "",
        }

        requests = build_hyperlink_requests(doc_body, replacements, LINK_TOKENS)

        assert requests == []

    def test_skips_tokens_not_in_link_set(self):
        """Tokens not in the link_tokens set are ignored even if they contain URLs."""
        doc_body = _make_doc_body([
            (0, "https://example.com/something"),
        ])
        replacements = {
            "exec.c_answer": "https://example.com/something",
        }

        requests = build_hyperlink_requests(doc_body, replacements, LINK_TOKENS)

        assert requests == []

    def test_handles_multiple_links(self):
        """Multiple link tokens each produce their own request."""
        doc_body = _make_doc_body([
            (10, "https://drive.google.com/sir before https://drive.google.com/isp"),
        ])
        replacements = {
            "sources.sir_link": "https://drive.google.com/sir",
            "sources.isp_link": "https://drive.google.com/isp",
        }

        requests = build_hyperlink_requests(doc_body, replacements, LINK_TOKENS)

        assert len(requests) == 2
        urls = {r["updateTextStyle"]["textStyle"]["link"]["url"] for r in requests}
        assert urls == {"https://drive.google.com/sir", "https://drive.google.com/isp"}

    def test_url_not_found_in_doc_is_skipped(self):
        """If the URL text isn't found in the doc body, no request is produced."""
        doc_body = _make_doc_body([
            (0, "some other text"),
        ])
        replacements = {
            "sources.sir_link": "https://drive.google.com/missing",
        }

        requests = build_hyperlink_requests(doc_body, replacements, LINK_TOKENS)

        assert requests == []

    def test_empty_link_tokens_produces_empty(self):
        """An empty link_tokens set produces no requests."""
        doc_body = _make_doc_body([
            (0, "https://example.com"),
        ])
        replacements = {
            "sources.sir_link": "https://example.com",
        }

        requests = build_hyperlink_requests(doc_body, replacements, frozenset())

        assert requests == []

    def test_url_split_across_text_runs(self):
        """A URL split across multiple textRun elements is still hyperlinked."""
        # Google Docs splits "https://drive.google.com/drive/folders/abc123" into two runs
        doc_body = _make_doc_body_split_runs([
            [
                (10, "https://drive.google.com/drive/"),
                (40, "folders/abc123"),
            ],
        ])
        full_url = "https://drive.google.com/drive/folders/abc123"
        replacements = {
            "meta.drive_folder_url": full_url,
        }

        requests = build_hyperlink_requests(doc_body, replacements, LINK_TOKENS)

        assert len(requests) == 1
        req = requests[0]["updateTextStyle"]
        assert req["range"]["startIndex"] == 10
        assert req["range"]["endIndex"] == 10 + len(full_url)
        assert req["textStyle"]["link"]["url"] == full_url


class TestFindTextIndexSplitRuns:
    """Tests for find_text_index_in_doc with split textRuns."""

    def test_finds_text_in_single_run(self):
        doc_body = _make_doc_body([(5, "hello world")])
        assert find_text_index_in_doc(doc_body, "world") == 11

    def test_finds_text_spanning_two_runs(self):
        doc_body = _make_doc_body_split_runs([
            [
                (10, "https://drive.google."),
                (31, "com/folders/abc"),
            ],
        ])
        assert find_text_index_in_doc(doc_body, "https://drive.google.com/folders/abc") == 10

    def test_returns_none_when_not_found(self):
        doc_body = _make_doc_body([(0, "no match here")])
        assert find_text_index_in_doc(doc_body, "missing") is None

    def test_finds_text_in_middle_of_split_runs(self):
        doc_body = _make_doc_body_split_runs([
            [
                (0, "prefix "),
                (7, "https://example"),
                (22, ".com/path"),
            ],
        ])
        assert find_text_index_in_doc(doc_body, "https://example.com/path") == 7
