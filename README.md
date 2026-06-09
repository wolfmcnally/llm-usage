# llm-usage

A terminal dashboard for your **Anthropic (Claude)** and **OpenAI (ChatGPT/Codex)** subscription rate limits — the same numbers Claude Code's `/usage` command and the Codex CLI report, side by side, sized to your terminal.

```text
Used: █ ok  █ 50%  █ 75%  █ 90%
Pace: ▼ under  ▼ on  ▼ over

ANTHROPIC  Claude MAX plan                                   Wed Jun 03 13:51 MDT
──────────────────────────────────────────────────────────────────────────────────
  OAuth token expires in 7h 19m (Wed Jun 03 21:11 MDT)

                                          ▼
7-day overall     ███████████████████████████████░░░░░░░░░░░░░░░░░░░░░░░    57%
                  resets Sun Jun 07 10:02 MDT  ·  in 3d 20h  ·  window 45% elapsed   ↑ +12pp over pace
                  ▼
5-hour            ████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░    12%
                  resets Wed Jun 03 18:02 MDT  ·  in 4h 10m  ·  window 16% elapsed   ↓ -4pp under pace

══════════════════════════════════════════════════════════════════════════════════

OPENAI  ChatGPT Pro plan                                     Wed Jun 03 13:52 MDT
──────────────────────────────────────────────────────────────────────────────────
  ...
```

## Reading the display

Each rate-limit band is a bar with a marker above it:

- **The bar** shows what percentage of the limit you've consumed, colored green → yellow → orange → red as it fills.
- **The `▼` marker** sits at the percentage of the *time window* that has elapsed — i.e., where "now" is.

The relationship between the two is the real signal:

| Pattern | Meaning |
|---|---|
| Bar ends **past** the `▼` (red `▼`, `↑ over pace`) | Burning faster than the window — you'll hit the limit before it resets |
| Bar ends **before** the `▼` (green `▼`, `↓ under pace`) | Headroom — usage is slower than time |
| Bar ends **at** the `▼` (white `▼`, `≈ on pace`) | Within ±5 percentage points of even burn |

Each band also shows its reset time (local), time remaining, and window-elapsed percentage. A red `↑` after a bar means usage reported over 100%.

OAuth token expiry is shown for both providers, with escalating warnings inside 7 days and inside 1 hour.

## Installation

Copy (or symlink) the script anywhere on your `PATH`:

```sh
ln -s "$PWD/llm-usage" ~/bin/llm-usage
```

Requirements: **Python 3.8+**, nothing else — no third-party packages.

## Authentication

The tool reuses the OAuth credentials your existing CLI logins already created. No separate setup is needed if you use Claude Code and/or the Codex CLI.

**Anthropic** (first match wins):
1. `$CLAUDE_CODE_OAUTH_TOKEN`
2. macOS Keychain entry `Claude Code-credentials`
3. `~/.claude/.credentials.json`

**OpenAI/Codex** (first match wins):
1. `$CODEX_OAUTH_TOKEN`
2. `~/.codex/auth.json`

If a token is missing or rejected, run `claude` or `codex` once to log in / refresh, then re-run `llm-usage`.

Tokens are read locally and sent only to the respective vendor's own API. OpenAI token expiry is determined by decoding the JWT locally (inspection only — never verified, never sent anywhere else).

## Caching

The undocumented usage endpoints are rate-limited, so `llm-usage` calls each provider's API **at most once every ten minutes**. Every successful response is cached on disk per provider; any run inside the TTL is served from cache with no network call and annotated `↻ cached Nm ago` under the section.

- **Success TTL:** 600 seconds (10 minutes) by default. Override with `LLM_USAGE_CACHE_TTL=<seconds>` (set `0` to disable caching entirely).
- **Rate-limit backoff:** if a provider returns **429**, that's *negatively* cached for **20 minutes** by default — the tool makes **no new call** to that provider for the whole window, so repeatedly running it won't keep poking a limited endpoint and prolong the block. The section shows `● rate limited (429) … retry in Nm` instead. Override with `LLM_USAGE_RATE_LIMIT_TTL=<seconds>`.
- **Bypass for one run:** `llm-usage --fresh` (alias `--no-cache`, or `LLM_USAGE_NO_CACHE=1`) hits the APIs live and refreshes the cache — including overriding an active 429 backoff.
- **Other failures aren't cached** — a 401/network error retries on your next run rather than sticking around.
- **Location:** `$XDG_CACHE_HOME/llm-usage/` (default `~/.cache/llm-usage/`).
- The `--json` output includes `cached`, `cache_age_seconds`, and `rate_limited` per provider.

## Failure behavior

Each provider section renders independently: a missing token, expired credential, network failure, or API error in one section degrades to an inline error line while the other section still renders fully. (The Anthropic usage endpoint occasionally returns a transient 429 — just re-run.)

**Exit codes:** `0` both providers OK · `1` one provider failed · `2` both failed · `130` interrupted · `141` broken pipe (e.g. piped to `head`).
