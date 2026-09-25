import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import run


class TestRunIds(unittest.TestCase):
    def test_next_run_ids_single(self):
        ids = run._next_run_ids("2099-01-01", ["last30days"])
        self.assertEqual(ids, {"last30days": "2099-01-01"})

    def test_next_run_ids_both(self):
        ids = run._next_run_ids("2099-01-01", ["last30days", "agent-reach"])
        self.assertEqual(set(ids), {"last30days", "agent-reach"})
        self.assertRegex(ids["last30days"], r"^2099-01-01-\d{4}-last30days$")
        self.assertRegex(ids["agent-reach"], r"^2099-01-01-\d{4}-agent-reach$")

    def test_write_report_engine_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(run, "REPORTS_DIR", Path(tmp)):
                run._write_report("2099-01-01-1200-agent-reach", "2099-01-01",
                                  "body", 3, 0, engine="agent-reach")
                meta = json.loads(
                    (Path(tmp) / "2099-01-01-1200-agent-reach.meta.json").read_text(encoding="utf-8")
                )
                self.assertEqual(meta["engine"], "agent-reach")

                run._write_report("2099-01-01", "2099-01-01", "body", 3, 5, engine=None)
                meta2 = json.loads(
                    (Path(tmp) / "2099-01-01.meta.json").read_text(encoding="utf-8")
                )
                self.assertNotIn("engine", meta2)


if __name__ == "__main__":
    unittest.main()
