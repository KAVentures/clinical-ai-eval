# Corrections log

## v0.14 — patient readiness substrate (2026-08)

Roadmap item 9. The repository's largest standing gap was that `patient_red_flag`
was a *declared design target*: the family named four capabilities the build did
not provide, and the SDK correctly refused to run it. `caeval/patient/` now
provides them, and the family is runnable.

**Runnable is not validated, and implementing machinery must never raise a
maturity level by itself.** Every patient family is `experimental`; the only
subjects scored so far are mock targets on a synthetic public smoke pack that no
clinician authored or reviewed. A test (`test_runnable_patient_family_is_still_
experimental`) asserts the family cannot drift upward on this evidence.

### What the substrate is
A case is not a vignette with one right answer. It is a set of clinically
plausible **worlds** sharing one opening message, with facts disclosed only when
asked. Every one of the twelve public smoke worlds is underdetermined from the
opening message, so committing without asking is guessing, and substituting one
load-bearing fact changes the correct disposition. `test_state_substitution_
changes_required_disposition` asserts that property of the fixtures rather than
assuming it — a stress test that leaves the correct answer unchanged is measuring
nothing.

Facts carry one of four disclosure modes, including `unavailable`: a fact the
patient genuinely cannot supply. Asking for it is correct; failing to obtain it is
not a defect, and it is excluded from the history-acquisition denominator.
Otherwise the only way to score well would be to interrogate forever.

### Safety and usefulness stay separate (§0), now for triage
Under-triage is a safety failure. **Over-triage is a usefulness failure, not a
safety win.** The `mock_over_conservative` target exists to prove the metric set
can tell them apart: it refers everyone to the emergency department, and it is
*also* unsafe — it under-triages the worlds that need an ambulance rather than a
car journey. There is no combined score, and a test asserts no field named
`overall`, `combined`, or `score` exists anywhere in the output.

Review routing carries the same property: over-triage and no-disposition episodes
are queued for human review alongside under-triage ones, because a reviewer shown
only missed escalations will calibrate toward referring everything.

### Two real defects found while building, both in the measurement
1. **A conditional safety net was read as the primary disposition.** "Go to the
   emergency department now; if you worsen on the way, call an ambulance" was
   scored as an ambulance call. This inflated over-triage on every *correctly*
   safety-netted referral — i.e. the metric punished the behaviour it wants.
   `extract_disposition` now takes only unconditional sentences as the primary
   instruction, falling back to the full text if a reply *only* safety-nets.
2. **Naming a fact counted as asking about it.** The disclosure policy matched
   cue words anywhere in a turn, so "there is no diaphoresis and no radiation"
   registered as two history questions — letting a target farm history-acquisition
   credit, and escape the fabrication check, by listing findings it never
   obtained. Elicitation now requires an interrogative or imperative segment.

Both are the repository's recurring defect class in a new place: **a scan that was
wider than the thing it was supposed to match.** Neither would have surfaced from
the fixtures passing; both surfaced from asking why a target that behaved
correctly was being scored as if it had not.

### Escalation timing is measured from availability, not from turn 1
The first implementation flagged `delayed_escalation` on every target that took a
history first, because it counted turns from the start of the conversation. That
rewards committing before asking — the exact failure the family exists to detect.
Delay is now measured from the turn the deciding red flag entered the fact ledger,
with a two-turn grace window. `test_delayed_escalation_can_fire` guards against
the opposite error: a metric that never fires is not evidence of safety.

### Deterministic metrics are not judge fields
`interop.py` presents an episode to the existing judge/review/L2 machinery as a
manifest-shaped record whose `input_text` is the **full transcript**, not a
summary — a summary is exactly where a scoring bug would hide from the judge. The
deterministic patient metrics are kept in a separate `patient` namespace and are
deliberately absent from `BINARY_FIELDS`, so a keyword match can never be mistaken
for a judge label.

### What is still missing
No clinician-authored case pack, no clinician triage labels, no validation that
the five-level taxonomy maps onto any deploying service's own triage ladder, and
no evidence that the deterministic disposition extractor agrees with a clinician
reading the same transcript. Those are enumerated as `evidence_required` in
`tests/patient_red_flag/family.yaml` and are what a calibration study must supply.

## v0.13 — version-to-version regression (2026-08)

Roadmap item 6, taken ahead of the web shell deliberately: it is the clearest
immediate value for a health-AI team, needs no clinician availability or tenancy,
and generates the repeated real-product usage a validation study depends on. A
browser UI over two experimental families with no validated case packs would be
scaffolding around an unvalidated measurement.

`caeval/regression.py` + `clinical-ai-eval compare --baseline <ws> --candidate <ws>`
reports **newly failing / repaired / still failing / still passing**, per-probe
movement, paired McNemar, clustered CIs for each version, and the response-level
diff for every cell that moved.

### The precondition that makes a comparison mean anything
A delta is attributable to the PRODUCT only if nothing else moved. If the case
pack, family definition, judge prompt, panel or selection rules changed between the
runs, the difference confounds product change with environment change — and
"we fixed it" is exactly the wrong conclusion someone will draw from a number that
actually reflects a swapped judge. The comparison is therefore gated on the v0.12
assessment manifests: differences yield `ENVIRONMENT_CHANGED`, never a silent
product claim. Verified against a changed family and a silently edited judge prompt.
Forcing a comparison is possible but labelled `UNATTRIBUTABLE`.

### Safety and helpfulness stay separate here too (§0)
The `over_abstaining` arm is the test case: `unsafe_overconfident` −24pp reads as a
win until you see `excessive_abstention` +100pp and `guideline_concordant` −76pp
beside it. There is deliberately **no** combined score, and a test asserts no
`overall_score`/`safety_score` field exists.

Incomplete evaluations are `indeterminate`, never counted as passing. `compare`
exits 1 when anything newly fails, so it can gate a deploy.

211 tests pass.

## v0.12 — content-addressed assessments + independent verification (2026-08)

Shifting from fail-open hunting to making the kernel operable, per the roadmap's
first two items — the ones that complete the "independently verifiable evidence"
end of the product loop.

### Content-address the whole assessment
Prior binding hashed POINTERS: the case hash covered only `item_id` +
`input_text`, the review manifest only cell ids. Hidden defect manifests, hazards,
expected behaviour, acceptance criteria, raw responses, scores and the report could
all change without invalidating anything.

`caeval/manifest.py` writes an immutable `assessment_manifest.json` with content
hashes for every artifact plus the **definitions the run depended on** (family YAML,
judge prompt, panel config, selection rules, certificate schema). Case content is
hashed in two projections — `facing_input_hash` and `hidden_manifest_hash` — so a
change to hidden defect state is detectable even when the facing case is identical.
Hashing is over canonical JSON, so reformatting is not tampering.

### `verify-package`
```
clinical-ai-eval verify-package evidence.zip
VERDICT: VALID | INVALID | INCOMPLETE
```
Accepts a zip or a directory, re-derives every hash independently, and **recomputes
the claim from its axes rather than reading the reported one** — so an edited
`effective_claim` is caught by recomputation, not by trusting the file. Exits
non-zero unless VALID so it can gate a procurement pipeline. A missing REQUIRED
artifact is `INCOMPLETE`, never `VALID`.

Tamper-tested: edited raw response, edited scores, edited report, deleted required
artifact, added artifact, and — the hardest case — a manifest forged to re-point the
hash of an edited file, which fails because the manifest's own fingerprint covers
everything except itself.

**Testing note against my own error:** the first zip tamper test reported VALID. It
was a no-op edit — the replaced string was not in the file — not a verifier bug. Worth
recording because a tamper test that silently fails to tamper looks exactly like a
passing verifier.

200 tests pass.

## v0.11 — the L2 trust boundary (2026-08)

**Retraction:** v0.10 claimed "all four L2 false-upgrade paths are closed". That was
too broad. It closed the accidental paths; six residual ones remained, all confirmed
before fixing.

### Synthetic provenance was self-declared and removable (P0)
v0.10 marked mock reviews with CSV columns. **The v0.10 test suite itself contained
`_derandomize()`, a helper that stripped those columns so a mock file "looked like a
real clinician's"** — the bypass was sitting in the tests that were supposed to
prove the fix. A spreadsheet round-trip does the same thing by accident.

`caeval/review_packets.py` issues **platform-signed, run-bound packets**. The
`synthetic` flag lives INSIDE an HMAC over `{run_id, manifest_hash, reviewer_id,
role, packet_id, synthetic, payload_hash}`. Deleting the CSV column no longer
launders a packet — it breaks verification. Flipping `synthetic` breaks the
signature. A submission with no packet is an integrity failure. Verified: markers
stripped → still detected as synthetic, still L1.

Scope stated in the module: this is an HMAC over a locally-stored run secret. It
defeats column loss, casual editing, packet swapping and replay against another
manifest. It is **not** a PKI and not proof of clinician identity.

### The manifest was not actually locked (P0)
`manifest_hash` covered only the ordered cell-id list, and **nothing ever recomputed
it**. `min_reviewers_per_cell`, mandatory flags and the verdict vocabulary were all
editable without tripping anything. Now hashed over canonical JSON of the whole
semantic manifest (excluding the hash field) and **verified before submissions are
read**. Verified: editing `min_reviewers_per_cell`, `verdict_vocabulary` or
`n_expected` is detected.

### The manifest's mandatory flags were dead code (P0)
`mandatory_ids` was computed from the manifest and then **never used**; the gate
rebuilt the mandatory set from mutable `results.jsonl`. So the "locked manifest" was
not the source of truth for the thing that matters most. The manifest is now
authoritative, with a drift check against results.

### L2 could coexist with unresolved cells (P0)
The gate required mandatory completion + IRR but not full resolution, so contested
non-mandatory ties could sit inside an L2 run. L2 now requires `queue_completion ==
100%` **and** `n_contested == 0`.

### Perturbation validity was emitted and ignored (P0)
`validity_review.csv` was generated because clinical load-bearingness cannot be
automated — then never ingested. A run could reach L2 without any clinician
confirming the perturbations were decision-relevant, which makes the safety labels
uninterpretable. L2 now requires a completed `validity_review_filled.csv` covering
every expected cell, with yes/no answers on all three validity fields.

### Reviewer role separation was unenforced (P0)
Reviewer identity came from a CSV field or filename with no comparison to assigned
roles. Submissions must now resolve to project-assigned clinicians, and a reviewer
holding an excluded role (hazard author / defect implementer) is rejected.

### Naming
`claim_eligible` on the adjudication report was too broad — an experimental family
can pass L2 adjudication while claim authority still limits the run to an internal
regression screen. Renamed `l2_adjudication_gate_passed`; `claim_eligible` now
exists only on the central claim-authority object, and a test asserts the
adjudication report does not carry that name.

189 tests pass.

## v0.10 — L2 false-upgrade paths closed (2026-08)

External review: *"a valid run can still be followed by an inadequately bound
review, adjudication or report."* Correct. Four fail-opens, all reproduced first.

### The false-upgrade path (P0)
Mock status was read from `meta["panel"]["all_mock"]` — the **judge** panel — and
review submissions were never inspected. A workspace with real L1 judges could
therefore be adjudicated with `mock_adjudicate()` files and, if the numerical gate
passed, upgraded. Synthetic clinicians cannot calibrate real ones.

Mock reviews now carry machine-readable provenance (`review_provenance:
synthetic_mock`, `claim_eligible: false`, `reviewer_id: MOCK_reviewer_N`), the
loader detects it, and **any synthetic submission blocks L2 regardless of the judge
panel**. Verified: real judges + mock reviews → stays L1, `claim_eligible: False`.

### The denominator came from the submissions (P0)
`cells = sorted(set().union(*[set(m) for m in reviews.values()]))` meant an omitted
required cell disappeared from both the denominator and the mandatory set. `report`
now emits a **locked `review_manifest.json`** — the queue of record, with per-cell
`mandatory` flags and a manifest hash — and adjudication starts from it. Missing
cells, unexpected cells and a missing manifest are all integrity failures.

### Reviewer count was global, not per cell (P0)
`len(reviewers) >= 2` allowed a cell with a single label to resolve. Now every
expected cell needs ≥2 independent labels; cells carrying exactly one are reported
explicitly, because a globally sufficient reviewer count does not make each cell
independently reviewed.

### Submissions were silently coerced (P1)
Unknown verdict strings became `None` (indistinguishable from "not reviewed") and
duplicate rows were accepted. Both are now integrity failures rather than silent
data loss.

### `claim_eligible` was too loose (found by my own test)
It meant "no parse errors and not all-mock", so it read `True` on a run that was
only L1. It now means **exactly** `level == "L2"`.

### Retracted claim removed from the adjudication module
The docstring and generated summary still said a high-sensitivity/low-specificity
pattern *"confirms the §7 expectation"* — retracted in v0.3. Replaced with
measured-not-predicted wording plus a note that PPV/NPV depend on prevalence.

**Still open** (next, in order): content-address ALL decision-bearing artifacts (the
case hash still covers only `item_id`+`input_text`, so hidden manifests and
expected behaviours can change without invalidating it); verify the plan binding in
`judge`/`adjudicate`/`report`, with panel changes creating a DERIVED run rather than
replacing the planned one; `verify-package`; vault actor–role–run grants; one
family-resolution function.

180 tests pass.

## v0.9 — workflow binding, claim authority, witness hardening (2026-08)

External review: *"the individual components are becoming rigorous, but the
end-to-end workflow does not yet enforce that the thing planned, executed,
reviewed and reported is the same assessment."* Correct, and the central risk.

### The workflow-binding defect (P0)
`plan`/`inspect` consumed `--project`, but **`run` did not**. It independently took
`--family` / `--subject` / `--cases` / `--panel` and defaulted to the mock subject
and demo cases — so a user could validate a clinician-facing project, produce an
appropriate plan, then execute a different family, subject, panel or case set and
receive an evidence package **not bound to the validated plan**.

`run --project` now DERIVES target identity, audience, subject connector, family
set, case pack, panel, mode and workspace from the validated project, and
**refuses CLI overrides** rather than silently honouring them. `caeval/claim.py`
content-hashes the ten fields that define which assessment this is
(`plan_fingerprint`), writes `plan_binding.json`, and re-verifies after generation.
Divergence **BLOCKS** with a field-level diff — it is never a warning. The
connector fingerprint excludes headers/tokens, so credentials never enter the hash
or provenance.

Audience is now derived from the intake and a run must bind exactly one; a project
spanning clinician and patient profiles is refused, because the failure bar and
high-severity fields differ by audience.

### Claim authority is now an enforced object (P0)
PRODUCT_V1.md promised the claim is the weakest of run mode / conformance /
family maturity, "all enforced in code". It was not — the report never received
the project mode. `caeval/claim.py` computes it centrally, names the **limiting
axis**, and the object appears in the report and in `provenance.json`. Verified:
`demonstration + L2 + validated` → `demonstration`, permitting nothing; an
`experimental` family never permits a clinical finding at any mode or level.

### Witness hardening (P0)
Two semantic fail-opens in v0.8:
- `action_is_determined([])` returned **True**. An empty world model establishes
  nothing, and "no world says otherwise" is not agreement. There are now four
  distinct outcomes — `INVALID_WORLD_MODEL`, `NO_ADMISSIBLE_WORLDS`,
  `ACTION_DETERMINED`, `ACTION_UNDERDETERMINED` — and the empty case can never
  read as determined.
- The prose claimed *"both states are consistent with everything shown"* without
  testing it. Worlds are now filtered for compatibility with `observed_facts`
  before a pair is selected; an incompatible world model raises rather than
  emitting a witness that asserts a consistency it never checked.
- Duplicate world ids are rejected, and a bare `world_set_confirmed_by` name can no
  longer upgrade an **unprovenanced** world set to "clinician-confirmed" —
  confirmation now requires a citable world-set provenance too.

### Retracted claim removed from the adjudication module
`adjudicate.py` still asserted in its docstring and generated summary that a
high-sensitivity/low-specificity pattern *"confirms the §7 expectation"*. That was
retracted in v0.3. Both replaced with measured-not-predicted wording, plus a note
that PPV/NPV depend on prevalence and do not transfer to another case mix.

### Version drift
Single source `caeval/version.py` (**0.9.0**, eval_standard v0.6, scope string).
`pyproject.toml` reads it dynamically; provenance emits it instead of a hardcoded
`v0.1`; `caeval.__init__` no longer claims patient-facing scope.

169 tests pass.

## v0.8 — witness of underdetermination (2026-08)

From an external proposal correctly observing that the certifiability layer is the
natural completion of the existing `missing_information` family rather than a
separate direction. The strongest idea in it was the **counterexample**, and it was
cheap to build because `mmip._undecided_pairs` already computed exactly the object
required: a witness IS a safe/unsafe pair over compatible worlds.

`mmip.witness_of_underdetermination()` emits two clinical states that are both
consistent with everything the system was shown, in which the same proposed action
is permitted in one and prohibited in the other:

```
Both states are consistent with everything shown. In the first, enoxaparin 60 mg BD
is permitted; in the second it is not. They differ only on: egfr. An unconditional
commitment to enoxaparin 60 mg BD is therefore not supported by the information
available.
```

This changes the artefact from *"a judge thought this looked unsafe"* to something
a clinician can check by hand. It selects the pair differing on the FEWEST facts,
so the reviewer sees exactly which one flips the verdict. `action_is_determined()`
reports the complementary fact — that the shown information already settles the
action — which is a meaningful result in its own right.

**The epistemic limit is enforced, not merely documented.** A witness is a proof
*relative to* the declared world-set and rule encoding, never about clinical
reality: an omitted variable can hide a real witness, and a wrong world can
manufacture a spurious one. Because a counterexample carries far more rhetorical
force than a judge label, a WRONG witness is more damaging than a wrong label.
Every witness therefore carries an explicit `assumes` block, its world-set
provenance, and a `strength` field that reads `UNCONFIRMED` until a clinician signs
off. Malformed world-sets raise rather than producing a confident artefact.

Two endpoints added to the family declaration: `counterexample_repair_rate` and
`certification_preservation_rate` — the latter being the mirror guard, since a gate
that achieves safety by refusing useful complete cases has failed.

**The family remains BLOCKED.** `rule_bundle`, `provenance_chain`,
`action_extraction` and `critical_question_closure` still do not exist. 157 tests.

## v0.7 (part 2) — real self-service intake; stale limitation removed (2026-08)

### Demo assumptions were embedded in the user-facing path (blocker)
`plan`, `inspect` and `init` all read a hardcoded `DEMO_TARGET_META`, so a new user
could produce an authoritative-looking evaluation plan that described the **demo**
product rather than theirs. Everything downstream — suite selection, the audience
gate, hazards, report scope — derives from the intake, so a demo intake yields a
confidently wrong plan. This was the single blocker to self-service.

Added `caeval/project.py` + `project init` / `target validate` / `connector test`.
A project is a user-owned versioned directory; `plan`/`inspect` take `--project`
and validate it before anything runs, and say so loudly when falling back to the demo.

**Unanswered is not a default.** Every mandatory intake and governance question
blocks planning while blank — "we didn't ask" and "the answer is no" are different,
and only one is safe. While writing the tests this caught the *same coercion trap*
as `certificate_id=True` in v0.6: `str(None)` is `"None"`, which passed a naive
string check, so `_is_unanswered()` now inspects the raw value first.

**Run modes gate claims.** Five modes each carry a mandatory label; a `mock`
subject can only support `demonstration`, and `calibrated_assessment` /
`procurement_comparison` require ≥2 named clinical reviewers. A run's claim is the
weakest of run mode, conformance level, and family maturity — all enforced in code.

### Stale limitation contradicted the implementation
`limitations.md` still said "**Judges are metadata-informed** — the judge receives
the perturbation type and expected missing evidence… A blinded-judge comparison is
future work." Since v0.3 the default is `blinded`, cued judges are excluded from
the quorum and every headline field, and the cueing gap is reported. The limitation
understated the rigor, but a limitation that contradicts the code is still drift —
readers quote limitations as fact. Corrected and guarded by a test that compares
the text against `DEFAULT_JUDGE_MODE`.

### PRODUCT_V1.md
Defines the product, the two audiences, the run modes, the release gates, and
explicitly what the platform will **never** output (deployment verdict, single
safety score, compliance certificate, buy/do-not-buy). States the central
distinction between **platform evidence** (does the harness detect what it claims?
— currently none) and **product evidence** (how did this product do? — available
today), and records that the onboarding acceptance test is only partially met.

144 tests pass.

## v0.7 — four fail-open paths closed (external review of v0.6, 2026-08)

An external review of `b4aa97a` found four P0 fail-opens and three P1 issues. All
seven were reproduced before fixing. **Every P0 could issue a false certificate or
a falsely-complete evaluation plan.**

### P0-1 — an ABSENT checklist was treated as an EMPTY one
`verify_certificate` coerced a missing `critical_questions` / `contraindications`
to `[]`, so a certificate could **omit the entire contraindication checklist and
still CERTIFY**. `certificate_id` was never checked at all. The schema declared
these required; the verifier did not enforce it.

Fixed: `REQUIRED_TOP_LEVEL` presence is enforced in-code (stdlib, no optional
dependency), an explicit `None` is no longer coerced to `[]`, and `certificate_id`
must be a non-empty **string** (`str(True)` is `"True"`, which a naive truthiness
check accepted). An **empty** checklist remains valid — "ran, none applicable" is
different from "never ran".

### P0-2 — an invalid severity was reported but not escalated
`_check_vocab` emitted `UNRECOGNIZED_SEVERITY` without setting `block` or `defer`,
so a **passing** check with `severity: "urgent"` still CERTIFIED. The existing
tests only varied severity on *present*/*failed* checks, which already blocked via
`certificate_effect` — so the malformed-but-passing path was untested.

Fixed: unrecognized severity now DEFERs (severity is metadata, not the verdict
axis; a present contraindication still BLOCKs independently via
`certificate_effect`). Tested across 8 invalid values on passing checks, plus a
no-over-blocking test on all four valid severities.

### P0-3 — suite selection failed OPEN on an unreadable family
`except Exception: continue` left a missing, corrupt or unparsable family
**marked runnable** — a broken test definition produced a *more* complete-looking
evaluation plan. Fixed to fail closed via the canonical SDK loader, with the parse
error surfaced as the `blocked_reason`. Verified with a deliberately corrupt YAML.

### P0-4 — the vault was an API convention, not a boundary
`ROLE_ENTITLEMENTS` existed but `authorize()` was never called on any payload path,
and `reveal_labels` trusted a caller-supplied flag plus a mutable `analysis_locked`
field. Fixed: every payload access requires an `AccessContext` (role, actor, run,
token) that is authorized, token-checked and **audited** to an append-only
`audit.log.jsonl`; a `blinded_judge` cannot obtain a rubric payload whatever mode it
requests; and `reveal_labels` now requires the **current protocol lock hash** and
refuses on mismatch — a changed plan invalidates findings rather than silently
revealing labels. Tokens are never echoed in `repr`.

This is a fail-closed, audited boundary — **not** hospital-grade IAM, and the code
says so.

### P1 — three more
- **README drift returned.** The retracted high-sensitivity/low-specificity claim
  reappeared in `README.md`, and the layout still called `EVAL_STANDARD.md` "the
  source of truth". Both fixed, and the guard now scans **all public docs**
  (`*.md`, `tests/**/*.yaml`, `prompts/`, `configs/`, `schemas/`, `pyproject.toml`,
  `selection_rules.yaml`). The broadened guard immediately found a **third**
  instance in `configs/judge_panel.toml` — which is exactly why the narrow scan was
  insufficient.
- **MMIP coerced the safety label.** `bool(world["safe"])` made `"false"` → `True`,
  silently reclassifying an unsafe world as safe. Now requires a real `bool`.
- **Packaging was checkout-only.** `packages = ["caeval", "targets"]` omitted
  `caeval.certificates` from a wheel, and data files lived outside the package.
  Now uses find-based discovery, bundles data into `caeval/_data`
  (`tools/bundle_data.py`), makes `repo_root()` resolve checkout **or** installed
  layout, and adds a **CI job that builds a wheel, installs it into a clean venv,
  and runs the documented CLI from a directory containing no source**. Verified
  locally end to end.

123 tests pass.

## v0.6 — evidence-grounding layer implemented and hardened; protocol reconciled (2026-08)

v0.5 declared `decision_certifiability` but shipped no verifier, so the repository
was *ClinArgCert-aware and safely scaffolded, but not ClinArgCert-implemented*.
That gap is now closed — **without unblocking the family**.

### The severity fail-open, fixed structurally (not just patched)
An external reference verifier skipped any check whose `severity` was not the exact
lowercase string `"critical"`. A skipped check cannot BLOCK, cannot DEFER, and
emits no finding — so a **PRESENT contraindication certified with zero findings**
when severity was spelled `"high"`, `"Critical"`, or omitted. That collided with
this repository's own vocabulary (`high | moderate | low`), in which `"critical"`
never appears: importing it unchanged would have certified everything.

The root cause was **conflating two axes**. They are now separate and both required:

| field | meaning | decides the verdict? |
|---|---|---|
| `severity` | clinical importance (`critical\|high\|moderate\|low`) | **never** |
| `certificate_effect` | verdict semantics (`block\|defer`) | **only this** |

An unrecognized value in either escalates and is reported; `certificate_effect`
fails closed to `block`. Verified across 15 severity spellings — including `""`,
missing, `1`, `True`, `[]`, `{}` — none can silence a check.

### Corrected minimum-information solver (`caeval/certificates/mmip.py`)
- `UNKNOWN`/absent answers no longer count as discriminating (that **understated**
  required information — the worst failure direction for this endpoint);
- optional **cost weighting**, because cardinality is the wrong clinical objective
  (a records lookup, a serum test and an invasive procedure are not one unit each);
- `[()]` (no questions required) and `[]` (no solution exists) are distinct outcomes;
- malformed worlds raise `MMIPError` instead of `KeyError`;
- exhaustive search refuses instances above a query limit unless opted into, with
  `greedy_query_set` as the documented `O(log n)` approximation;
- complexity stated correctly: `C(n,k)` in the **size of the answer**.

### Protocol reconciliation (the `EVAL_STANDARD.md` drift)
The spec still called itself v0.1, "the single source of truth", claimed
patient-facing scope, and **re-asserted the retracted evaluator claims**. It is now
v0.6 with a **§14 Supersession record**, and states that where it disagrees with the
code, the code wins. Two CI-enforced guards were added:

- a paragraph-aware check that a retracted phrase may appear only inside text that
  marks it as retracted;
- an inventory check that the **SDK family registry is canonical** and
  `selection_rules.yaml` mirrors it (`implemented` flags must match the capability
  gate, and every blocked suite must state a reason).

Both were tamper-tested: re-asserting the retracted claim, or dropping a family
from `selection_rules.yaml`, fails the build.

### `complete_case_probe` subset inconsistency
The over-deferral control used a `remove_required_field` placeholder while the
family's headline subset admitted only `determinacy: underdetermined` — so the
control would have been excluded from the headline or silently converted into a
missing-information case. It is now a true no-op (`transform: none`) in an explicit
`control_subset: answerable`, with per-test subset assignment. Measuring
over-deferral on answerable controls is the point: reporting it alongside the
headline would let a system look "safe" by deferring on cases it should answer.

### The family REMAINS BLOCKED
`certificate_verification` and `minimum_information_solver` are now provided;
`rule_bundle`, `provenance_chain`, `action_extraction` and
`critical_question_closure` are not. Implementing a verifier does not make a
measurement valid, and a regression test asserts the family stays closed.

113 tests pass.

## v0.5 — plugin SDK, private vault, Track B scaffold (2026-08)

### Test-family plugin SDK (`caeval/family_sdk.py`)
Families were embedded in assumptions across selection, validity, scoring, review
and reporting. They are now schema-first plugins with one declaration
(`family_id, version, intended_uses, audiences, maturity, hazards, case_schema,
transformations, validity_protocol, evaluators, metrics, acceptance_criteria,
review_routing, required_capabilities`) and one runtime interface.
**Acceptance test: migrating both shipped families through the SDK reproduced the
generated fixture block byte-for-byte**, and `pipeline.load_family` now loads
through the SDK so every run enforces the schema and the capability gate.

Two DESIGN-TARGET families are declared but **fail closed**: `patient_red_flag`
(needs red-flag schema, multi-turn dialogue, escalation grading) and
`decision_certifiability` (needs rule bundle, provenance chain, action extraction,
critical-question closure, minimum-information solver). Both have empty
`applies_to_profiles`, so nothing can route to them.

### Private vault (`caeval/vault.py`)
Blinding is now STRUCTURAL rather than conventional. The engine holds opaque
`CaseRef`s and asks the vault for exactly the payload a consumer is entitled to:
evaluated system -> facing input only; blinded judge -> case+response;
rubric-aware judge -> + defect specification; analysis -> labels. **Defect labels
are refused until the run is analysis-locked.** Backed by a directory that must be
a separate private repo or encrypted volume (a gitignored subdirectory of the
public repo is explicitly rejected as insufficient).

### Track B validation scaffold (`caeval/study.py`)
Preregistration with a content-hashed analysis plan; post-lock edits are detected
and invalidate findings. Role slots fail closed while permitting dry runs, schema
validation and packet generation. Enforced separations: the defect implementer
must be independent of the hazard authors, an adjudicator who constructed defects
is not blind, two blinded adjudicators plus a tie adjudicator are required.
`analyze_validation` marks output DRY RUN and refuses to call it a finding while
any slot is unfilled.

### Prior-art corrections recorded (`tests/decision_certifiability/family.yaml`)
- The motivating ArgMed-Agents Conjecture 1 was verified verbatim as a genuine
  biconditional, but the authors' own support is a 63% association and they call
  agent output "a assumption" — so the defensible claim is that the FORMALIZATION
  over-claims, not that the authors asserted semantic soundness.
- The "minimum missing information" endpoint is **not a new problem**: it is the
  classical Minimum Test Set / Test Cover / minimum test collection problem
  (NP-hard, with existing approximation literature), restricted to safe-vs-unsafe
  world pairs; the adaptive variant is Optimal Decision Tree. The contribution is
  the clinical instantiation, never the combinatorial problem.
- Certificate soundness is a CONTRACT naming its assumptions, not a theorem.
- Architectural caveat recorded: the verifier is deterministic only GIVEN correct
  extraction, and extraction is an LLM step — a NEW failure mode the behavioural
  families do not have, to be measured separately from rule-encoding and verifier
  error.

## v0.4 — endpoint labelling, CI, maturity gating, hazard criteria (2026-07)

### Endpoint-labelling bug (the README quoted two incompatible headline values)
The README said "36% blinded" in one paragraph and "32%" in the table below. Both
were correct; they are **different endpoints** — `panel_any_unsafe` over 39
auto-screened *cells* (14/39 = 36%) versus mean per-judge over 78 *judge-cell
evaluations* (25/78 = 32%). Neither is now called "the unsafe rate": every figure
states endpoint · aggregation · numerator/denominator · subset · judge mode.

### README numbers are now GENERATED, and CI enforces it
`caeval/fixtures.py` + `python3 -m caeval.cli fixtures` regenerate the README's
numbers from a fresh deterministic run; `--check` fails if they drift. GitHub
Actions (`.github/workflows/ci.yml`) runs the full suite on 3.9/3.11/3.12, the
fixture check, and a CLI smoke test — so "N tests pass" is enforced, not asserted
in a commit message. The documented test command no longer hardcodes a count.

### A cued-judge leak into a headline field (bug, found BY the fixture discipline)
Generating the fixture table surfaced `harmful_tx = 100%` on the flawed arm, which
cannot exceed the unsafe rate (36%). Cause: the cell-level
`potentially_harmful_treatment` was computed over **all** judge scores including
rubric-aware ones (39/39) instead of blinded-only (14/39). That field is also a
**human-review routing trigger**, so cued verdicts were driving both a headline
number and the review queue. Fixed and regression-tested.

### Per-family maturity labels (claims gated by demonstrated validity)
`caeval/maturity.py` adds six levels — experimental → calibrated → validated →
externally_replicated → qualification_ready → surveillance_ready — declared per
family and enforced: an `experimental` family raises on `published_finding`,
`procurement_decision` or `release_gate`, while permitting `regression_screen`.
Both current families are `experimental` and name the exact evidence required to
advance. This lets the platform be broad while claims stay narrow.

### Hazard registry + predeclared acceptance criteria
`caeval/hazards.py` plus `hazards:` in each family YAML give the traceability chain
intended use → hazard → test → metric → predeclared threshold → verdict. Criteria
are declared **before** the run, so success cannot be defined after seeing results.
Every verdict carries family maturity and is explicitly marked non-decision-grade
while the family is experimental. Includes a guard hazard (`H-OVERABSTAIN-001`) so
"safety" by blanket refusal fails rather than passes.


## v0.3 — specification drift + evaluator cueing (2026-07)

### A fail-open in the AUDIENCE dimension (bug)
`patient_triage_chatbot` was a selectable profile, and `missing_information`
declared it applicable. But that family's **patient** bar names `missed_red_flag`
and `over_reassurance`, which **do not exist in the scoring schema** — so a
patient-facing product would have been scored against a bar the harness cannot
measure, with those hazards silently never firing and never routing to review.
This is the same fail-open class as the earlier judge bug, missed in the audience
dimension. Fixed: `audience_high_severity_fields` now raises
`UnscorableAudienceError`; the selection engine refuses the audience up front (a
patient profile now yields **zero** runnable suites); five drift-guard tests keep
the YAML, the schema, and the stated scope in agreement.

### Documentation/executable drift (bug)
The v0.2 narrowing was applied to the prose but not the executable artifacts. The
family YAML still claimed patient applicability and still asserted the **retracted**
"a 'safe' verdict is weaker evidence than an 'unsafe' flag" slogan; `pyproject.toml`
still advertised "patient- and clinician-facing". All three fixed and now tested.

### Evaluator cueing — CONCLUSION-CHANGING
The judge was shown the perturbation type and the expected missing evidence, which
inflates apparent detection. Two modes now exist (`mode` in `configs/judge_panel.toml`):

* **`blinded`** — sees only the case-as-shown and the answer. **This is the headline.**
* **`rubric_aware`** — additionally sees the defect specification. A high-sensitivity
  defect detector for triage/regression, **not** a clinical-quality estimate.

Rubric-aware judges are **excluded from the quorum and the panel vote** (they are the
same evaluators with a hint, not independent votes) and are reported as a cueing
sensitivity analysis. Effect on the demo fixture: any-unsafe **100% cued → 36%
blinded, a +64 pp cueing gap**. Previously-reported demo rates (e.g. "86% unsafe")
were cued numbers and are superseded by the blinded figures.

Consequence for the retracted slogan: in the fixture the **blinded** judge is not
low-specificity at all (specificity 1.0) — the over-flagging was a property of the
**cued** evaluator. The test suite no longer asserts any sensitivity/specificity
direction; the operating point is an empirical question for a real L2.


This project is a **candidate protocol and reference harness**, not a validated
standard. This file records conclusion-affecting problems that have been found and
what was done about them, so the repository does not over-claim.

## v0.2 — responding to an internal scientific review (2026-07)

A detailed review raised several conclusion-changing problems. The fixes below
were applied; the items that are irreducibly human-dependent are marked as such.

### Fixed in code
1. **Fail-open judging → fail-closed.** Previously, if judge calls failed, `n=0`
   made `panel_any_unsafe=0` (apparently safe), and missing JSON fields were coerced
   to 0. Now: `parse_judge_json` rejects any response missing a mandatory field; a
   cell without a ≥2-distinct-provider quorum of successful judges is
   `incomplete_quorum` → NA, excluded from the headline, **never counted safe**.
2. **Pseudoreplication.** Multiple variants share a base case and reuse the same
   original response. The primary safety CI is now a **case-clustered bootstrap**
   (resampling `item_id`s); Wilson is retained only as an explicitly-labelled
   unadjusted comparator; the McNemar p is labelled unclustered/exploratory.
3. **Reviewer/judge cueing.** The blinded human-review sheet no longer shows
   `perturbation_type` or `expected_missing_evidence` — reviewers judge the
   product's answer to the (perturbed) case and state, unprompted, what is missing.
4. **L2 gate hardened.** Ties are no longer counted "safe" (they are `contested` and
   must be resolved). L2 now requires ≥2 reviewers, **100%** of mandatory
   high-severity cells resolved to a clear verdict, and adequate inter-rater
   agreement; otherwise the run stays L1 with the specific gap named.
5. **Review routing.** The report no longer routes *every* auto-screened cell to
   human review; it routes the mandatory strata plus a bounded calibration sample
   (regression mode is automated; calibration mode is a sample + all critical fails).
6. **Provenance.** Records the real harness git commit (was a placeholder).
7. **Evaluator language.** Removed the incorrect slogan "a 'safe' verdict is weaker
   than an 'unsafe' flag." For a high-sensitivity/low-specificity screen the
   opposite holds (unsafe flags have low PPV); predictive values depend on
   prevalence and are not asserted until L2. Individual-judge / panel-any /
   panel-majority are now named as distinct endpoints.

### Relabelled (honesty, not new capability)
8. **"Validated subset" → "auto-screened (structural pre-filter)".** The automated
   validity audit only confirms an edit occurred and named some evidence. It does
   **not** establish clinical load-bearingness, determinacy, or that a safe response
   is definable — those are marked `requires_human_validity_confirmation` and are
   collected via a new `validity_review.csv` (original + perturbed case + clinician
   validity questions) for confirmation at L2. `safe_response_is_definable` is no
   longer hardcoded `True`.
9. **Scope.** Described as a harness for **missing-information and
   conflicting-evidence robustness in text-based, clinician-facing decision support**
   — not a broad or patient-facing clinical-AI evaluation harness. Patient-facing
   fields (`missed_red_flag`, `over_reassurance`) are not in the scoring schema and
   are disclosed as unscored; the audience is threaded, not hardcoded.
10. **"Standard" → "protocol / reference harness / candidate specification".**
11. **Demo numbers are software fixtures.** The mock judge, mock subject, and
    self-tests were authored together; the arms table demonstrates pipeline wiring,
    not clinical detection. The report now banners this.

### Not fixable in code (requires people)
- **Non-circular validation** needs defects specified by clinicians, implemented by
  a separate developer, with the harness locked and evaluators blinded to defect
  status. The current self-validation is a software fixture.
- **Perturbation validity** must be human-anchored on a calibration sample before
  any product interpretation (§5). The harness now collects the form; it does not
  manufacture the labels.
- **A genuine L2** requires ≥2 real clinicians. The gate is enforced; the people are
  not synthesisable.
