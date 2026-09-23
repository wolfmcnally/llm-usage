import importlib.machinery
import importlib.util
import json
import socket
import threading
import urllib.error
import urllib.request
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


def service_status_snapshot(provider, indicator="none",
                            description="All Systems Operational"):
    return {
        "provider": provider,
        "status": {
            "provider": provider,
            "indicator": indicator,
            "description": description,
            "operational": indicator == "none",
            "affected_components": [],
        },
        "cached": False,
        "stale": False,
        "age": None,
        "error": None,
        "loaded_at": 1000.0,
        "deadline": 1060.0,
    }


class ProviderStatusTests(unittest.TestCase):
    def test_normalizes_affected_components(self):
        result = llm_usage.normalize_provider_status("anthropic", {
            "status": {
                "indicator": "major",
                "description": "Partial System Outage",
            },
            "components": [
                {"name": "Claude API", "status": "major_outage"},
                {"name": "Console", "status": "operational"},
            ],
        })
        self.assertFalse(result["operational"])
        self.assertEqual(result["indicator"], "major")
        self.assertEqual(result["affected_components"], [{
            "name": "Claude API",
            "status": "major_outage",
        }])

    def test_outage_is_highlighted_and_operational_status_is_quiet(self):
        outage = service_status_snapshot(
            "anthropic", "major", "Partial System Outage")
        line = llm_usage.provider_status_line(outage)
        self.assertIn("Service status: Partial System Outage", line)
        self.assertIn(llm_usage.fg256(203), line)
        self.assertIsNone(llm_usage.provider_status_line(
            service_status_snapshot("openai")))

    def test_fresh_disk_cache_avoids_network_request(self):
        cached = {
            "ts": 990.0,
            "body": {
                "status": {
                    "indicator": "none",
                    "description": "All Systems Operational",
                },
                "components": [],
            },
        }
        with mock.patch.object(
                llm_usage, "_read_cache_document",
                return_value=cached), mock.patch.object(
                llm_usage, "http_get_json_response") as request, \
                mock.patch.object(llm_usage.time, "time", return_value=1000.0):
            result = llm_usage.provider_status_snapshot("openai")
        request.assert_not_called()
        self.assertTrue(result["cached"])
        self.assertFalse(result["stale"])
        self.assertTrue(result["status"]["operational"])
        self.assertEqual(result["deadline"], 1050.0)

    def test_failed_refresh_preserves_last_known_outage_and_backs_off(self):
        cached = {
            "ts": 900.0,
            "body": {
                "status": {
                    "indicator": "major",
                    "description": "Partial System Outage",
                },
                "components": [],
            },
            "failures": 0,
        }
        with mock.patch.object(
                llm_usage, "_read_cache_document",
                return_value=cached), mock.patch.object(
                llm_usage, "http_get_json_response",
                return_value=(503, {"error": "unavailable"}, {})), \
                mock.patch.object(
                    llm_usage, "_write_cache_document") as write, \
                mock.patch.object(
                    llm_usage.random, "uniform", return_value=1.0), \
                mock.patch.object(llm_usage.time, "time", return_value=1000.0):
            result = llm_usage.provider_status_snapshot("anthropic")
        self.assertTrue(result["stale"])
        self.assertFalse(result["status"]["operational"])
        self.assertEqual(result["deadline"], 1300.0)
        written = write.call_args.args[1]
        self.assertEqual(written["body"], cached["body"])
        self.assertEqual(written["retry_at"], 1300.0)

    def test_etag_revalidation_reuses_body_after_304(self):
        cached = {
            "ts": 900.0,
            "body": {
                "status": {
                    "indicator": "none",
                    "description": "All Systems Operational",
                },
                "components": [],
            },
            "etag": 'W/"fixture"',
        }
        with mock.patch.object(
                llm_usage, "_read_cache_document",
                return_value=cached), mock.patch.object(
                llm_usage, "http_get_json_response",
                return_value=(304, {"error": "http"},
                              {"etag": 'W/"fixture"'})) as request, \
                mock.patch.object(
                    llm_usage, "_write_cache_document") as write, \
                mock.patch.object(llm_usage.time, "time", return_value=1000.0):
            result = llm_usage.provider_status_snapshot("anthropic")
        self.assertEqual(
            request.call_args.args[1]["If-None-Match"], 'W/"fixture"')
        self.assertTrue(result["status"]["operational"])
        self.assertFalse(result["stale"])
        self.assertEqual(write.call_args.args[1]["ts"], 1000.0)

    def test_zero_status_ttl_does_not_read_or_write_cache(self):
        with mock.patch.dict(
                llm_usage.os.environ, {"LLM_USAGE_STATUS_TTL": "0"}), \
                mock.patch.object(
                    llm_usage, "_read_cache_document") as read, \
                mock.patch.object(
                    llm_usage, "_write_cache_document") as write, \
                mock.patch.object(
                    llm_usage, "http_get_json_response",
                    return_value=(200, {
                        "status": {
                            "indicator": "none",
                            "description": "All Systems Operational",
                        },
                        "components": [],
                    }, {})), mock.patch.object(
                    llm_usage.time, "time", return_value=1000.0):
            result = llm_usage.provider_status_snapshot("openai")
        read.assert_not_called()
        write.assert_not_called()
        self.assertTrue(result["status"]["operational"])

    def test_retry_after_header_controls_429_backoff(self):
        with mock.patch.object(
                llm_usage, "_read_cache_document",
                return_value=None), mock.patch.object(
                llm_usage, "http_get_json_response",
                return_value=(429, {"error": "limited"},
                              {"retry-after": "120"})), mock.patch.object(
                llm_usage, "_write_cache_document"), mock.patch.object(
                llm_usage.time, "time", return_value=1000.0):
            result = llm_usage.provider_status_snapshot("openai")
        self.assertTrue(result["stale"])
        self.assertIsNone(result["status"])
        self.assertEqual(result["deadline"], 1120.0)


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

    def _render_plain(self, body: dict) -> tuple[list[str], str, bool]:
        snapshot = {
            "token": {"access_token": "fixture", "expires_at_ms": None},
            "code": 200,
            "body": body,
            "age": 120.0,
            "loaded_at": 1000.0,
        }
        with mock.patch.object(llm_usage.time, "time", return_value=1010.0):
            lines, ok = llm_usage.render_openai_section(snapshot, 120)
        plain = "\n".join(llm_usage.ANSI_RE.sub("", line) for line in lines)
        return lines, plain, ok

    def test_render_includes_credits_and_labels_used_extra_limits(self):
        lines, plain, ok = self._render_plain(self.body)
        self.assertTrue(ok)
        self.assertIn("credit balance 42.5", plain)
        self.assertIn("2 usage resets", plain)
        self.assertIn("next expires", plain)
        # Spark has usage in the fixture, so it renders under a friendly name.
        self.assertIn("Codex Spark", plain)
        self.assertNotIn("GPT-5.3-Codex-Spark", plain)
        # Luna Reserve sits at 0% in the fixture, so it stays hidden.
        self.assertNotIn("Reserve", plain)
        self.assertNotIn("gpt-reserve", plain)
        self.assertTrue(all(llm_usage.visible_len(line) <= 120
                            for line in lines))

    def test_render_hides_untouched_extra_limits_and_shows_used_ones(self):
        body = json.loads(json.dumps(self.body))
        spark, reserve = body["additional_rate_limits"]
        for key in ("primary_window", "secondary_window"):
            spark["rate_limit"][key]["used_percent"] = 0
        reserve["rate_limit"]["primary_window"]["used_percent"] = 12
        _lines, plain, ok = self._render_plain(body)
        self.assertTrue(ok)
        self.assertNotIn("Spark", plain)
        self.assertIn("Luna Reserve", plain)
        self.assertNotIn("gpt-reserve", plain)

    def test_openai_limit_display_name(self):
        name = llm_usage.openai_limit_display_name
        self.assertEqual(
            name("GPT-5.3-Codex-Spark", "codex_bengalfox", None),
            "Codex Spark")
        self.assertEqual(
            name("gpt-reserve", "base_model_inference", "gpt-5.6-luna"),
            "Luna Reserve")
        self.assertEqual(
            name("gpt-reserve", "base_model_inference", None),
            "GPT Reserve")
        self.assertEqual(
            name("gpt-reserve", "base_model_inference", "gpt-5.7"),
            "GPT Reserve")
        self.assertEqual(name("Some Future Limit", "other", None),
                         "Some Future Limit")

    def test_openai_limit_in_use(self):
        in_use = llm_usage.openai_limit_in_use
        self.assertFalse(in_use({"primary_window": {"used_percent": 0},
                                 "secondary_window": None}))
        self.assertTrue(in_use({"primary_window": {"used_percent": 0},
                                "secondary_window": {"used_percent": 0.5}}))
        self.assertFalse(in_use({"primary_window": {"used_percent": "n/a"}}))
        self.assertFalse(in_use({}))

    def test_json_normalizes_current_openai_usage_shape(self):
        with mock.patch.object(
                llm_usage, "load_codex_token",
                return_value={"access_token": "fixture"}), mock.patch.object(
                llm_usage, "cached_fetch",
                return_value=(200, self.body, 120.0)), mock.patch.object(
                llm_usage.time, "time", return_value=1000.0):
            result = llm_usage._openai_json(
                service_status_snapshot("openai"))

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["windows"]["primary_window"]["reset_seconds"],
            14280.0,
        )
        self.assertEqual(
            result["windows"]["primary_window"]["window_seconds"], 18000)
        self.assertEqual(
            result["windows"]["secondary_window"]["window_seconds"], 604800)
        self.assertIn("oauth_expires_at_ms", result)
        self.assertEqual(result["credits"]["balance"], "42.5")
        self.assertEqual(
            result["rate_limit_reset_credits"]["available_count"], 2)
        self.assertTrue(
            result["rate_limit_reset_credits"]["details_complete"])
        self.assertEqual(
            result["rate_limit_reset_credits"]["next_expires_at"],
            1893456000.0,
        )
        self.assertTrue(result["service_status"]["operational"])
        self.assertEqual(len(result["additional_rate_limits"]), 2)
        extra, reserve = result["additional_rate_limits"]
        self.assertEqual(extra["name"], "GPT-5.3-Codex-Spark")
        self.assertEqual(extra["display_name"], "Codex Spark")
        self.assertEqual(extra["metered_feature"], "codex_bengalfox")
        self.assertTrue(extra["in_use"])
        self.assertEqual(
            extra["windows"]["secondary_window"]["utilization"], 7.0)
        self.assertEqual(
            extra["windows"]["secondary_window"]["window_seconds"], 604800)
        # The JSON surface keeps untouched allowances; only rendering hides them.
        self.assertEqual(reserve["name"], "gpt-reserve")
        self.assertEqual(reserve["display_name"], "Luna Reserve")
        self.assertEqual(reserve["normal_model_slug"], "gpt-5.6-luna")
        self.assertFalse(reserve["in_use"])
        self.assertEqual(list(reserve["windows"]), ["primary_window"])

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

    def test_near_reset_expiry_is_relative_and_urgency_colored(self):
        body = {
            "rate_limit_reset_credits": {
                "available_count": 1,
                "credits": [
                    {"status": "available", "expires_at": 8200},
                ],
            }
        }
        with mock.patch.object(llm_usage.time, "time", return_value=1000):
            line = llm_usage.openai_credit_status_line(body)
        plain = llm_usage.ANSI_RE.sub("", line)
        self.assertIn("next expires in 2h 00m", plain)
        self.assertIn(llm_usage.fg256(203), line)

    def test_reset_expiry_two_days_away_is_relative_and_yellow(self):
        body = {
            "rate_limit_reset_credits": {
                "available_count": 1,
                "credits": [
                    {"status": "available", "expires_at": 173800},
                ],
            }
        }
        with mock.patch.object(llm_usage.time, "time", return_value=1000):
            line = llm_usage.openai_credit_status_line(body)
        plain = llm_usage.ANSI_RE.sub("", line)
        self.assertIn("next expires in 2d 0h", plain)
        self.assertIn(llm_usage.fg256(186), line)

    def test_normalizes_app_server_reset_credit_shape(self):
        result = llm_usage.normalize_codex_reset_credits({
            "availableCount": 1,
            "credits": [{
                "id": "credit",
                "resetType": "codexRateLimits",
                "status": "available",
                "grantedAt": 100,
                "expiresAt": 200,
                "title": "Full reset",
                "description": "Fixture",
            }],
        })
        self.assertEqual(result["available_count"], 1)
        self.assertEqual(result["credits"][0]["reset_type"],
                         "codexRateLimits")
        self.assertEqual(result["credits"][0]["expires_at"], 200)

    def test_next_reset_expiry_requires_complete_non_null_details(self):
        capped = {
            "rate_limit_reset_credits": {
                "available_count": 2,
                "credits": [
                    {"status": "available", "expires_at": 2000},
                ],
            }
        }
        missing_expiry = {
            "rate_limit_reset_credits": {
                "available_count": 1,
                "credits": [
                    {"status": "available", "expires_at": None},
                ],
            }
        }
        with mock.patch.object(llm_usage.time, "time", return_value=1000):
            self.assertIsNone(
                llm_usage.openai_next_reset_credit_expiry(capped))
            self.assertIsNone(
                llm_usage.openai_next_reset_credit_expiry(missing_expiry))

    def test_fetch_codex_merges_app_server_reset_credit_details(self):
        usage = {
            "rate_limit_reset_credits": {"available_count": 1},
        }
        details = {
            "available_count": 1,
            "credits": [
                {"status": "available", "expires_at": 2000},
            ],
        }
        with mock.patch.object(
                llm_usage, "http_get_json",
                return_value=(200, usage)), mock.patch.object(
                llm_usage, "fetch_codex_reset_credit_details",
                return_value=details):
            code, body = llm_usage.fetch_codex({
                "access_token": "fixture",
                "account_id": "account",
                "source": "auth_file",
            })
        self.assertEqual(code, 200)
        self.assertEqual(body["rate_limit_reset_credits"], details)

    def test_fetch_codex_does_not_mix_environment_token_with_local_codex(self):
        usage = {
            "rate_limit_reset_credits": {"available_count": 1},
        }
        with mock.patch.object(
                llm_usage, "http_get_json",
                return_value=(200, usage)), mock.patch.object(
                llm_usage, "fetch_codex_reset_credit_details") as details:
            _code, body = llm_usage.fetch_codex({
                "access_token": "fixture",
                "account_id": None,
                "source": "environment",
            })
        details.assert_not_called()
        self.assertEqual(
            body["rate_limit_reset_credits"]["available_count"], 1)

    def test_openai_cache_expires_at_next_reset_credit_expiry(self):
        cached = {
            "ts": 900.0,
            "code": 200,
            "body": {
                "rate_limit_reset_credits": {
                    "available_count": 1,
                    "credits": [
                        {"status": "available", "expires_at": 1000.0},
                    ],
                },
            },
        }
        with mock.patch.object(
                llm_usage, "_cache_path",
                return_value="/fixture/openai.json"), mock.patch(
                "builtins.open",
                mock.mock_open(read_data=json.dumps(cached))), mock.patch.object(
                llm_usage.time, "time", return_value=1001.0):
            self.assertIsNone(llm_usage.cache_read("openai"))


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

    def test_render_includes_spend_without_redundant_limit_status(self):
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
        self.assertNotIn("7-day Fable: warning", plain)
        self.assertEqual(plain.count("7-day overall"), 1)
        self.assertEqual(plain.count("7-day Fable"), 1)
        self.assertTrue(all(llm_usage.visible_len(line) <= 120
                            for line in lines))

    def test_spend_status_hides_inactive_out_of_credits_state(self):
        body = {
            "spend": {
                "enabled": False,
                "used": {"amount_minor": 0, "currency": "USD"},
                "limit": {"amount_minor": 2000, "currency": "USD"},
                "severity": "normal",
                "disabled_reason": "out_of_credits",
                "can_purchase_credits": False,
                "can_toggle": False,
            }
        }
        self.assertIsNone(llm_usage.anthropic_extras_line(body))

    def test_spend_status_keeps_actionable_disabled_state(self):
        body = {
            "spend": {
                "enabled": False,
                "used": {"amount_minor": 0, "currency": "USD"},
                "severity": "warning",
                "disabled_reason": "payment_required",
            }
        }
        line = llm_usage.anthropic_extras_line(body)
        plain = llm_usage.ANSI_RE.sub("", line)
        self.assertIn("usage credits off", plain)
        self.assertIn("payment required", plain)
        self.assertIn("warning", plain)

    def test_json_includes_normalized_spend_and_generic_limit_metadata(self):
        with mock.patch.object(
                llm_usage, "load_anthropic_token",
                return_value={"access_token": "fixture",
                              "subscription": "max"}), mock.patch.object(
                llm_usage, "cached_fetch",
                return_value=(200, self.body, 120.0)):
            result = llm_usage._anthropic_json(
                service_status_snapshot("anthropic"))

        self.assertTrue(result["ok"])
        self.assertEqual(result["windows"]["seven_day"]["utilization"], 40.0)
        self.assertEqual(
            result["windows"]["seven_day"]["label"], "7-day overall")
        self.assertEqual(
            result["windows"]["seven_day"]["window_seconds"], 7 * 86400)
        self.assertEqual(
            result["windows"]["five_hour"]["window_seconds"], 5 * 3600)
        self.assertIn("oauth_expires_at_ms", result)
        scoped = result["windows"]["seven_day_fable"]
        self.assertEqual(scoped["kind"], "weekly_scoped")
        self.assertEqual(scoped["severity"], "warning")
        self.assertEqual(result["usage_credits"]["source"], "spend")
        self.assertTrue(result["service_status"]["operational"])
        self.assertEqual(result["usage_credits"]["used"]["amount"], 12.34)


# 2026-09-22T16:00:00Z, the start of the sanitized grant below.
GRANT_NOW = 1790092800.0


def reset_grant_block(**grant_overrides):
    """Sanitized `cedar_ember` block in the shape observed 2026-09-22."""
    grant = {
        "id": "launch-grant",
        "label": "Launch: one usage-limit reset",
        "resets_total": 1,
        "resets_left": 1,
        "starts_at": "2026-09-22T16:00:00+00:00",
        "ends_at": "2026-10-22T16:00:00+00:00",
        "clears": ["five_hour", "seven_day"],
        "paused": False,
        "usable_now": True,
        "use_requires_limit": False,
        "percent_used": {"five_hour": 1, "seven_day": 26},
        "blocking": [],
        "arm": None,
    }
    grant.update(grant_overrides)
    return {
        "eligible": True,
        "ineligible_reason": None,
        "at_limit": False,
        "exhausted": [],
        "grants": [grant],
        "next_grant_id": "launch-grant",
        "weekly_resets_at": "2026-09-28T15:00:00+00:00",
        "cooldown_until": None,
        "event_props": None,
    }


class AnthropicResetGrantTests(unittest.TestCase):
    def test_absent_or_ineligible_block_shows_nothing(self):
        self.assertIsNone(llm_usage.anthropic_reset_grants({}))
        self.assertIsNone(
            llm_usage.anthropic_reset_grants({"cedar_ember": None}))
        ineligible = {"cedar_ember": {
            "eligible": False, "ineligible_reason": "cli_version",
            "grants": [], "next_grant_id": None}}
        grants = llm_usage.anthropic_reset_grants(ineligible, now=GRANT_NOW)
        self.assertEqual(grants["available_count"], 0)
        self.assertEqual(grants["ineligible_reason"], "cli_version")
        self.assertIsNone(llm_usage.anthropic_extras_line(ineligible))

    def test_counts_available_grant_and_reports_expiry(self):
        body = {"cedar_ember": reset_grant_block()}
        grants = llm_usage.anthropic_reset_grants(body, now=GRANT_NOW)
        self.assertEqual(grants["available_count"], 1)
        self.assertEqual(grants["next_grant_id"], "launch-grant")
        self.assertTrue(grants["usable_now"])
        self.assertEqual(grants["next_expires_at"], GRANT_NOW + 30 * 86400)
        self.assertEqual(grants["next_expires_in_seconds"], 30 * 86400)
        self.assertEqual(grants["grants"][0]["clears"],
                         ["five_hour", "seven_day"])

    def test_unavailable_grants_are_not_counted(self):
        cases = {
            "paused": {"paused": True},
            "spent": {"resets_left": 0},
            "expired": {"ends_at": "2026-09-22T15:59:00+00:00"},
            "not started": {"starts_at": "2026-09-23T00:00:00+00:00"},
            "date-only end": {"ends_at": "2026-10-22"},
            "malformed end": {"ends_at": "2026-02-30T00:00:00+00:00"},
        }
        for name, overrides in cases.items():
            with self.subTest(name):
                body = {"cedar_ember": reset_grant_block(**overrides)}
                grants = llm_usage.anthropic_reset_grants(
                    body, now=GRANT_NOW)
                self.assertEqual(grants["available_count"], 0)
                self.assertIsNone(grants["next_expires_at"])

    def test_extras_line_shows_resets_with_hidden_spend(self):
        body = {
            "spend": {
                "enabled": False,
                "used": {"amount_minor": 0, "currency": "USD"},
                "severity": "normal",
                "disabled_reason": "out_of_credits",
            },
            "cedar_ember": reset_grant_block(),
        }
        with mock.patch.object(llm_usage.time, "time",
                               return_value=GRANT_NOW):
            line = llm_usage.anthropic_extras_line(body)
        plain = llm_usage.ANSI_RE.sub("", line)
        self.assertIn("1 usage reset, next expires", plain)
        self.assertNotIn("usage credits", plain)
        self.assertNotIn("usable", plain)

    def test_extras_line_explains_unusable_grant(self):
        body = {"cedar_ember": reset_grant_block(
            usable_now=False, use_requires_limit=True)}
        with mock.patch.object(llm_usage.time, "time",
                               return_value=GRANT_NOW):
            plain = llm_usage.ANSI_RE.sub(
                "", llm_usage.anthropic_extras_line(body))
        self.assertIn("usable once limited", plain)

    def test_json_exposes_reset_grants(self):
        body = {"five_hour": {"utilization": 1.0},
                "cedar_ember": reset_grant_block()}
        with mock.patch.object(
                llm_usage, "load_anthropic_token",
                return_value={"access_token": "fixture",
                              "subscription": "max"}), mock.patch.object(
                llm_usage, "cached_fetch",
                return_value=(200, body, None)), mock.patch.object(
                llm_usage.time, "time", return_value=GRANT_NOW):
            result = llm_usage._anthropic_json(
                service_status_snapshot("anthropic"))
        credits = result["rate_limit_reset_credits"]
        self.assertEqual(credits["available_count"], 1)
        self.assertEqual(credits["next_grant_id"], "launch-grant")

    def test_anthropic_cache_expires_at_next_grant_expiry(self):
        cached = {"ts": GRANT_NOW, "code": 200,
                  "body": {"cedar_ember": reset_grant_block(
                      ends_at="2026-09-22T16:05:00+00:00")}}
        for now, expired in ((GRANT_NOW + 299, False),
                             (GRANT_NOW + 301, True)):
            with self.subTest(now=now), mock.patch.object(
                    llm_usage, "_cache_path",
                    return_value="/fixture/anthropic.json"), mock.patch(
                    "builtins.open",
                    mock.mock_open(read_data=json.dumps(cached))), \
                    mock.patch.object(llm_usage.time, "time",
                                      return_value=now):
                hit = llm_usage.cache_read("anthropic")
                self.assertEqual(hit is None, expired)

    def test_anthropic_refresh_deadline_stops_at_grant_expiry(self):
        body = {"cedar_ember": reset_grant_block(
            ends_at="2026-09-22T16:05:00+00:00")}
        with mock.patch.dict(llm_usage.os.environ, {}, clear=True):
            deadline = llm_usage.next_data_refresh_deadline(
                200, 0.0, GRANT_NOW, "anthropic", body)
        self.assertEqual(deadline, GRANT_NOW + 300)

    def test_lapsed_expiry_does_not_force_refetch_every_tick(self):
        body = {"rate_limit_reset_credits": {
            "available_count": 1,
            "credits": [{"expires_at": GRANT_NOW - 10}]}}
        with mock.patch.dict(llm_usage.os.environ, {}, clear=True):
            deadline = llm_usage.next_data_refresh_deadline(
                200, 0.0, GRANT_NOW, "openai", body)
        self.assertEqual(deadline, GRANT_NOW + llm_usage.DEFAULT_CACHE_TTL)


class ProviderViewTests(unittest.TestCase):
    """Every surface renders from provider_view's display block."""

    def test_rate_limit_view_carries_retry_clock_and_no_error_line(self):
        snapshot = {"token": {"access_token": "fixture",
                              "subscription": "max"},
                    "code": 429, "body": {}, "age": 60.0,
                    "loaded_at": 1000.0}
        with mock.patch.dict(llm_usage.os.environ, {}, clear=True), \
                mock.patch.object(llm_usage.time, "time",
                                  return_value=1000.0):
            view = llm_usage.provider_view("anthropic", snapshot)
        display = view["display"]
        self.assertFalse(view["ok"])
        self.assertTrue(view["rate_limited"])
        self.assertEqual(display["plan"], "Claude Max Plan")
        self.assertIsNone(display["error"])
        notice = display["notices"][0]
        self.assertEqual(notice["text"], "● rate limited (429), retry in ")
        self.assertEqual(notice["seconds"],
                         llm_usage.DEFAULT_RATE_LIMIT_TTL - 60.0)
        self.assertEqual(notice["counting"], "down")

    def test_openai_view_rows_apply_visibility_rule_once(self):
        body = json.loads(OPENAI_FIXTURE_PATH.read_text())
        snapshot = {"token": {"access_token": "fixture"}, "code": 200,
                    "body": body, "age": 120.0, "loaded_at": 1000.0}
        with mock.patch.object(llm_usage.time, "time", return_value=1010.0):
            view = llm_usage.provider_view("openai", snapshot)
        rows = view["display"]["rows"]
        headings = [r["text"] for r in rows if r["type"] == "heading"]
        self.assertEqual(headings, ["Codex Spark"])
        labels = [r["label"] for r in rows if r["type"] == "row"]
        self.assertIn("7-day (weekly)", labels)
        # The JSON surface still lists every group for other consumers.
        self.assertEqual(len(view["additional_rate_limits"]),
                         len(body["additional_rate_limits"]))
        for r in rows:
            if r["type"] == "row":
                self.assertIn(r["pace"], ("over", "under", "on", None))
                self.assertEqual(r["severity"],
                                 llm_usage.severity_tone(r["utilization"]))

    def test_segment_clock_advances_in_the_declared_direction(self):
        down = llm_usage.seg("retry in ", "warn", 600)
        up = llm_usage.seg("cached ", "dim", 600, "up", " ago")
        self.assertEqual(llm_usage.seg_text(down, elapsed=120), "retry in 8m")
        self.assertEqual(llm_usage.seg_text(up, elapsed=120), "cached 12m ago")

    def test_fetch_requests_grants_with_installed_cli_version(self):
        with mock.patch.object(
                llm_usage.shutil, "which",
                return_value="/home/u/.local/bin/claude"), mock.patch.object(
                llm_usage.os.path, "realpath",
                return_value="/home/u/.local/share/claude/versions/9.8.7"), \
                mock.patch.object(llm_usage, "http_get_json",
                                  return_value=(200, {})) as get:
            llm_usage.fetch_anthropic({"access_token": "fixture"})
        url, headers = get.call_args[0]
        self.assertTrue(url.endswith("/api/oauth/usage?cedar_ember=1"))
        self.assertEqual(headers["User-Agent"],
                         "claude-cli/9.8.7 (external, cli)")

    def test_cli_version_falls_back_for_unversioned_installs(self):
        with mock.patch.object(
                llm_usage.shutil, "which",
                return_value="/usr/local/bin/claude"), mock.patch.object(
                llm_usage.os.path, "realpath",
                return_value="/usr/local/lib/node_modules/claude/cli.js"):
            self.assertEqual(llm_usage.claude_cli_version(),
                             llm_usage.CLAUDE_CLI_VERSION_FALLBACK)
        with mock.patch.object(llm_usage.shutil, "which", return_value=None):
            self.assertEqual(llm_usage.claude_cli_version(),
                             llm_usage.CLAUDE_CLI_VERSION_FALLBACK)


class VersionTests(unittest.TestCase):
    def test_version_flag_prints_version_without_fetching(self):
        with mock.patch.object(llm_usage.sys, "argv",
                               ["llm-usage", "--version"]), mock.patch.object(
                llm_usage, "render_dashboard") as render, mock.patch(
                "builtins.print") as printed:
            llm_usage.main()
        render.assert_not_called()
        printed.assert_called_once_with(
            f"llm-usage {llm_usage.__version__}")
        self.assertRegex(llm_usage.__version__, r"^\d+\.\d+\.\d+$")


class HtmlOutputTests(unittest.TestCase):
    PAYLOAD = {
        "anthropic": {"ok": True, "windows": {}},
        "openai": {"ok": True, "windows": {}},
        "generated_at": 1000.0,
    }

    def test_build_html_page_embeds_payload_mode_and_poll_interval(self):
        payload = {
            "anthropic": {"ok": False,
                          "error": "</script><script>alert(1)",
                          "windows": {}},
            "openai": {"ok": True, "windows": {}},
            "generated_at": 1000.0,
        }
        page = llm_usage.build_html_page(payload, "static")
        self.assertIn('data-mode="static"', page)
        self.assertIn('"generated_at": 1000.0', page)
        self.assertIn("<\\/script><script>alert(1)", page)
        self.assertNotIn("</script><script>alert(1)", page)
        live = llm_usage.build_html_page(payload, "live")
        self.assertIn('data-mode="live"', live)
        self.assertIn(
            f"const POLL_MS = {llm_usage.HTML_POLL_SECONDS} * 1000", live)

    def test_usage_payload_runs_cli_json_and_honors_fresh(self):
        completed = mock.Mock(
            stdout=json.dumps({"anthropic": {"ok": True},
                               "openai": {"ok": False}}),
            returncode=1)
        with mock.patch.object(llm_usage.subprocess, "run",
                               return_value=completed) as run:
            payload = llm_usage.usage_payload(fresh=True)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[2:], ["--json", "--fresh"])
        self.assertTrue(payload["anthropic"]["ok"])
        self.assertIn("generated_at", payload)
        self.assertEqual(llm_usage.payload_exit_code(payload), 1)

        with mock.patch.object(llm_usage.subprocess, "run",
                               return_value=completed) as run:
            llm_usage.usage_payload()
        self.assertNotIn("--fresh", run.call_args.args[0])

    def test_usage_payload_failure_degrades_both_providers(self):
        with mock.patch.object(llm_usage.subprocess, "run",
                               side_effect=OSError("no interpreter")):
            payload = llm_usage.usage_payload()
        self.assertFalse(payload["anthropic"]["ok"])
        self.assertFalse(payload["openai"]["ok"])
        self.assertIn("usage snapshot failed", payload["openai"]["error"])
        self.assertIn("generated_at", payload)
        self.assertEqual(llm_usage.payload_exit_code(payload), 2)

    def test_parse_html_flags(self):
        self.assertEqual(llm_usage.parse_html_flags(["--html"]),
                         (True, None, None))
        self.assertEqual(
            llm_usage.parse_html_flags(["--html-static", "out.html"]),
            (False, "out.html", None))
        _html, static_path, error = llm_usage.parse_html_flags(
            ["--html-static"])
        self.assertIsNone(static_path)
        self.assertIn("output path", error)
        _html, static_path, error = llm_usage.parse_html_flags(
            ["--html-static", "--fresh"])
        self.assertIsNone(static_path)
        self.assertIn("output path", error)

    def test_parse_port_flag(self):
        self.assertEqual(llm_usage.parse_port_flag(["--html"]), (None, None))
        self.assertEqual(
            llm_usage.parse_port_flag(["--html", "--port", "17398"]),
            (17398, None))
        for bad in (["--port"], ["--port", "abc"], ["--port", "0"],
                    ["--port", "65536"]):
            port, error = llm_usage.parse_port_flag(bad)
            self.assertIsNone(port)
            self.assertIn("--port", error)

    def test_run_html_server_fails_hard_when_port_is_taken(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Match the server's wildcard address; a loopback-only listener
            # can coexist with a wildcard listener on some platforms.
            blocker.bind(("0.0.0.0", 0))
            blocker.listen(1)
            taken_port = blocker.getsockname()[1]
            with mock.patch.object(llm_usage.webbrowser, "open") as opened, \
                    mock.patch.object(llm_usage.sys, "stderr"), \
                    mock.patch.object(llm_usage.ThreadingHTTPServer,
                                      "serve_forever",
                                      side_effect=AssertionError("unexpected bind success")):
                exit_code = llm_usage.run_html_server(False, port=taken_port)
        finally:
            blocker.close()
        self.assertEqual(exit_code, 2)
        opened.assert_not_called()

    def test_write_html_static_writes_snapshot_page(self):
        import tempfile
        with mock.patch.object(llm_usage, "usage_payload",
                               return_value=dict(self.PAYLOAD)):
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "nested" / "usage.html"
                with mock.patch("sys.stdout"):
                    exit_code = llm_usage.write_html_static(str(out), False)
                page = out.read_text()
        self.assertEqual(exit_code, 0)
        self.assertIn('data-mode="static"', page)
        self.assertIn('"generated_at"', page)

    def test_html_server_serves_dashboard_and_data(self):
        server = llm_usage.create_html_server(
            lambda: dict(self.PAYLOAD), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            self.assertEqual(server.server_address[0], "0.0.0.0")
            with urllib.request.urlopen(base + "/", timeout=5) as r:
                page = r.read().decode()
            self.assertIn('data-mode="live"', page)
            self.assertIn("llm-usage", page)
            with urllib.request.urlopen(base + "/data.json", timeout=5) as r:
                data = json.loads(r.read().decode())
            self.assertTrue(data["anthropic"]["ok"])
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(base + "/other", timeout=5)
            self.assertEqual(ctx.exception.code, 404)
            ctx.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
