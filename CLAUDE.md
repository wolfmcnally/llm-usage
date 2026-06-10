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
./llm-usage --tui      # interactive curses view with resize + vertical scroll
```

`--tui` requires an interactive terminal and exits 2 when stdout is not a TTY.
`--tui --json` is invalid and exits 2. In TUI mode, `--fresh` bypasses cache
reads only for the initial data load; subsequent refreshes are cache-aware.

TUI controls: `↑`/`k`, `↓`/`j`, `PageUp`, `PageDown`, `Home`, `End`, mouse wheel
when supported, and `q`/`Esc`/`Ctrl-C` to quit. The TUI refetches data when
cache/backoff deadlines expire, repaints every 60 seconds for countdowns and
cache ages, and redraws without fetching on resize or scroll.

There is no lint/test command. Verify changes by running the script directly. Note: the Anthropic endpoint occasionally returns a transient 429; that renders as an inline section error and exit code 1, not a bug.

## Caching

To avoid hammering the undocumented (rate-limited) usage endpoints, responses are cached on disk per provider under `$XDG_CACHE_HOME/llm-usage` (default `~/.cache/llm-usage/{anthropic,openai}.json`). Two TTLs apply, keyed off the cached HTTP status (`cache_read` picks the TTL by the stored `code`):

- **Success (HTTP 200)** → cached for `cache_ttl()`, default **600s (10 min)**. Any run within the window is served with **no network call**, so the real APIs are hit at most once every ten minutes per provider.
- **Rate-limit (HTTP 429)** → **negatively** cached for `rate_limit_ttl()`, default **1200s (20 min)**. Once a provider returns 429, `cached_fetch` writes that 429 and *no new call* is made for the backoff window — re-running the tool won't keep poking a limited endpoint. The section reports the backoff and time remaining instead.
- **Any other failure** (401, 5xx, network `None`) is **never** cached (`cached_fetch` only writes on `code in (200, 429)`), so it retries on the very next run.

Display / output:
- TUI: a dim `↻ cached Nm ago` line for a cached 200; an orange `● rate limited (429) … retry in Nm` line (`rate_limit_warn`) for a cached or live 429.
- `--json`: each provider object carries `cached` (bool), `cache_age_seconds`, and `rate_limited` (bool, present on the error path).

Overrides:
- `LLM_USAGE_CACHE_TTL=<seconds>` — success window; `0` (or ≤0) disables **all** caching (success *and* negative).
- `LLM_USAGE_RATE_LIMIT_TTL=<seconds>` — 429 backoff window; independent of the success TTL.
- `--fresh` / `--no-cache` / `LLM_USAGE_NO_CACHE=1` — bypass cache reads for one run (still refreshes the cache on a 200 or 429).

Writes are atomic (`os.replace` of a pid-suffixed temp file) and entirely best-effort — cache I/O errors never break a render. Both the TUI and `--json` paths funnel through `cached_fetch`.

## Hard constraints

- **Stdlib only.** No third-party imports, ever. The provider SDKs do not expose these usage endpoints; the script calls them directly with `urllib`, mimicking each vendor CLI's headers (see `fetch_anthropic` / `fetch_codex` — the `User-Agent`, `anthropic-beta`, `Originator`, and `Chatgpt-Account-Id` headers are required by the endpoints, not decoration).
- **Python 3.8+ compatible.** `from __future__ import annotations` makes the `X | None` annotations safe on older runtimes; don't introduce syntax newer than walrus operators.

## Architecture

Top-to-bottom pipeline, one section per provider, each failing independently:

1. **Token loaders** (`load_anthropic_token`, `load_codex_token`) — chain of sources: env var → macOS Keychain (`security find-generic-password`, Anthropic only) → credential file (`~/.claude/.credentials.json` / `~/.codex/auth.json`). Codex token expiry comes from locally decoding the access token's JWT `exp` claim (`jwt_claims` — unverified by design, inspection only).
2. **Fetchers** (`fetch_anthropic`, `fetch_codex`) — GET the undocumented usage endpoints (`api.anthropic.com/api/oauth/usage`, `chatgpt.com/backend-api/wham/usage`) via `http_get_json`, which never raises: it returns `(status_or_None, body)`.
3. **Snapshots + sections** (`anthropic_snapshot` / `openai_snapshot`, then `render_anthropic_section` / `render_openai_section`) — each provider fetch/load happens once per data refresh, then renders independently and degrades any failure (no token, 401, API error) to an inline `warn_line` so the other section still renders. The two providers' response shapes differ: Anthropic returns named bands (`five_hour`, `seven_day`, `seven_day_opus`, …) with `utilization` + `resets_at` ISO timestamps; OpenAI returns `rate_limit.{primary,secondary}_window` with `used_percent` + window/reset seconds.
4. **Row renderer** (`row`) — the core visual idea: each band is a 3-line block where line 0 places a `▼` at the percent-of-window-elapsed position above a usage bar (line 1), so bar-end vs. `▼` position *is* the over/under-pace signal. Pace verdict (±5pp threshold) colors both the `▼` and the trailing annotation.

The render path now uses provider snapshots: data fetch/load happens once, then
`render_dashboard_snapshot` can repaint the same snapshot at a new width or a
later time without another provider/cache read. Preserve that boundary for TUI
resize, scroll, and 60-second repaint behavior.

The trailing `__main__` block deliberately handles `BrokenPipeError` (exit 141) including the interpreter's exit-time stdout flush — preserve that if touching `main()`.
