# Defect triage reference

This is the single normative product-defect disposition policy. Use it with the [AI team delivery workflow](SKILL.md#defect-triage-and-repair-bounds). It does not replace Owner product acceptance, action authorization, provider privacy or required Security/QA/Mac gates.

<a id="user-impact-first-defect-triage-and-bounded-delivery-owner-decision-issue-92"></a>
## User-impact-first defect triage and bounded delivery (Owner decision, Issue #92)

The [decision and Claude independent review](https://github.com/davidcatcat/davidcattoolsproject/issues/92)
adopted C-1 through C-6 with coordinator precision amendments. A report is
evidence, not an executable automation. Existing P0 escalation, action
authorization, provider privacy and mandatory Security/QA/Mac gates remain intact.

Every newly reported or reviewer-found defect must record: affected **user
journey / observable impact**, exact feature/release scope and reachability,
evidence/reproducer and source SHA, **Severity**, **Priority**, **release blocker
for that specific deliverable**, disposition/owner and next checkpoint.
These are **three separate decisions**, not one label:

- `SEV-0` critical: demonstrated or credibly reachable data loss,
  unauthorized mutation/exfiltration, or breached mandatory authority boundary.
- `SEV-1` high: core functionality is unusable or materially incorrect,
  including a false success result or a fail-open required safety check.
- `SEV-2` medium: bounded or recoverable functional defect; record any
  *independently verified* workaround if one exists. It remains a **release
  blocker** if the delivered feature reaches an unsafe or false-success path.
- `SEV-3` low/informational: nonfunctional wording, cosmetics, extra
  assertions or quality improvements with no uncovered mandatory invariant.

`Priority = NOW | NEXT | BACKLOG | WONT_FIX` records *when* to act.
`release_blocker = YES | NO | UNCLASSIFIED` refers to the **affected
release**, not every independently safe mainline feature. A required safety
fail-open, false `APPLIED`, data/credential/authority harm or missing required
acceptance evidence on a reachable release path is **always blocking for that
path regardless of Medium naming or lack of user complaints** (C-1). A failed
mandatory Mac gate cannot be called PASS because Linux CI is green.
Developer-authored downgrades are prohibited: moving severity/blocker downward
requires an **independent reviewer or coordinator written rationale** (C-2).

A workaround or disabled feature removes a blocker only if independent evidence
proves the workaround and **implementation-enforced unreachability** of the
unsafe path (not just README instructions) (C-3). When product-vs-test origin
is uncertain, record `UNCLASSIFIED / POSSIBLE_PRODUCT_DEFECT`, HOLD the
**affected** feature's required gate and use one minimal independent behavioral
oracle; do not assume a test bug or block unrelated safe functionality (C-4).
An alternative verification must cover the **same user outcome, security
invariants and required failure paths**, on the **same candidate SHA and
required platform**, with equal-or-stronger reviewer-independent evidence;
not literally identical assertion syntax. No mock/Linux substitute for required
native Mac behavior (C-5).

Run the cheapest relevant **user-journey smoke + existing regression tests**
first, then affected Security/Functional/native checks; use the mandated full
suite and exact-head CI at the release gate. Reuse durable regression tests;
do not add a test framework for each defect. A passing test is scoped
evidence, never general proof that untested paths are safe. Keep nonblocking
issues in one tracked list with owner, target milestone and explanation.
Do not create a PR for every review report: publish SHA-bound review evidence
on the product PR or tracking Issue.

**Bounded convergence (C-6):** The repair cap follows the [High Debug repair cap](pm-continuation-reference.md#repair-cap). After the first unsuccessful correction in a confirmed root-cause/finding family, PM applies the mandatory [High Debug rule](pm-continuation.md#blocked-diagnose-then-retry-reassign-or-isolate) before the 2nd repair. Four independent review rounds, a third recurrence
of equivalent findings, or disproportionate token/time spend trigger a
**whole-feature cost/benefit decision**: safe reduced release, independently
reviewed redesign or HOLD/reprioritize. Reviewer independent of the author
adjudicates root-cause equivalence; renaming a finding does not reset counters.
Budgets are measured where evidence exists and `UNKNOWN` otherwise, never
invented zero. No count or cost threshold waives an actual mandatory blocker,
permission boundary or unresolved product-correctness invariant. Owner only
receives a P0-level product choice when necessary, not routine technical
troubleshooting.
