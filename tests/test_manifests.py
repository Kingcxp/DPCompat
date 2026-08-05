"""Validation tests for the source-attributed feature minimum manifest."""

from __future__ import annotations

import unittest

from dpcompat.manifests import feature_specs, identifier_minimums, resource_minimums
from dpcompat.models import PackFormat

from pydantic import ValidationError


class ManifestTests(unittest.TestCase):
    def test_registered_features_have_matchers_and_unique_ids(self) -> None:
        specs = feature_specs()
        self.assertGreaterEqual(len(specs), 6)
        ids = [spec.id for spec in specs]
        self.assertEqual(len(ids), len(set(ids)))
        for spec in specs:
            self.assertTrue(spec.resource_types or spec.commands or spec.identifiers)
            self.assertGreater(spec.min_format, PackFormat(61))

    def test_resource_and_identifier_indexes_are_consistent(self) -> None:
        resources = resource_minimums()
        identifiers = identifier_minimums()
        self.assertIn("sulfur_cube_archetype", resources)
        self.assertEqual(resources["sulfur_cube_archetype"][0], PackFormat(107, 1))
        self.assertIn("minecraft:iron_chain", identifiers)
        self.assertEqual(identifiers["minecraft:iron_chain"][0], PackFormat(88))

    def test_feature_without_matcher_is_rejected(self) -> None:
        from dpcompat.manifests import FeatureManifest

        with self.assertRaises(ValidationError):
            FeatureManifest.model_validate(
                {
                    "schema": 1,
                    "features": [
                        {
                            "id": "empty.feature",
                            "min_format": [71, 0],
                            "downgrade": "unsupported",
                            "source": "https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-5",
                        }
                    ],
                }
            )

    def test_duplicate_feature_ids_are_rejected(self) -> None:
        from dpcompat.manifests import FeatureManifest

        with self.assertRaises(ValidationError):
            FeatureManifest.model_validate(
                {
                    "schema": 1,
                    "features": [
                        {
                            "id": "duplicate.feature",
                            "min_format": [71, 0],
                            "identifiers": ["demo:first"],
                            "downgrade": "unsupported",
                            "source": "https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-5",
                        },
                        {
                            "id": "duplicate.feature",
                            "min_format": [71, 0],
                            "identifiers": ["demo:second"],
                            "downgrade": "unsupported",
                            "source": "https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-5",
                        },
                    ],
                }
            )


if __name__ == "__main__":
    unittest.main()
