from __future__ import annotations

import unittest

from ouro_mcp.tools.datasets import _coerce_json_object


class TestDatasetViewHelpers(unittest.TestCase):
    def test_coerce_json_object_accepts_dict(self) -> None:
        value = {"type": "bar", "series": [{"dataKey": "total"}]}
        self.assertEqual(
            _coerce_json_object(value, parameter_name="config"),
            value,
        )

    def test_coerce_json_object_accepts_json_string(self) -> None:
        value = '{"type":"bar","series":[{"dataKey":"total"}]}'
        self.assertEqual(
            _coerce_json_object(value, parameter_name="config"),
            {"type": "bar", "series": [{"dataKey": "total"}]},
        )

    def test_coerce_json_object_rejects_non_object_json(self) -> None:
        with self.assertRaises(ValueError):
            _coerce_json_object('["not", "an", "object"]', parameter_name="config")


if __name__ == "__main__":
    unittest.main()
