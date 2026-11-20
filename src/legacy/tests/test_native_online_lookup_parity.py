"""Offline parity fixtures for the Rust manual lookup and legacy online.ask."""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_modeler.agent import online


FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "core"
    / "cad-engine"
    / "fixtures"
    / "native_online_lookup.json"
)
LEGACY_FIELDS = (
    "ok",
    "results",
    "suggestion",
    "reason",
    "query",
    "notes",
    "params",
)


def run_fixture(case):
    search_index = 0
    search_queries = []
    opened_urls = []
    search_responses = case["search_responses"]
    page_responses = case["page_responses"]

    def search(query, timeout=8):
        nonlocal search_index
        search_queries.append(query)
        response = search_responses[search_index] if search_index < len(search_responses) else {"rows": []}
        search_index += 1
        if "error" in response:
            raise RuntimeError(response["error"])
        return response.get("rows", [])

    def fetch_page_text(url, timeout=6, limit=4000):
        opened_urls.append(url)
        page = page_responses.get(url, {})
        if "text_repeat" in page:
            spec = page["text_repeat"]
            body = spec.get("value", "") * spec.get("count", 0) + spec.get("suffix", "")
        else:
            body = page.get("text", "")
        return body[:limit]

    with mock.patch.object(online, "search", side_effect=search), mock.patch.object(
        online, "fetch_page_text", side_effect=fetch_page_text
    ):
        response = online.ask(case["query"])
    return {
        "search_queries": search_queries,
        "opened_urls": opened_urls,
        "response": response,
    }


def load_cases():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def write_goldens():
    cases = load_cases()
    for case in cases:
        case["expected"] = run_fixture(case)
    FIXTURE_PATH.write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class NativeOnlineLookupParityTests(unittest.TestCase):
    def test_python_ask_matches_fixed_offline_golden_fixtures(self):
        for case in load_cases():
            with self.subTest(case=case["id"]):
                self.assertEqual(run_fixture(case), case["expected"])

    def test_golden_covers_no_result_params_absence_and_no_parameter_empty_map(self):
        cases = {case["id"]: case["expected"]["response"] for case in load_cases()}
        self.assertNotIn("params", cases["no_results_omits_params"])
        self.assertEqual(cases["search_results_without_dimensions"]["params"], {})

    def test_deep_lookup_reads_only_the_selected_first_two_result_pages(self):
        case = next(
            row
            for row in load_cases()
            if row["id"] == "expanded_queries_deep_pages_and_m_size_override"
        )
        expected = case["expected"]
        self.assertEqual(
            expected["search_queries"],
            [
                "M5 内六角螺钉 标准 规格 尺寸表 mm",
                "M5 内六角螺钉 规格参数 mm",
            ],
        )
        self.assertEqual(
            expected["opened_urls"],
            ["https://example.test/screw-a", "https://example.test/screw-b"],
        )

    def test_parameter_frequency_tie_keeps_first_value(self):
        case = next(
            row
            for row in load_cases()
            if row["id"] == "parameter_frequency_tie_keeps_first_value"
        )
        self.assertEqual(case["expected"]["response"]["params"]["直径"], 5.0)

    def test_page_body_is_limited_to_4000_characters(self):
        case = next(
            row
            for row in load_cases()
            if row["id"] == "page_body_is_limited_to_4000_characters"
        )
        response = case["expected"]["response"]
        self.assertEqual(response["params"], {})
        self.assertIsNone(response["suggestion"])


if __name__ == "__main__":
    if "--write-goldens" in sys.argv:
        write_goldens()
    else:
        unittest.main()
