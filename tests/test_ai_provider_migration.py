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

    def test_bound_credential_is_not_decrypted_for_a_different_endpoint(self) -> None:
        from study_app.ai import providers

        for provider, endpoint in (
            ("custom", "https://endpoint-b.example.test/v1"),
            ("deepseek", ""),
        ):
            with self.subTest(provider=provider):
                raw = {
                    **providers.DEFAULT_LLM_SETTINGS,
                    "enabled": True,
                    "provider": provider,
                    "custom_base_url": endpoint,
                    "api_key_protected": "isolated-encrypted-key",
                    "api_key_identity": ["custom", "https://endpoint-a.example.test/v1"],
                }
                with (
                    patch.object(providers, "get_setting", return_value=raw),
                    patch.object(providers, "unprotect_secret") as decrypt,
                    patch.object(providers, "set_setting") as persist,
                ):
                    settings = providers.load_llm_settings()
                self.assertEqual(settings.api_key, "")
                decrypt.assert_not_called()
                persist.assert_not_called()

    def test_legacy_encrypted_key_is_bound_to_current_endpoint(self) -> None:
        from study_app.ai import providers

        raw = {
            **providers.DEFAULT_LLM_SETTINGS,
            "provider": "custom",
            "custom_base_url": "https://endpoint-a.example.test/v1/",
            "api_key_protected": "isolated-encrypted-key",
        }
        with (
            patch.object(providers, "get_setting", return_value=raw),
            patch.object(providers, "unprotect_secret", return_value="isolated-key"),
            patch.object(providers, "set_setting") as persist,
        ):
            settings = providers.load_llm_settings()
        self.assertEqual(settings.api_key, "isolated-key")
        self.assertEqual(
            persist.call_args.args[1]["api_key_identity"],
            ["custom", "https://endpoint-a.example.test/v1"],
        )


if __name__ == "__main__":
    unittest.main()
