import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import collect_agent_reach
from src.config import Config, load_config
from src.models import NewsItem

EXA_TEXT_SAMPLE = """Title: Intrum AB publishes quarterly interim report
URL: https://www.intrum.com/press/report-q2-2026/
Published: 2026-08-28T07:00:00.000Z
Author: N/A
Highlights:
Intrum AB today published its Q2 interim report.
...
Revenues remained solid while operating leverage improved.
---
Title: Financial news roundup
URL: https://example.com/roundup
Published: N/A
Author: Market Watcher
Highlights:
General European financial market movements and debt servicing updates.
---
Title: Invalid record without URL
Published: 2026-09-01
Highlights:
This should be skipped.
"""


class TestCollectAgentReach(unittest.TestCase):
    def test_parse_results(self):
        items = collect_agent_reach.parse_results(EXA_TEXT_SAMPLE)
        self.assertEqual(len(items), 2)

        first = items[0]
        self.assertEqual(first.title, "Intrum AB publishes quarterly interim report")
        self.assertEqual(first.url, "https://www.intrum.com/press/report-q2-2026/")
        self.assertEqual(first.source, "agent-reach (Exa)")
        self.assertIsNotNone(first.published)
        self.assertEqual(first.published.year, 2026)
        self.assertEqual(first.published.month, 8)
        self.assertEqual(first.published.day, 28)
        self.assertIn("Q2 interim report", first.snippet)
        self.assertFalse(first.snippet.startswith("..."))

        second = items[1]
        self.assertEqual(second.title, "Financial news roundup")
        self.assertIsNone(second.published)

    def test_collect_agent_reach_news_mocked(self):
        cfg = mock.MagicMock(spec=Config)
        cfg.agent_reach_enabled = True
        cfg.agent_reach_queries = ["Intrum corporate news", "Intrum debt servicing"]
        cfg.agent_reach_results_per_query = 5

        def mock_search(query, num_results, **kwargs):
            return EXA_TEXT_SAMPLE

        items = collect_agent_reach.collect_agent_reach_news(cfg, search_fn=mock_search)
        # Should dedupe URLs across queries
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].url, "https://www.intrum.com/press/report-q2-2026/")

    def test_collect_agent_reach_news_disabled(self):
        cfg = mock.MagicMock(spec=Config)
        cfg.agent_reach_enabled = False
        items = collect_agent_reach.collect_agent_reach_news(cfg)
        self.assertEqual(items, [])

    def test_collect_agent_reach_news_tolerates_query_error(self):
        cfg = mock.MagicMock(spec=Config)
        cfg.agent_reach_enabled = True
        cfg.agent_reach_queries = ["failing query", "ok query"]
        cfg.agent_reach_results_per_query = 5

        def mock_search(query, num_results, **kwargs):
            if "failing" in query:
                raise RuntimeError("connection timed out")
            return EXA_TEXT_SAMPLE

        items = collect_agent_reach.collect_agent_reach_news(cfg, search_fn=mock_search)
        self.assertEqual(len(items), 2)


if __name__ == "__main__":
    unittest.main()
