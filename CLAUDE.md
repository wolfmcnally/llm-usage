# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file, stdlib-only Python 3 terminal dashboard (`llm-usage`) that displays Anthropic (Claude) and OpenAI (ChatGPT/Codex) subscription rate-limit usage — the same numbers Claude Code's `/usage` and the Codex CLI report. There is no build system, no dependencies, no test suite. The entire project is the one executable script.

`~/bin/llm-usage` is a symlink into this directory, so edits to the script are live immediately on the PATH.

## Running

```sh
./llm-usage          # renders both provider sections
echo $?              # 0 = both OK, 1 = one provider failed, 2 = both failed
```

```sh
./llm-usage --fresh   # bypass the cache for this run, hit the APIs live
./llm-usage --json     # machine-readable snapshot (also cached)
```

There is no lint/test command. Verify changes by running the script directly. Note: the Anthropic endpoint occasionally returns a transient 429; that renders as an inline section error and exit code 1, not a bug.

## Caching

To avoid hammering the undocumented (rate-limited) usage endpoints, every **successful** (HTTP 200) response is cached on disk per provider under `$XDG_CACHE_HOME/llm-usage` (default `~/.cache/llm-usage/{anthropic,openai}.json`). Any run within the TTL — default **600s (10 minutes)** — is served from that cache with **no network call**, so the real APIs are hit at most once every ten minutes per provider regardless of how often the script runs.

- **Failures are never cached** (`cached_fetch` only writes on `code == 200`), so a transient 429/network error retries on the very next run instead of being pinned for the whole window.
- Cache served? The TUI prints a dim `↻ cached Nm ago` line under each section; `--json` adds `cached` (bool) and `cache_age_seconds` to each provider object.
- **Overrides:** `LLM_USAGE_CACHE_TTL=<seconds>` (0 disables), or `--fresh` / `--no-cache` / `LLM_USAGE_NO_CACHE=1` to bypass for one run (still refreshes the cache on success).
- Writes are atomic (`os.replace` of a pid-suffixed temp file) and entirely best-effort — cache I/O errors never break a render. Both the TUI and `--json` paths funnel through `cached_fetch`.

## Hard constraints

- **Stdlib only.** No third-party imports, ever. The provider SDKs do not expose these usage endpoints; the script calls them directly with `urllib`, mimicking each vendor CLI's headers (see `fetch_anthropic` / `fetch_codex` — the `User-Agent`, `anthropic-beta`, `Originator`, and `Chatgpt-Account-Id` headers are required by the endpoints, not decoration).
- **Python 3.8+ compatible.** `from __future__ import annotations` makes the `X | None` annotations safe on older runtimes; don't introduce syntax newer than walrus operators.

## Architecture

Top-to-bottom pipeline, one section per provider, each failing independently:

1. **Token loaders** (`load_anthropic_token`, `load_codex_token`) — chain of sources: env var → macOS Keychain (`security find-generic-password`, Anthropic only) → credential file (`~/.claude/.credentials.json` / `~/.codex/auth.json`). Codex token expiry comes from locally decoding the access token's JWT `exp` claim (`jwt_claims` — unverified by design, inspection only).
2. **Fetchers** (`fetch_anthropic`, `fetch_codex`) — GET the undocumented usage endpoints (`api.anthropic.com/api/oauth/usage`, `chatgpt.com/backend-api/wham/usage`) via `http_get_json`, which never raises: it returns `(status_or_None, body)`.
3. **Sections** (`anthropic_section`, `openai_section`) — each returns a success bool and degrades any failure (no token, 401, API error) to an inline `warn_line` so the other section still renders. The two providers' response shapes differ: Anthropic returns named bands (`five_hour`, `seven_day`, `seven_day_opus`, …) with `utilization` + `resets_at` ISO timestamps; OpenAI returns `rate_limit.{primary,secondary}_window` with `used_percent` + window/reset seconds.
4. **Row renderer** (`row`) — the core visual idea: each band is a 3-line block where line 0 places a `▼` at the percent-of-window-elapsed position above a usage bar (line 1), so bar-end vs. `▼` position *is* the over/under-pace signal. Pace verdict (±5pp threshold) colors both the `▼` and the trailing annotation.

The trailing `__main__` block deliberately handles `BrokenPipeError` (exit 141) including the interpreter's exit-time stdout flush — preserve that if touching `main()`.
