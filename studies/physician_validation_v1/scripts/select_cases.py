#!/usr/bin/env python3
"""Build reproducible source-case candidate queues without publishing case text.

Public outputs contain IDs/metadata/hashes only. Raw HealthBench Professional and
Real-POCQi text is written only under --vault.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

STUDY_SEED = "clinical-ai-eval-physician-validation-v1"

HBP_REVISION = "371b4ec5b622470faf375d3e3018a617cb164a94"
HBP_FILENAME = "healthbench_professional_eval.jsonl"
HBP_URL = (
    "https://huggingface.co/datasets/openai/healthbench-professional/resolve/"
    f"{HBP_REVISION}/{HBP_FILENAME}?download=true"
)
HBP_SHA256 = "d44b08e6e952e04c945e2c406f02533d9e7a989a84e35820ee7efdff20c9e4e2"
HBP_EXPECTED_TOTAL = 525
HBP_EXPECTED_CONSULT = 236
HBP_STRATA = {
    ("good_faith", "typical"): {"available": 84, "quota": 53},
    ("good_faith", "difficult"): {"available": 59, "quota": 38},
    ("red_teaming", "difficult"): {"available": 93, "quota": 59},
}

REAL_POCQI_ROWS = "https://datasets-server.huggingface.co/rows"
REAL_POCQI_DATASET = "jjfenglab/Real-POCQi"
REAL_POCQI_CONFIG = "default"
REAL_POCQI_SPLIT = "questions"
REAL_POCQI_EXPECTED_TOTAL = 620

PATIENT_SIGNALS = re.compile(
    r"\b(patient|pt\.?|my patient|this patient|year[- ]old|years? old|yo\b|"
    r"presents?|presented|admitted|history of|whose|with an? [a-z])",
    re.I,
)
DECISION_SIGNALS = re.compile(
    r"\b(should|management|manage|treat|treatment|dose|dosing|appropriate|admit|"
    r"proceed|start|stop|recommend|next step|work[- ]?up|diagnos|surgery|therapy|"
    r"antibiotic|medication|monitor|follow[- ]?up|discharge|refer)\b",
    re.I,
)
GENERIC_RESEARCH_START = re.compile(
    r"^\s*(what percentage|how common|what is the prevalence|which trial|"
    r"what trial|meta[- ]?analysis|systematic review|what study|study of|"
    r"mechanism of|how do .{0,40} function|what is .{0,40} trial)\b",
    re.I,
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_rank(source_id: str, namespace: str) -> str:
    return hashlib.sha256(f"{STUDY_SEED}|{namespace}|{source_id}".encode()).hexdigest()


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "clinical-ai-eval-validation-v1/0.1"})
    with urllib.request.urlopen(req, timeout=120) as response:
        return response.read()


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def hbp_queue(vault: Path, public_dir: Path) -> None:
    raw = fetch(HBP_URL)
    digest = sha256_bytes(raw)
    if digest != HBP_SHA256:
        raise RuntimeError(f"HealthBench Professional digest mismatch: {digest}")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    if len(rows) != HBP_EXPECTED_TOTAL:
        raise RuntimeError(f"expected {HBP_EXPECTED_TOTAL} HBP rows, got {len(rows)}")

    consult = [r for r in rows if r.get("use_case") == "consult"]
    if len(consult) != HBP_EXPECTED_CONSULT:
        raise RuntimeError(f"expected {HBP_EXPECTED_CONSULT} HBP consult rows, got {len(consult)}")

    public_rows: list[dict] = []
    private_rows: list[dict] = []
    for (kind, difficulty), spec in HBP_STRATA.items():
        stratum = [r for r in consult if r.get("type") == kind and r.get("difficulty") == difficulty]
        if len(stratum) != spec["available"]:
            raise RuntimeError(
                f"HBP stratum {(kind, difficulty)} expected {spec['available']} rows, got {len(stratum)}"
            )
        stratum.sort(key=lambda r: stable_rank(str(r["id"]), "hbp"))
        for pos, record in enumerate(stratum, start=1):
            source_id = str(record["id"])
            conversation = record.get("conversation")
            source_content_hash = sha256_bytes(canonical_json({
                "conversation": conversation,
                "rubric_items": record.get("rubric_items"),
                "physician_response": record.get("physician_response"),
            }))
            pub = {
                "source_dataset": "openai/healthbench-professional",
                "source_revision": HBP_REVISION,
                "source_file_sha256": HBP_SHA256,
                "source_id": source_id,
                "type": kind,
                "difficulty": difficulty,
                "specialty": record.get("specialty", ""),
                "stratum_priority": pos,
                "stratum_quota": spec["quota"],
                "selection_rank_sha256": stable_rank(source_id, "hbp"),
                "source_content_sha256": source_content_hash,
                "status": "candidate",
            }
            public_rows.append(pub)
            private_rows.append({**pub, "source_record": record})

    public_rows.sort(key=lambda r: (r["type"], r["difficulty"], int(r["stratum_priority"])))
    write_csv(
        public_dir / "healthbench_professional_candidate_queue.csv",
        public_rows,
        [
            "source_dataset", "source_revision", "source_file_sha256", "source_id", "type",
            "difficulty", "specialty", "stratum_priority", "stratum_quota",
            "selection_rank_sha256", "source_content_sha256", "status",
        ],
    )

    private_path = vault / "sources" / "healthbench_professional_candidates.private.jsonl"
    private_path.parent.mkdir(parents=True, exist_ok=True)
    with private_path.open("w", encoding="utf-8") as f:
        for row in private_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"HealthBench Professional: {len(public_rows)} care-consult candidates queued; final quota 150")
    print(f"  public:  {public_dir / 'healthbench_professional_candidate_queue.csv'}")
    print(f"  private: {private_path}")


def fetch_real_pocqi_questions() -> list[dict]:
    all_rows: list[dict] = []
    offset = 0
    while True:
        query = urllib.parse.urlencode({
            "dataset": REAL_POCQI_DATASET,
            "config": REAL_POCQI_CONFIG,
            "split": REAL_POCQI_SPLIT,
            "offset": offset,
            "length": 100,
        })
        payload = json.loads(fetch(f"{REAL_POCQI_ROWS}?{query}").decode("utf-8"))
        batch = [entry["row"] for entry in payload.get("rows", [])]
        all_rows.extend(batch)
        total = int(payload.get("num_rows_total", len(all_rows)))
        offset += len(batch)
        if not batch or offset >= total:
            break
    return all_rows


def real_pocqi_eligible_text(text: str) -> bool:
    text = text or ""
    if GENERIC_RESEARCH_START.search(text):
        return False
    return bool(PATIENT_SIGNALS.search(text) and DECISION_SIGNALS.search(text))


def real_pocqi_queue(vault: Path, public_dir: Path) -> None:
    rows = fetch_real_pocqi_questions()
    if len(rows) != REAL_POCQI_EXPECTED_TOTAL:
        raise RuntimeError(f"expected {REAL_POCQI_EXPECTED_TOTAL} Real-POCQi questions, got {len(rows)}")

    corpus_digest = sha256_bytes(canonical_json(sorted(rows, key=lambda r: str(r.get("question_id", "")))))
    eligible = [r for r in rows if real_pocqi_eligible_text(str(r.get("question_text", "")))]
    eligible.sort(key=lambda r: stable_rank(str(r["question_id"]), "real-pocqi"))

    public_rows: list[dict] = []
    private_rows: list[dict] = []
    for pos, record in enumerate(eligible, start=1):
        qid = str(record["question_id"])
        text = str(record.get("question_text", ""))
        pub = {
            "source_dataset": REAL_POCQI_DATASET,
            "source_corpus_sha256": corpus_digest,
            "source_id": qid,
            "specialty": record.get("specialty", ""),
            "candidate_priority": pos,
            "target_validated_cases": 50,
            "selection_rank_sha256": stable_rank(qid, "real-pocqi"),
            "source_text_sha256": sha256_bytes(text.encode("utf-8")),
            "status": "candidate",
        }
        public_rows.append(pub)
        private_rows.append({**pub, "question_text": text})

    write_csv(
        public_dir / "real_pocqi_candidate_queue.csv",
        public_rows,
        [
            "source_dataset", "source_corpus_sha256", "source_id", "specialty",
            "candidate_priority", "target_validated_cases", "selection_rank_sha256",
            "source_text_sha256", "status",
        ],
    )
    private_path = vault / "sources" / "real_pocqi_candidates.private.jsonl"
    private_path.parent.mkdir(parents=True, exist_ok=True)
    with private_path.open("w", encoding="utf-8") as f:
        for row in private_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Real-POCQi: {len(eligible)} heuristic patient-specific candidates queued; final physician-valid target 50")
    print(f"  public:  {public_dir / 'real_pocqi_candidate_queue.csv'}")
    print(f"  private: {private_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", required=True, type=Path, help="Private directory outside the public repo")
    parser.add_argument(
        "--public-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data",
        help="ID-only public manifest output directory",
    )
    parser.add_argument("--skip-real-pocqi", action="store_true")
    args = parser.parse_args()

    args.vault.mkdir(parents=True, exist_ok=True)
    args.public_dir.mkdir(parents=True, exist_ok=True)
    hbp_queue(args.vault, args.public_dir)
    if not args.skip_real_pocqi:
        real_pocqi_queue(args.vault, args.public_dir)


if __name__ == "__main__":
    main()
