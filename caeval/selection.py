"""Test-selection engine (EVAL_STANDARD.md §4) — rule-based and inspectable.

Maps an intake target profile to `required_suites` via the EXPLICIT rules in
selection_rules.yaml, never an opaque single-model decision. A reviewer can see
*why* each suite was chosen (the `because` justification is copied through). A
required suite with no implemented family is recorded as REQUIRED-BUT-NOT-RUN,
never silently dropped (§4, and §13 honesty about scope).
"""
from __future__ import annotations

import yaml

from .util import repo_root


def load_rules(path: str | None = None) -> dict:
    p = path or (repo_root() / "selection_rules.yaml")
    with open(p) as f:
        return yaml.safe_load(f)


def _load_family(suite: str) -> dict:
    with open(repo_root() / "tests" / suite / "family.yaml") as f:
        return yaml.safe_load(f)


def select_suites(profiles: list[str], rules: dict | None = None) -> dict:
    """Return {required_suites: [...], not_run: [...], matched_rules: [...]}.

    Each required-suite entry: {suite, because, implemented}.
    """
    rules = rules or load_rules()
    suites_meta = rules.get("suites", {})
    chosen: dict[str, dict] = {}
    matched_rules = []

    for rule in rules.get("rules", []):
        applies = rule.get("applies_to_types", [])
        if "*" in applies or any(p in applies for p in profiles):
            matched_rules.append(rule["id"])
            for req in rule.get("require", []):
                suite = req["suite"]
                implemented = bool(suites_meta.get(suite, {}).get("implemented", False))
                # first justification wins but record all matched rules for audit
                chosen.setdefault(suite, {
                    "suite": suite,
                    "because": req.get("because", ""),
                    "implemented": implemented,
                    "blocked_reason": suites_meta.get(suite, {}).get("blocked_reason", ""),
                    "chosen_by_rule": rule["id"],
                })

    # AUDIENCE GATE: a suite is only runnable if the family can actually SCORE this
    # target's audience. A family whose audience bar names unscorable high-severity
    # fields is refused here, up front, instead of silently scoring against a bar
    # the harness cannot measure (fail-closed in the audience dimension).
    from .score import family_audience_support, audience_key
    from .intake import TARGET_PROFILES
    audiences = {audience_key(TARGET_PROFILES[p]["audience"]) for p in profiles if p in TARGET_PROFILES}
    for meta in chosen.values():
        if not meta["implemented"]:
            continue
        try:
            fam = _load_family(meta["suite"])
        except Exception:  # noqa: BLE001 — family file unreadable; leave as implemented
            continue
        support = family_audience_support(fam)
        blocked = [a for a in audiences if not support.get(a, {}).get("supported", False)]
        if blocked:
            unscorable = sorted({f for a in blocked for f in support[a]["unscorable_fields"]})
            meta["implemented"] = False
            meta["blocked_reason"] = (
                f"audience(s) {sorted(blocked)} not supported by family '{meta['suite']}': "
                f"high-severity field(s) {unscorable} are not in the scoring schema. "
                f"Refusing to score rather than measure against an unmeasurable bar.")

    required = [chosen[s] for s in sorted(chosen)]
    not_run = [r for r in required if not r["implemented"]]
    return {
        "matched_rules": matched_rules,
        "required_suites": required,
        "runnable_suites": [r["suite"] for r in required if r["implemented"]],
        "required_but_not_run": [{"suite": r["suite"], "blocked_reason": r["blocked_reason"]} for r in not_run],
    }
