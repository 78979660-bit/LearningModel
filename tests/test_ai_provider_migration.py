from __future__ import annotations

import unittest
from unittest.mock import patch


class AIProviderMigrationTests(unittest.TestCase):
    def test_legacy_missing_feature_list_enables_only_historical_parser(self) -> None:
        from study_app.ai import providers

        legacy = {"enabled": True, "provider": "openai"}
        with (
            patch.object(providers, "get_setting", return_value=legacy),
            patch.object(providers, "set_setting") as persist,
        ):
            settings = providers.load_llm_settings()

        self.assertEqual(settings.enabled_features, ("record_parser",))
        self.assertEqual(persist.call_args.args[1]["enabled_features"], ["record_parser"])

    def test_legacy_explicit_empty_feature_consent_stays_empty(self) -> None:
        from study_app.ai import providers

        legacy = {
            "enabled": True,
            "provider": "openai",
            "enabled_features": [],
        }
        with (
            patch.object(providers, "get_setting", return_value=legacy),
            patch.object(providers, "set_setting") as persist,
        ):
            settings = providers.load_llm_settings()

        self.assertEqual(settings.enabled_features, ())
        self.assertEqual(persist.call_args.args[1]["enabled_features"], [])

    def test_legacy_explicit_feature_consent_is_preserved(self) -> None:
        from study_app.ai import providers

        legacy = {
            "enabled": True,
            "provider": "openai",
            "api_key": "PRIVATE_KEY",
            "enabled_features": ["record_parser"],
        }
        with (
            patch.object(providers, "get_setting", return_value=legacy),
            patch.object(providers, "set_setting") as persist,
        ):
            settings = providers.load_llm_settings()

        self.assertEqual(settings.enabled_features, ("record_parser",))
        self.assertNotIn("natural_query", settings.enabled_features)
        self.assertNotIn("daily_summary", settings.enabled_features)
        persisted = persist.call_args.args[1]
        self.assertEqual(persisted["enabled_features"], ["record_parser"])
        self.assertEqual(persisted["feature_defaults_version"], 3)


if __name__ == "__main__":
    unittest.main()
