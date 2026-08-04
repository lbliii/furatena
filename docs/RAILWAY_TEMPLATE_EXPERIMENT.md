# Railway template 90-day experiment

Status: pre-launch plan; collection is blocked on publication in #458

This runbook fixes the hypotheses, privacy boundary, support expectations, and
day-90 decision rules before marketplace results exist. The machine-checked
policy is [`config/railway-template-experiment.json`](../config/railway-template-experiment.json).
It contains no observed values. Snapshot records stay in access-controlled
maintainer storage and must not be committed, attached to public issues, or
copied into support messages.

## Provider boundary

As checked on 2026-08-04, Railway's public documentation says its template
dashboard exposes aggregate deployments, earnings, and support health. It does
not currently promise project-level retention, deployment-success, or active-use
metrics, and its extended metrics section is marked under construction. Record
an unavailable field as unavailable; never infer it from an identity, endpoint,
or private project.

Railway also documents that published marketplace templates are eligible for
usage-based kickbacks, active Template Queue support affects support-bonus
eligibility, and earnings default to Railway credits unless a maintainer changes
the account setting. Rates and payout rules are provider policy, not Furatena
configuration: re-check the source at every review and never copy a payout
account, withdrawal, Stripe Connect record, or cash-versus-credit choice into
this repository.

Sources:

- [Railway template metrics](https://docs.railway.com/templates/metrics)
- [Railway template kickbacks and Template Queue](https://docs.railway.com/templates/kickbacks)
- [Railway template publication](https://docs.railway.com/templates/publish-and-share)
- [GitHub repository traffic](https://docs.github.com/en/repositories/viewing-activity-and-data-for-your-repository/viewing-traffic-to-a-repository)

GitHub exposes repository traffic for the past 14 days. Capture only aggregate
views, unique visitors, and clones every seven days so the day-30, day-60, and
day-90 reviews can be assembled without retaining referrer paths, popular
content, or visitor identities. Repository issue and fork counts may be captured
at the four review points because they are cumulative public aggregates.

## Start gate and immutable hypotheses

Day 0 begins only after #458 records a published template code and URL, public
demo, publication date, exact image version and digest, and passing clean-account
conformance. Before that moment the maintainer privately records the accountable
support owner and coverage owner. Do not put private names or account details in
the policy or snapshot.

The experiment tests three fixed hypotheses:

1. **Repeatable adoption:** by day 90 the aggregate dashboard shows at least two
   deployments, at least one privacy-safe active-use signal is observable, and
   realized earnings are positive.
2. **Supportable operation:** every observed Template Queue question is answered,
   acknowledgements meet the one-business-day expectation, useful responses
   meet the two-business-day expectation, and the queue does not miss the
   acknowledgement expectation in two consecutive review windows.
3. **Reliable delivery:** the public demo and supported deployment, refresh,
   update, and rollback paths remain conformant; no visibility, credential, or
   private-content boundary is violated.

Do not revise these hypotheses or their thresholds after day 0. Day-30 and
day-60 changes are interventions, recorded separately with their reason and
expected effect.

## Collection and privacy

Capture snapshots at day 0, 30, 60, and 90. Also capture the bounded GitHub
traffic aggregates every seven days. Each source gets an observation timestamp
and one of `observed`, `unavailable`, or `not_applicable`; zero and unavailable
are never interchangeable.

The allowlist in the policy covers aggregate Railway metrics, aggregate support
timing/counts, controlled failure-theme counts, public repository aggregates,
public runtime conformance, and categorical product-fit signals. Qualitative
evidence is a maintainer paraphrase assigned to a controlled theme. Never retain
support text, quotes, user or project identity, private repository or endpoint,
deployment identifiers, credentials, authored queries, response bodies, or
payout data.

Record earnings and operator time only in the private snapshot. The public
day-90 decision may say `positive`, `zero`, or `not_observable` earnings and
whether support burden was sustainable; it must not publish amounts, hourly
rates, internal cost, scale, or account configuration.

## Support and incidents

The stable-image promoter owns the queue; the repository owner is escalation
owner. A question is acknowledged within one business day and receives a useful
answer or a named next diagnostic within two business days. Before planned
unavailability, assign coverage that can meet those expectations or record the
window as unstaffed before it starts. This is not a 24x7 commitment.

Security, private-content, credential, public-availability, or known-bad rollout
reports follow [LIVE_OPERATIONS.md](LIVE_OPERATIONS.md) immediately rather than
waiting for a review. Repeated product failures are filed as separate native
issues with aggregate evidence, explicit priority, and no customer material.
Template changes use the conformance path from #457, preserve their image/content
identities, and record the intervention at the next review.

## Reviews and decision

At day 30 and day 60, compare the current snapshot with day 0, record course
corrections, and keep the original thresholds visible. Do not create the agent
knowledge-base or private-documentation variants during the experiment.

At day 90 choose exactly one outcome using the checked policy:

- `continue`: every adoption, support, and reliability hypothesis passes;
- `change`: there is at least one deployment and no stop override, but a
  continue condition misses;
- `stop`: there is a confirmed security/privacy boundary violation, no
  deployments, no active-use evidence plus zero earnings, or two consecutive
  support-expectation misses; or
- `approve_one_variant`: the continue floor passes and the same unmet use-case
  theme appears at least three times across at least two review snapshots.

The variant outcome approves exactly one separately scoped issue; it does not
authorize implementation in the review. When several outcomes appear possible,
apply stop overrides first, then the variant rule, then continue, then change.
The decision records source availability, interventions, reliability proof, the
categorical economics result, and a no-impact explanation for every unavailable
metric.

## External-gate ledger

The repository can prove this plan and its privacy constraints before launch.
It cannot prove publication, marketplace discoverability, private account
settings, Template Queue delivery, adopter behavior, resource usage, earnings,
retention, payouts, or customer sentiment. Those remain time-bound external
evidence after #458; absence of evidence while blocked is not a zero-valued
result and cannot close #460.
