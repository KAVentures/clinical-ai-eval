"""Local, blinded manual candidate-response exchange for single-turn families.

The operator stages approved, non-identifiable clinician vignettes on the
server. The browser never accepts a filesystem path or contacts a vendor.
Returned responses are self-attested and unscored, not clinical evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import buyer_agent as buyer, buyer_plan, executors, manifest, packsource, pipeline, selection

MAX_PACK_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_CASES = 100
MAX_SLOTS = 1000


def _directory(root, name):
    path = Path(root).resolve() / name
    if path.is_symlink():
        raise buyer.BuyerError("symbolic link not allowed in exchange directory")
    return path


def _identifier(value, *, digest=False):
    if not isinstance(value, str) or not buyer.IDENTIFIER.fullmatch(value):
        raise buyer.BuyerError("invalid local identifier")
    if digest and (len(value) != 64 or any(c not in "0123456789abcdef" for c in value)):
        raise buyer.BuyerError("invalid exchange digest")
    return value


def packs(directory):
    buyer.load(directory)
    base = _directory(directory, "approved-packs")
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir()
                  if p.is_dir() and not p.is_symlink() and buyer.IDENTIFIER.fullmatch(p.name))[:100]


def _pack(directory, pack_id):
    folder = _directory(directory, "approved-packs") / _identifier(pack_id)
    if folder.is_symlink() or not folder.is_dir():
        raise buyer.BuyerError("case pack is not staged in approved-packs")
    # Only the canonical clinician pack files may be read. No arbitrary paths,
    # links, external corpus, executable attachment or uploaded pack is accepted.
    total = 0
    for name in ("cases.json", "cases.jsonl", "pack.json"):
        p = folder / name
        if p.is_symlink() or (p.exists() and not p.is_file()):
            raise buyer.BuyerError("invalid case pack file")
        if p.is_file():
            total += p.stat().st_size
    if total > MAX_PACK_BYTES:
        raise buyer.BuyerError("case pack exceeds manual exchange limit")
    if (folder / "cases.json").exists() and (folder / "cases.jsonl").exists():
        raise buyer.BuyerError("stage exactly one case file")
    try:
        cases, descriptor = packsource.resolve({"source": str(folder)}, "clinician_vignette")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        raise buyer.BuyerError("cannot resolve the staged clinician pack") from exc
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise buyer.BuyerError("case pack must contain 1–100 clinician vignettes")
    ids = []
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get("item_id"), str)
                or not case["item_id"] or not isinstance(case.get("input_text"), str)
                or not case["input_text"].strip()):
            raise buyer.BuyerError("invalid vignette identity or input")
        ids.append(case["item_id"])
    if len(ids) != len(set(ids)):
        raise buyer.BuyerError("duplicate case identifier")
    return cases, descriptor


def _build(directory, plan_id, pack_id):
    state = buyer.load(directory)
    plan = buyer_plan.load(directory, plan_id)
    scope = {k: state.get(k, "") for k in
             ("project_name", "intended_use", "setting", "jurisdiction", "created_at")}
    if (not plan["definitions_current"] or plan["buyer_scope"] != scope
            or plan["vendor_id"] not in {v["vendor_id"] for v in state["vendors"]}):
        raise buyer.BuyerError("plan is stale; regenerate before preparing prompts")
    profiles = plan["plan"]["target_profile"]["types"]
    selected = selection.select_suites(profiles)
    if selected != plan["plan"]["selection"]:
        raise buyer.BuyerError("saved selection differs from canonical rules")
    families = selected["runnable_suites"]
    if not families or any(executors.resolve(f).executor_id != executors.GENERIC_PAIRED_TEXT
                           for f in families):
        raise buyer.BuyerError("manual exchange supports only single-turn text plans; no families silently omitted")
    cases, desc = _pack(directory, pack_id)
    slots = {}
    for family in families:
        def capture(row):
            cell = row["cell_id"]
            prompt = row["input_text"]
            if cell in slots:
                if slots[cell]["prompt"] != prompt or family in slots[cell]["families"]:
                    raise buyer.BuyerError("conflicting or duplicate canonical cell")
                slots[cell]["families"].append(family)
            else:
                slots[cell] = {"prompt": prompt, "families": [family]}
            if len(slots) > MAX_SLOTS:
                raise buyer.BuyerError("too many prompt slots")
            return "(placeholder)"
        try:
            pipeline.generate_responses(capture, pipeline.load_family(family), cases)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            raise buyer.BuyerError("canonical prompt generation failed for " + family) from exc
    # Baseline originals shared across families are collected once, exactly
    # as the existing manual CLI response mapping does.
    if not slots:
        raise buyer.BuyerError("no prompts generated")
    record = {
        "schema": "manual-candidate-exchange/1", "plan_id": plan_id,
        "vendor_id": plan["vendor_id"], "target": plan["plan"]["target"],
        "pack_id": pack_id, "pack_content_hash": desc["content_hash"],
        "pack_review_status_recorded": desc.get("review_status"),
        "family_ids": families, "slots": slots,
        "scientific_definitions": {
            rel: manifest.hash_file(manifest.definition_file(rel))
            for rel in manifest.EXECUTOR_DEFINITION_SOURCES["generic_paired_text"]
        },
        "decision_grade": False,
    }
    digest = buyer_plan._digest(record)
    result = {**record, "exchange_id": digest}
    if len(buyer_plan._canonical(result)) > MAX_REQUEST_BYTES:
        raise buyer.BuyerError("prompt exchange exceeds size limit")
    return result


def _write_once(path, record):
    if path.is_symlink():
        raise buyer.BuyerError("exchange file may not be a symbolic link")
    try:
        buyer._write_json(path, record, create=True)
    except FileExistsError:
        if _read(path) != record:
            raise buyer.BuyerError("existing exchange differs; refusing overwrite")


def _read(path):
    if path.is_symlink() or not path.is_file():
        raise buyer.BuyerError("exchange artifact not found")
    with path.open("rb") as handle:
        raw = handle.read(MAX_REQUEST_BYTES + 1)
    return _parse(raw)


def _parse(raw):
    if len(raw) > MAX_REQUEST_BYTES:
        raise buyer.BuyerError("exchange file exceeds size limit")
    def unique(pairs):
        obj = {}
        for k, v in pairs:
            if k in obj:
                raise buyer.BuyerError("duplicate JSON key")
            obj[k] = v
        return obj
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise buyer.BuyerError("invalid exchange JSON") from exc


def prepare(directory, plan_id, pack_id):
    record = _build(directory, plan_id, pack_id)
    folder = _directory(directory, "manual-exchanges")
    folder.mkdir(mode=0o700, exist_ok=True)
    _write_once(folder / (record["exchange_id"] + ".json"), record)
    return _template(record)


def _load(directory, exchange_id):
    identifier = _identifier(exchange_id, digest=True)
    record = _read(_directory(directory, "manual-exchanges") / (identifier + ".json"))
    if not isinstance(record, dict) or record.get("exchange_id") != identifier:
        raise buyer.BuyerError("invalid exchange snapshot")
    if buyer_plan._digest({k: v for k, v in record.items() if k != "exchange_id"}) != identifier:
        raise buyer.BuyerError("exchange integrity mismatch")
    if _build(directory, record["plan_id"], record["pack_id"]) != record:
        raise buyer.BuyerError("plan, pack or scientific definitions changed; prepare again")
    return record


def _opaque(record, cell):
    return buyer_plan._digest([record["exchange_id"], cell])


def _template(record):
    # Expose only text actually supplied to the candidate and opaque slot IDs.
    # No hidden perturbation metadata, clinical reference labels or family keys.
    rows = sorted(({"slot": _opaque(record, cell), "prompt": slot["prompt"]}
                   for cell, slot in record["slots"].items()), key=lambda r: r["slot"])
    return {"request": {"schema": "manual-response-template/1",
                        "exchange_id": record["exchange_id"], "prompts": rows},
            "submission": {"operator": "", "tested_product_version": "",
                           "answers": [{"slot": r["slot"], "status": "", "answer": ""}
                                       for r in rows]}}


def template(directory, exchange_id):
    return _template(_load(directory, exchange_id))


def import_response(directory, raw):
    obj = _parse(raw)
    if not isinstance(obj, dict) or set(obj) != {"request", "submission"} or not isinstance(obj["request"], dict):
        raise buyer.BuyerError("invalid exchange response")
    record = _load(directory, obj["request"].get("exchange_id"))
    expected = _template(record)
    if obj["request"] != expected["request"]:
        raise buyer.BuyerError("prompt template changed or belongs to another exchange")
    sub = obj["submission"]
    if not isinstance(sub, dict) or set(sub) != {"operator", "tested_product_version", "answers"}:
        raise buyer.BuyerError("invalid submission fields")
    for key in ("operator", "tested_product_version"):
        if not isinstance(sub[key], str) or not sub[key].strip() or len(sub[key]) > 300:
            raise buyer.BuyerError("explicit operator and product version required")
    if sub["tested_product_version"] != record["target"]["version"]:
        raise buyer.BuyerError("tested product version differs from saved plan")
    reverse = {_opaque(record, cell): cell for cell in record["slots"]}
    answers = sub["answers"]
    if not isinstance(answers, list) or len(answers) != len(reverse):
        raise buyer.BuyerError("one explicit answer status is required for every slot")
    seen = set()
    mapping = {}
    for row in answers:
        if not isinstance(row, dict) or set(row) != {"slot", "status", "answer"}:
            raise buyer.BuyerError("invalid answer row")
        slot, status, answer = row["slot"], row["status"], row["answer"]
        if not isinstance(slot, str) or slot not in reverse or slot in seen:
            raise buyer.BuyerError("unknown or duplicate response slot")
        seen.add(slot)
        if not isinstance(answer, str) or len(answer) > 50000:
            raise buyer.BuyerError("invalid response text")
        if status == "provided" and answer.strip():
            mapping[reverse[slot]] = answer
        elif status != "not_available" or answer.strip():
            raise buyer.BuyerError("use provided with nonblank answer or not_available with empty answer")
    content = {
        "schema": "manual-response-submission/1", "exchange_id": record["exchange_id"],
        "submission": sub, "mapping": mapping, "n_expected": len(reverse),
        "n_provided": len(mapping), "status": "complete_unscored" if len(mapping) == len(reverse) else "incomplete",
        "claim_authority": "operator_attested_unverified_responses",
        "decision_grade": False,
    }
    digest = buyer_plan._digest(content)
    content["submission_id"] = digest
    if len(buyer_plan._canonical(content)) > MAX_REQUEST_BYTES:
        raise buyer.BuyerError("stored response exceeds size limit")
    folder = _directory(directory, "manual-submissions")
    folder.mkdir(mode=0o700, exist_ok=True)
    _write_once(folder / (digest + ".json"), content)
    return {k: v for k, v in content.items() if k not in ("submission", "mapping")}


def _submission(directory, submission_id):
    identifier = _identifier(submission_id, digest=True)
    content = _read(_directory(directory, "manual-submissions") / (identifier + ".json"))
    if (not isinstance(content, dict) or content.get("submission_id") != identifier
            or buyer_plan._digest({k: v for k, v in content.items() if k != "submission_id"}) != identifier):
        raise buyer.BuyerError("response submission integrity mismatch")
    record = _load(directory, content["exchange_id"])
    mapping = content.get("mapping")
    if (not isinstance(mapping, dict) or not set(mapping) <= set(record["slots"])
            or any(not isinstance(v, str) or not v.strip() for v in mapping.values())
            or content.get("n_expected") != len(record["slots"])
            or content.get("n_provided") != len(mapping)
            or content.get("status") != ("complete_unscored" if len(mapping) == len(record["slots"]) else "incomplete")):
        raise buyer.BuyerError("submission coverage mismatch")
    return content


def submission_status(directory, submission_id):
    content = _submission(directory, submission_id)
    return {k: v for k, v in content.items() if k not in ("submission", "mapping")}


def responses(directory, submission_id):
    """Exact canonical manual-subject mapping; no score or clinical claim."""
    content = _submission(directory, submission_id)
    if content["status"] != "complete_unscored":
        raise buyer.BuyerError("incomplete responses cannot be handed to the scoring engine")
    return content["mapping"]


def import_file(directory, path):
    with Path(path).expanduser().open("rb") as handle:
        return import_response(directory, handle.read(MAX_REQUEST_BYTES + 1))
