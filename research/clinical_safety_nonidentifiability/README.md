# Clinical safety non-identifiability package

This directory contains a complete, replayable candidate result for clinical-AI evaluation.

## Result

1. A perfect safety evaluator using only the exposed case and response exists **iff** the unsafe-action label is constant on every observation fiber.
2. One same-input, opposite-label world pair defeats every deterministic judge and gives every randomized judge a worst-case error lower bound of **1/2**.
3. A set of added clinical questions repairs the problem **iff** it separates every opposite-label pair compatible with the same observed case.
4. Finding a minimum critical-question closure is NP-complete by direct reduction from Hitting Set.

The full statements and proofs are in [`MANUSCRIPT.md`](MANUSCRIPT.md).

## Replay

From the repository root:

```bash
python -m unittest tests_unit.test_identifiability
python research/clinical_safety_nonidentifiability/reproduce.py --check
```

To generate a full report with implementation and fixture hashes:

```bash
python research/clinical_safety_nonidentifiability/reproduce.py \
  --output out/clinical_safety_identifiability.json
```

## Files

- `MANUSCRIPT.md` — formal result, proofs, implications, prior-art boundary.
- `witnesses.json` — synthetic finite clinical worlds and Hitting Set instance.
- `EXPECTED_RESULT.json` — semantic replay certificate.
- `reproduce.py` — dependency-free replay script.
- `../../caeval/identifiability.py` — reusable implementation.
- `../../tests_unit/test_identifiability.py` — unit tests.

## Scientific status

Mathematically complete within the stated finite-world model; not peer reviewed; not a clinical validation study; not patient-specific advice. The claimed novelty requires independent literature review.
