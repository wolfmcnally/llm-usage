import importlib.machinery
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
OPENAI_FIXTURE_PATH = FIXTURES / "openai_usage.json"
ANTHROPIC_FIXTURE_PATH = FIXTURES / "anthropic_usage.json"
ANTHROPIC_LEGACY_FIXTURE_PATH = FIXTURES / "anthropic_usage_legacy.json"

loader = importlib.machinery.SourceFileLoader(
    "llm_usage_under_test", str(ROOT / "llm-usage"))
spec = importlib.util.spec_from_loader(loader.name, loader)
llm_usage = importlib.util.module_from_spec(spec)
loader.exec_module(llm_usage)


class OpenAIUsageTests(unittest.TestCase):
    def setUp(self):
        self.body = json.loads(OPENAI_FIXTURE_PATH.read_text())

    def test_cached_relative_reset_subtracts_cache_and_snapshot_age(self):
        snapshot = {"loaded_at": 1000.0, "age": 120.0}
        window = {"reset_after_seconds": 1000}
        with mock.patch.object(llm_usage.time, "time", return_value=1010.0):
            remaining = llm_usage.adjusted_openai_reset_seconds(
                snapshot, window)
        self.assertEqual(remaining, 870.0)

    def test_render_includes_credits_and_can_suppress_codex_spark(self):
        snapshot = {
            "token": {"access_token": "fixture", "expires_at_ms": None},
            "code": 200,
            "body": self.body,
            "age": 120.0,
            "loaded_at": 1000.0,
        }
        with mock.patch.object(llm_usage.time, "time", return_value=1010.0):
            lines, ok = llm_usage.render_openai_section(snapshot, 120)
        plain = "\n".join(llm_usage.ANSI_RE.sub("", line) for line in lines)
        self.assertTrue(ok)
        self.assertIn("credit balance 42.5", plain)
        self.assertIn("2 usage resets", plain)
        self.assertIn("GPT-5.3-Codex-Spark", plain)
        self.assertTrue(all(llm_usage.visible_len(line) <= 120
                            for line in lines))

        with mock.patch.object(llm_usage.time, "time", return_value=1010.0):
            tui_lines, ok = llm_usage.render_openai_section(
                snapshot, 120, suppress_codex_spark=True)
        tui_plain = "\n".join(
            llm_usage.ANSI_RE.sub("", line) for line in tui_lines)
        self.assertTrue(ok)
        self.assertNotIn("GPT-5.3-Codex-Spark", tui_plain)

    def test_json_normalizes_current_openai_usage_shape(self):
        with mock.patch.object(
                llm_usage, "load_codex_token",
                return_value={"access_token": "fixture"}), mock.patch.object(
                llm_usage, "cached_fetch",
                return_value=(200, self.body, 120.0)), mock.patch.object(
                llm_usage.time, "time", return_value=1000.0):
            result = llm_usage._openai_json()

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["windows"]["primary_window"]["reset_seconds"],
            14280.0,
        )
        self.assertEqual(result["credits"]["balance"], "42.5")
        self.assertEqual(
            result["rate_limit_reset_credits"]["available_count"], 2)
        self.assertEqual(len(result["additional_rate_limits"]), 1)
        extra = result["additional_rate_limits"][0]
        self.assertEqual(extra["name"], "GPT-5.3-Codex-Spark")
        self.assertEqual(extra["metered_feature"], "codex_bengalfox")
        self.assertEqual(
            extra["windows"]["secondary_window"]["utilization"], 7.0)

    def test_reset_credit_detail_rows_are_a_forward_compatible_fallback(self):
        body = {
            "rate_limit_reset_credits": {
                "credits": [
                    {"status": "available"},
                    {"status": "consumed"},
                    {"title": "Legacy detail without status"},
                ]
            }
        }
        self.assertEqual(llm_usage.openai_reset_credit_count(body), 2)


class AnthropicUsageTests(unittest.TestCase):
    def setUp(self):
        self.body = json.loads(ANTHROPIC_FIXTURE_PATH.read_text())
        self.legacy_body = json.loads(
            ANTHROPIC_LEGACY_FIXTURE_PATH.read_text())

    def test_generic_limits_are_canonical_and_keep_metadata(self):
        bands = llm_usage.anthropic_limit_bands(self.body)
        self.assertEqual(
            [band["key"] for band in bands],
            ["seven_day", "seven_day_fable", "five_hour"],
        )
        self.assertEqual([band["utilization"] for band in bands],
                         [40.0, 50.0, 25.0])
        self.assertTrue(all(band["source"] == "limits" for band in bands))
        self.assertEqual(bands[1]["severity"], "warning")
        self.assertTrue(bands[1]["is_active"])

    def test_legacy_limits_and_extra_usage_remain_supported(self):
        bands = llm_usage.anthropic_limit_bands(self.legacy_body)
        self.assertEqual(
            [band["key"] for band in bands],
            ["seven_day", "seven_day_sonnet", "five_hour"],
        )
        self.assertTrue(all(band["source"] == "legacy" for band in bands))
        spend = llm_usage.normalize_anthropic_spend(self.legacy_body)
        self.assertEqual(spend["source"], "extra_usage")
        self.assertEqual(spend["used"]["amount"], 5.0)
        self.assertEqual(spend["limit"]["amount"], 20.0)

    def test_render_includes_spend_and_scoped_limit_status(self):
        snapshot = {
            "token": {
                "access_token": "fixture",
                "expires_at_ms": None,
                "subscription": "max",
            },
            "code": 200,
            "body": self.body,
            "age": 120.0,
            "loaded_at": 1000.0,
        }
        lines, ok = llm_usage.render_anthropic_section(snapshot, 120)
        plain = "\n".join(llm_usage.ANSI_RE.sub("", line) for line in lines)
        self.assertTrue(ok)
        self.assertIn("usage credits on", plain)
        self.assertIn("$12.34 / $50.00", plain)
        self.assertIn("7-day Fable: warning", plain)
        self.assertEqual(plain.count("7-day overall"), 1)
        self.assertTrue(all(llm_usage.visible_len(line) <= 120
                            for line in lines))

    def test_json_includes_normalized_spend_and_generic_limit_metadata(self):
        with mock.patch.object(
                llm_usage, "load_anthropic_token",
                return_value={"access_token": "fixture",
                              "subscription": "max"}), mock.patch.object(
                llm_usage, "cached_fetch",
                return_value=(200, self.body, 120.0)):
            result = llm_usage._anthropic_json()

        self.assertTrue(result["ok"])
        self.assertEqual(result["windows"]["seven_day"]["utilization"], 40.0)
        scoped = result["windows"]["seven_day_fable"]
        self.assertEqual(scoped["kind"], "weekly_scoped")
        self.assertEqual(scoped["severity"], "warning")
        self.assertEqual(result["usage_credits"]["source"], "spend")
        self.assertEqual(result["usage_credits"]["used"]["amount"], 12.34)


if __name__ == "__main__":
    unittest.main()
