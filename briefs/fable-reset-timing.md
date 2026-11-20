# Bayesian timing for Fable-specific usage resets

**Research and design brief · 2026-11-20 · No implementation**

**Question:** Given a single `llm-usage` snapshot, is spending one scarce Fable reset **now** better than waiting and retaining the option to reset later? Optimize useful work completed and interruptions avoided, not the number of points the meter gives back. For this decision, model weekly limits only.

## Provider mechanics

- **Verified for the included Fable allowance:** Fable usage counts against the all-model weekly limit and has a nested cap of 50% of that limit on the applicable plans. The two display percentages have different denominators. A Fable reset restores the Fable usage already spent in both weekly meters, leaves other-model usage in place, and does not change the natural renewal time or the caps. Eligible users can redeem before hitting a limit; Anthropic describes restoration as immediate, not a guarantee a user will be available to redeem or that the dashboard observes the change immediately. See [Fable models on your plan](https://support.claude.com/en/articles/15424964-claude-fable-models-on-your-plan), [Fable 5.2 launch offers](https://support.claude.com/en/articles/17506494-claude-fable-5-2-launch-offers), and [What is a limit reset?](https://support.claude.com/en/articles/17007452-what-is-a-limit-reset).
- **Inventory and expiry:** the premium launch offer grants one to three resets by tenure, expiring March 8, 2027 at 11:59 p.m. Pacific (March 9, 12:59 a.m. MST). The short Pro/standard Team promotion ended November 16, 2026. Use actual per-grant timestamps and eligibility from the account, not promo dates as implementation defaults. A grant is spent once; renewal does not replenish it. Redemption does not refund usage-credit spend.
- **Scope:** exclude the five-hour meter and its refill from this model. Anthropic explicitly excludes that limit for [verified Ultra](https://support.claude.com/en/articles/17728952-what-is-the-ultra-plan) but documents it for accounts that still have it; do not infer account tier from a screenshot or remove unrelated dashboard rows. Cases with an unverified cap ratio, inapplicable billing arrangement, or incompatible weekly boundaries should abstain rather than convert percentages using a guess.

## Snapshot example

Use units of one full all-model weekly allowance. Let `w` and `f` be the displayed all-model and Fable fractions, `c` the Fable cap in overall units, and `x = c × f` the Fable usage. Other-model usage is `y = w − x`. A Fable reset changes `(x,y)` to `(0,y)`; it uses one grant. Before reset Fable headroom is `min(c−x, 1−x−y)`; after reset it is `min(c, 1−y)`. These are capacity limits, not estimates of demand.

At **51% overall, 59% Fable, about 60% elapsed, 67 hours left, two resets**, with verified `c = 0.50`: `x ≈ 0.295`, `y ≈ 0.215`. Redeeming restores **29.5 percentage points of overall capacity** and takes the Fable meter to zero. It raises available Fable work from 20.5 to 50 overall percentage points if nothing else is consumed. At uniform historical pace Fable would finish around 98–99% of its cap, before accounting for rounding or demand changes. On that scenario the reset buys no extra work this week. A burst or deadline could change that; the snapshot alone does not justify a numerical confidence or an unconditional “reset now.”

## Decision model

State `s = (t, T, x, y, G, B, q, a)`: current time, weekly renewal, both usage components, individually expiring grants `G`, belief `B` over the latent demand process and meter error, work backlog/deadlines `q`, and operator availability `a`. Advance on meaningful events: arrivals, likely cap approach, observation/redeem opportunities, grant expiries and renewals. Natural renewal zeros weekly usage, but preserves unexpired grants. If meter clocks unexpectedly differ, abstain in the first version. Honor grant flags, cooling-off times, unknown eligibility, and in-flight work. Do not assume the client can interrupt an arbitrary request midflight.

Fit joint Fable/non-Fable latent demand in allowance units, not messages or tokens. A practical starting prior is a mixture of quiet, normal and burst regimes, with a shared activity factor plus model-specific variation, time-of-week weights, and optional scheduled workload. One snapshot gives a noisy aggregate to the observation time, not durations, burst timing, use under blocked limits, future availability, or which regimes will hold. Condition on rounding intervals and freshness, and treat capped or reset-discontinuous observations as censored or segmented. Start with transparent broad scenario ranges and sensitivity analysis; do not call handpicked scenario weights empirically calibrated. Optional opt-in local, sanitized history of meter deltas, renewals, redemptions and observation times can update the model and support out-of-sample calibration. Past meter deltas alone cannot recover work that users abandoned or switched away from after a cap.

Use reward `r` for completed useful Fable and other work by deadline, less interruption, missed deadline and redemption friction costs. Explicitly parameterize model substitution or usage credits if included; the baseline assumes neither. Either model through grant expiry across renewals or use a terminal continuation value for unused grants learned/estimated from held-out weeks; do not also charge the same future opportunity cost in the reward.

```text
V(s) = max over feasible a in {RESET, WAIT} of E[r(s,a,s') + V(s') | B]
Q_now  = E[total utility | reset now, then follow the policy]
Q_wait = E[total utility | skip this decision opportunity, then follow the WAIT policy]
Delta  = Q_now − Q_wait
```

WAIT is **not** “never reset”: its policy may redeem at the next real opportunity just before or after a binding cap, later this week, or in a later week before expiry. Do not compare payoffs against a policy that sees the future. State the decision grid or event cadence; reducing the wait interval to zero makes the comparison degenerate. With costless, immediate redemption at any needed moment, continuous observation and no expiry or in-flight interruption before that moment, waiting can preserve at least as much value in the one-cycle, one-grant case: it lets more consumed Fable work accrue before reclaiming it. Less remaining time after a late reset does **not** by itself favor early reset. Early reset can win when delay risks a blocked long task or missed deadline, the operator might be absent at the cap or expiry, or a real future redemption opportunity is scarce.

For a small prototype simulate demand and observations forward, using a discretized belief-state DP or a few transparent threshold WAIT policies tuned on training simulations and evaluated out of sample. A threshold approximation is not a proven optimum; identify the strongest evaluated comparator. Couple RESET and WAIT on the same latent demand, availability and model parameters but let each policy observe only its own history:

```text
train candidate continuation policies on seeded training simulations
for each held-out posterior/scenario draw:
    simulate forced RESET now, then continuation
    simulate forced WAIT at this opportunity, then continuation
    record paired utility difference D and any cap/interruption under each
report E[D], predictive interval of D, P(D > delta), and binding-cap risks
repeat under plausible alternate demand, availability and provider assumptions
```

Here `delta` is the smallest useful gain, expressed in reward units. The predictive interval is uncertainty in the **paired outcome**, not a confidence interval for the posterior mean; report numerical simulation error separately if meaningful. For a calibrated model, a proposed rule is RESET only if available and both `E[D] > delta` and `P(D > delta) ≥ pMin` (e.g. 80% as an operator-selected starting threshold, not a fitted constant), and the sign survives plausible alternative priors. Clearly label prior-conditional numbers. If results change sign or the source data are stale/incomplete, report “not enough evidence,” with the determining assumptions. If waiting provides similar value and keeps a grant, prefer waiting. Track cap-hit probability as an explanation, not the action criterion.

## Integration, if approved later

Keep the executable **stdlib-only Python**. The TypeScript below describes the JSON boundary, not a new runtime; no decision logic in browser rendering. Adapt the normalized Anthropic `windows` (utilization, kind, reset timestamp), cache age/rate-limit state and `rate_limit_reset_credits.grants` (scope, starts/ends, remaining, paused, usability). Normalize using the provider's existing scope rules, not raw grant `type` or a localized label alone. Treat missing data and fallback zeros as unknown, not as real zero use. The model receives an explicit UTC `observedAt` and uses absolute renewals/expiries, not the advancing display countdown. An old cached response cannot prove current redemption eligibility. Do not poll more often or call redemption endpoints; keep all redemption in Claude.

```ts
type ResetInput = {
  observedAt: string; evaluatedAt: string; renewsAt: string;
  overallUsed: number; fableUsed: number; fableCapOfOverall: number;
  grants: { remaining: number; startsAt?: string; endsAt: string;
            usableNow: boolean; paused: boolean; requiresLimit: boolean }[];
  cooldownUntil?: string; evidence: "fresh" | "cached" | "incomplete";
};
type ResetAdvice = {
  action: "reset-now" | "wait" | "insufficient-evidence";
  reason: string; modelVersion: string; calibrated: boolean;
  restoredOverallFraction?: number;
  incrementalUtility?: { mean: number; p10: number; p90: number; pAboveDelta: number };
  comparison?: string; reconsiderAt?: string;
};
```

Add optional advice in `provider_view` next to Fable-specific banked resets, with basis/freshness and what would change the recommendation. Do not change the existing dashboard on this brief alone. With no opted-in history or credible workload priors, ship a scenario explanation rather than a percentage. Later backtests should use time-ordered holdouts, measure reliability for cap hits and any observable outcomes, and compare regret against WAIT-until-needed and never-redeem baselines. Realized counterfactual utility is not directly observable.

## Deterministic proof cases

| Fixture | Expected |
| --- | --- |
| Snapshot above, fresh, fixed `c=0.5` | Reset restores `0.295` overall and leaves `0.215`; no expected-value claim from the meter conversion. |
| Fixed sub-cap workload all week, grant valid next week | WAIT. No extra work now; retains future option. |
| Frictionless redeem at the cap, grant valid, pending work | The WAIT comparator may redeem and complete the work itself; do not credit RESET with fictitious extra throughput. |
| Known indivisible Fable burst before next opportunity, deadline before renewal; reset makes it fit | RESET if saved work exceeds grant continuation value. |
| Overall cap binds first from non-Fable mix | Check both limits; a Fable reset retains all non-Fable spend and is not a full reset. |
| Grant expires before next modeled opportunity, or is paused/nonusable | Expired grants lose all continuation value; current ineligible grants are never offered. Distinguish missing eligibility from confirmed unavailable. |
| CLOCK: natural renewal occurs first, grant remains valid | Clear usage only; retain the grant for future evaluation. |
| Rounding-inconsistent meters, unknown cap/scope, stale unverified eligibility | No reset recommendation. Do not silently clamp incompatible input into a plausible snapshot. |

Use fixed timestamps, fixed workload paths, and seeded simulation where necessary. For user-facing advice, assert complete deterministic rendered text; test transitions and utility calculations separately.
