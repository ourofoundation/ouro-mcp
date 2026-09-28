from __future__ import annotations

import json
import unittest

from ouro.models import AssetTag

from ouro_mcp.utils import slim_asset_tags, strip_heavy_fields


class TestStripHeavyFields(unittest.TestCase):
    def test_strips_tag_embedding_vectors(self) -> None:
        payload = {
            "id": "asset-1",
            "tags": [
                {
                    "source": "auto",
                    "confidence": 0.91,
                    "tag": {
                        "id": "tag-1",
                        "name": "materials",
                        "slug": "materials",
                        "embedding": [0.1] * 768,
                        "fts": "'materi':1",
                    },
                }
            ],
        }
        cleaned = strip_heavy_fields(payload)
        self.assertNotIn("embedding", json.dumps(cleaned))
        self.assertNotIn("fts", json.dumps(cleaned))
        self.assertEqual(cleaned["tags"][0]["tag"]["name"], "materials")

    def test_strips_nested_creation_action_embeddings(self) -> None:
        action = {
            "id": "action-1",
            "response": {"embedding": [0.0, 1.0], "result": "ok"},
        }
        cleaned = strip_heavy_fields(action)
        self.assertEqual(cleaned["response"], {"result": "ok"})


class TestSlimAssetTags(unittest.TestCase):
    def test_returns_agent_metadata_only(self) -> None:
        tag_id = "7b0f4f0e-7d8e-4b4a-9d8c-1f2e3d4c5b6a"
        rows = [
            AssetTag.model_validate(
                {
                    "id": "a1b2c3d4-0000-4000-8000-000000000001",
                    "asset_id": "a1b2c3d4-0000-4000-8000-000000000002",
                    "tag_id": tag_id,
                    "source": "manual",
                    "confidence": None,
                    "tag": {
                        "id": tag_id,
                        "name": "chemistry",
                        "slug": "chemistry",
                        "type": "domain",
                        "description": "Chemistry",
                        "embedding": [0.2] * 768,
                        "rank": 3,
                    },
                }
            )
        ]
        self.assertEqual(
            slim_asset_tags(rows),
            [
                {
                    "source": "manual",
                    "tag": {
                        "id": tag_id,
                        "name": "chemistry",
                        "slug": "chemistry",
                        "type": "domain",
                        "description": "Chemistry",
                    },
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
