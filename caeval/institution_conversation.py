"""Institutional patient-conversation job queue.

Only approved literal-loopback connectors, pre-staged patient-world packs,
and current scientific plans. Browser prepares/cancels; trusted worker
explicitly executes. Separate from the generic single-turn review pipeline.
"""
from __future__ import annotations
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from . import (adapters, buyer_agent as buyer, buyer_assessment as assessment,
               buyer_manual as manual, buyer_plan, buyer_research, claim,
               cli, institution_access as access, manifest, packsource, pipeline,
               project, selection)
from .patient.session import MAX_TURNS, STRESS_TESTS

MAX_JOBS=100
MAX_PACK_BYTES=2*1024*1024
MAX_CONFIG_BYTES=64*1024
FAMILY="patient_red_flag"


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS conversation_jobs(
      id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES users(id),
      binding TEXT NOT NULL, digest TEXT NOT NULL, status TEXT NOT NULL,
      result TEXT NOT NULL DEFAULT '{}')""")


def _actor(db,actor,*roles):
    access.require(actor,*roles)
    row=db.execute("SELECT role,enabled FROM users WHERE id=?",(actor["user_id"],)).fetchone()
    if not row or not row["enabled"] or row["role"]!=actor["role"]:
        raise access.AccessDenied("active authorized account required")


def _root(directory):
    buyer.load(directory)
    p=manual._directory(directory,"conversation-jobs")
    p.mkdir(mode=0o700,exist_ok=True)
    return p


def _read(path,limit):
    if path.is_symlink() or not path.is_file():
        raise buyer.BuyerError("approved regular input required")
    with path.open("rb") as handle:
        raw=handle.read(limit+1)
    if len(raw)>limit:
        raise buyer.BuyerError("input exceeds approved size limit")
    return raw


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _options(directory,folder,files=False):
    base=manual._directory(directory,folder)
    if not base.is_dir():
        return []
    return sorted(p.stem if files else p.name for p in base.iterdir()
                  if not p.is_symlink() and
                  (p.is_file() and p.suffix==".json" if files else p.is_dir())
                  and buyer.IDENTIFIER.fullmatch(p.stem if files else p.name))[:100]


def options(directory):
    return {"connectors":_options(directory,"approved-conversations",True),
            "packs":["builtin:public_smoke"]+_options(directory,"approved-patient-packs"),
            "panels":assessment.panels(directory)}


def _connector(directory,identifier,plan):
    path=manual._directory(directory,"approved-conversations")/(manual._identifier(identifier)+".json")
    cfg=manual._parse(_read(path,MAX_CONFIG_BYTES))
    from . import conversation_protocol
    common={"vendor_id","target_name","target_version","url","headers","timeout_seconds"}
    if not isinstance(cfg,dict) or set(cfg) not in (common|{"history_field","answer_path","session_field"},common|{"protocol"}):
        raise buyer.BuyerError("invalid staged conversation connector")
    if "protocol" in cfg and cfg["protocol"]!=conversation_protocol.PROTOCOL:
        raise buyer.BuyerError("invalid staged conversation connector")
    target=plan["plan"]["target"]
    if (cfg["vendor_id"]!=plan["vendor_id"] or cfg["target_name"]!=target["name"]
            or cfg["target_version"]!=target["version"]):
        raise buyer.BuyerError("connector must match exact planned candidate")
    subject={"kind":"http","modality":"conversation","local_only":True,
             "url":cfg["url"],"history_field":cfg.get("history_field","messages"),
             "answer_path":cfg.get("answer_path","answer"),"session_field":cfg.get("session_field",""),
             "headers":cfg["headers"],"timeout":cfg["timeout_seconds"],"retries":1}
    if "protocol" in cfg:
        subject.update(protocol=cfg["protocol"],target_name=cfg["target_name"],
                       name=cfg["target_name"],version=cfg["target_version"])
    cli._conversation_spec(subject)
    return subject


def staged_connectors(directory):
    """Validated public connector metadata only; never return URL or headers."""
    results=[]
    for identifier in _options(directory,"approved-conversations",True):
        try:
            path=manual._directory(directory,"approved-conversations")/(identifier+".json")
            cfg=manual._parse(_read(path,MAX_CONFIG_BYTES))
            vendor=manual._identifier(cfg["vendor_id"])
            name=buyer_plan._text(cfg["target_name"],"target_name",300)
            version=buyer_plan._text(cfg["target_version"],"target_version",300)
            subject=_connector(directory,identifier,{"vendor_id":vendor,
                "plan":{"target":{"name":name,"version":version}}})
            results.append({"connector_id":identifier,"vendor_id":vendor,"target_name":name,
                "target_version":version,"protocol":subject.get("protocol","custom-conversation-json")})
        except (OSError,ValueError,RuntimeError,TypeError,KeyError,AttributeError,RecursionError):
            continue
    return results


def _pack(directory,identifier):
    if identifier=="builtin:public_smoke":
        cases,desc=packsource.resolve({"source":identifier},"patient_worlds")
        files={}
    else:
        folder=manual._directory(directory,"approved-patient-packs")/manual._identifier(identifier)
        if folder.is_symlink() or not folder.is_dir():
            raise buyer.BuyerError("approved patient-world pack required")
        names={p.name for p in folder.iterdir()}
        if not {"cases.json"}<=names<={"cases.json","pack.json"}:
            raise buyer.BuyerError("patient pack must contain only cases.json and optional pack.json")
        files={"pack/"+name:_read(folder/name,MAX_PACK_BYTES) for name in names}
        if sum(map(len,files.values()))>MAX_PACK_BYTES:
            raise buyer.BuyerError("patient pack exceeds size limit")
        with tempfile.TemporaryDirectory(prefix=".conversation-pack-",dir=_root(directory)) as tmp:
            dest=Path(tmp)/identifier
            dest.mkdir(mode=0o700)
            for name,raw in files.items():
                (dest/Path(name).name).write_bytes(raw)
            cases,desc=packsource.resolve({"source":str(dest)},"patient_worlds")
    if not cases or len({c.case_id for c in cases})!=len(cases):
        raise buyer.BuyerError("nonempty uniquely identified patient cases required")
    for c in cases:
        if not c.worlds or len({w.world_id for w in c.worlds})!=len(c.worlds):
            raise buyer.BuyerError("nonempty uniquely identified worlds required")
    budget=sum(len(c.worlds) for c in cases)*len(STRESS_TESTS)*MAX_TURNS+4
    if budget>adapters.LOCAL_MAX_CALLS:
        raise buyer.BuyerError("patient pack exceeds local call budget")
    # Remove temporary path; freeze semantic and authority-bearing metadata.
    desc={k:v for k,v in desc.items() if k!="source"}
    return cases,desc,files,budget


def _material(directory,plan_id,connector_id,pack_id,panel_id,governance):
    plan=buyer_plan.load(directory,plan_id)
    state=buyer.load(directory)
    scope={k:state.get(k,"") for k in ("project_name","intended_use","setting","jurisdiction","created_at")}
    if (not plan["definitions_current"] or plan["buyer_scope"]!=scope
            or plan["vendor_id"] not in {v["vendor_id"] for v in state["vendors"]}):
        raise buyer.BuyerError("current saved scientific plan and buyer scope required")
    profiles=plan["plan"]["target_profile"]["types"]
    selected=selection.select_suites(profiles)
    if selected!=plan["plan"]["selection"] or selected["runnable_suites"]!=[FAMILY]:
        raise buyer.BuyerError("conversation queue requires a patient-only runnable scope")
    subject=_connector(directory,connector_id,plan)
    _,desc,files,budget=_pack(directory,pack_id)
    raw,panel=assessment._panel(directory,panel_id)
    real=any(not j["mock"] for j in panel["judges"])
    mode="internal_regression" if not any(j["mock"] for j in panel["judges"]) and not desc["is_builtin"] else "demonstration"
    target=plan["plan"]["target"]
    vendor=next(v for v in state["vendors"] if v["vendor_id"]==plan["vendor_id"])
    config=project.template("Institutional patient conversation assessment",mode)
    config["target"]={"name":target["name"],"version":target["version"],"vendor":vendor["name"]}
    config["target_profile"]["types"]=profiles
    config["intake"]=plan["plan"]["intake"]
    config["governance"]=assessment._governance(governance)
    config["subject"]=subject
    config["case_pack"]=({"source":pack_id} if desc["is_builtin"] else
                         {"source":"pack","pack_id":desc["pack_id"],"version":desc["version"]})
    config["panel"]={"config":"panel.toml"}
    config["procurement"]={"conditions_hash":""}
    config["clinical_review"]={"reviewers":[],"resolution_mode":"consensus","tie_reviewer":""}
    project.Project(path=Path(directory),data=config).require_valid()
    files.update({"project.yaml":buyer_plan._canonical(config),"panel.toml":raw})
    defs=set(manifest.DEFINITION_SOURCES)|set(manifest.EXECUTOR_DEFINITION_SOURCES["patient_episode"])
    # Freeze the transport and orchestration code too, even where older
    # canonical evidence manifests did not include them.
    defs.update(("caeval/adapters.py","caeval/patient/session.py","caeval/patient/run.py",
                 "caeval/cli.py","caeval/lifecycle.py","tests/"+FAMILY+"/family.yaml"))
    if subject.get("protocol"):
        defs.update(("caeval/conversation_protocol.py","caeval/candidate_protocol.py"))
    definitions={k:manifest.hash_file(manifest.definition_file(k)) for k in sorted(defs)}
    if not all(definitions.values()):
        raise buyer.BuyerError("scientific definition missing")
    binding={"schema":"institution-conversation-job/1","plan_id":plan_id,
             "connector_id":connector_id,"pack_id":pack_id,"panel_id":panel_id,
             "governance":config["governance"],"pack_descriptor":desc,
             "target":config["target"],"vendor_id":plan["vendor_id"],
             "mode":mode,"real_judges":real,"max_candidate_calls":budget,
             "required_but_not_run":selected["required_but_not_run"],
             "file_sha256":{k:_sha(v) for k,v in files.items()},"definitions":definitions,
             "decision_grade":False}
    return json.loads(buyer_plan._canonical(binding)),files


def prepare(directory,actor,spec):
    if set(spec)!={"plan_id","connector_id","pack_id","panel_id","governance"}:
        raise access.AccessDenied("exact conversation job inputs required")
    with access.db(directory) as db:
        _schema(db)
        _actor(db,actor,"admin","operator")
    binding,files=_material(directory,**spec)
    ident="conversation-"+uuid.uuid4().hex
    digest=buyer_plan._digest(binding)
    with access.db(directory) as db:
        _schema(db)
        db.execute("BEGIN IMMEDIATE")
        _actor(db,actor,"admin","operator")
        if db.execute("SELECT count(*) FROM conversation_jobs").fetchone()[0]>=MAX_JOBS:
            raise access.AccessDenied("conversation job limit")
        # No worker starts from a browser call.
        db.execute("INSERT INTO conversation_jobs(id,owner,binding,digest,status) VALUES(?,?,?,?,?)",
                   (ident,actor["user_id"],json.dumps(binding),digest,"queued"))
        access._audit(db,actor["user_id"],"conversation-queued",ident)
    return {"id":ident,"binding_hash":digest,"status":"queued","decision_grade":False}


def _row(db,identifier):
    row=db.execute("SELECT * FROM conversation_jobs WHERE id=?",(manual._identifier(identifier),)).fetchone()
    if not row:
        raise access.AccessDenied("unknown conversation job")
    row=dict(row)
    row["binding"]=json.loads(row["binding"])
    row["result"]=json.loads(row["result"])
    if buyer_plan._digest(row["binding"])!=row["digest"]:
        raise access.AccessDenied("conversation binding changed")
    return row


def cancel(directory,actor,identifier):
    with access.db(directory) as db:
        _schema(db)
        db.execute("BEGIN IMMEDIATE")
        _actor(db,actor,"admin","operator")
        row=_row(db,identifier)
        if actor["role"]!="admin" and actor["user_id"]!=row["owner"]:
            raise access.AccessDenied("job owner or administrator required")
        if db.execute("UPDATE conversation_jobs SET status='cancelled' WHERE id=? AND status='queued'",(identifier,)).rowcount!=1:
            raise access.AccessDenied("only queued jobs can be cancelled")
        access._audit(db,actor["user_id"],"conversation-cancelled",identifier)


def _workspace(directory,identifier):
    dest=_root(directory)/manual._identifier(identifier)
    if dest.is_symlink() or not dest.is_dir():
        raise buyer.BuyerError("missing or linked conversation workspace")
    return dest


def _expected_episodes(cases):
    from .patient.stress import applicable
    expected=set()
    skipped=[]
    for case in cases:
        for world in case.worlds:
            for stress in sorted(STRESS_TESTS):
                if stress=="BASELINE":
                    expected.add((case.case_id,world.world_id,stress))
                    continue
                ok,why=applicable(case,world.world_id,stress)
                if ok:
                    expected.add((case.case_id,world.world_id,stress))
                else:
                    skipped.append({"case_id":case.case_id,"world_id":world.world_id,
                                    "stress_test":stress,"reason":why})
    from .patient.run import coverage
    return expected,coverage(cases,sorted(set(STRESS_TESTS)-{"BASELINE"}),skipped)


def _verify_episode_inventory(cases,episodes,cells,reported_coverage):
    from .util import stable_hash_text
    expected,expected_coverage=_expected_episodes(cases)
    observed=[(e["case_id"],e["world_id"],e["stress_test"]) for e in episodes]
    ids=[e["episode_id"] for e in episodes]
    if (set(observed)!=expected or len(observed)!=len(expected)
            or len(ids)!=len(set(ids)) or len(cells)!=len(episodes)
            or reported_coverage!=expected_coverage):
        raise buyer.BuyerError("incomplete or inconsistent patient stress coverage")
    traces={}
    for e in episodes:
        digest=stable_hash_text("|".join(f"{t['speaker']}:{t['text']}" for t in e["turns"]))
        if e["trace_hash"]!=digest:
            raise buyer.BuyerError("episode transcript hash changed")
        traces[e["episode_id"]]=digest
    scored=[(cell["patient"]["episode_id"],cell["patient"]["trace_hash"]) for cell in cells]
    if len(scored)!=len(traces) or len({eid for eid,_ in scored})!=len(scored) or dict(scored)!=traces:
        raise buyer.BuyerError("scored cells do not match episode traces")


def _verify(directory,row):
    dest=_workspace(directory,row["id"])
    b=row["binding"]
    buyer_research._safe_tree(dest)
    for k,v in b["file_sha256"].items():
        if k not in ("project.yaml","panel.toml","pack/cases.json","pack/pack.json"):
            raise buyer.BuyerError("unexpected input inventory")
        if _sha(_read(dest/k,MAX_PACK_BYTES))!=v:
            raise buyer.BuyerError("frozen conversation input changed")
    if any(manifest.hash_file(manifest.definition_file(k))!=v for k,v in b["definitions"].items()):
        raise buyer.BuyerError("scientific definitions changed")
    cfg=project.load(dest)
    cfg.require_valid()
    panel=pipeline.load_panel(dest/"panel.toml")
    spec=dict(cfg.data["case_pack"])
    if spec["source"]=="pack":
        spec["source"]=str(dest/"pack")
    cases,desc=packsource.resolve(spec,"patient_worlds")
    if {k:v for k,v in desc.items() if k!="source"}!=b["pack_descriptor"]:
        # Staged packs with no pack_id are resolved with the original ID
        # above; reject any other authority or content change.
        raise buyer.BuyerError("patient pack descriptor changed")
    out=dest/"out"
    if out.is_symlink() or {p.name for p in out.iterdir()}!={"run_"+FAMILY}:
        raise buyer.BuyerError("unexpected family output")
    ws=out/("run_"+FAMILY)
    meta=buyer_research._preflight(ws)
    if meta["family_id"]!=FAMILY or meta["executor"]!="patient_episode" or manifest.verify_manifest(ws)["verdict"]!=manifest.VALID:
        raise buyer.BuyerError("invalid patient episode package")
    analysis=json.loads(buyer_research.verified_bytes(ws,meta,"analysis"))
    recorded=json.loads(buyer_research.verified_bytes(ws,meta,"plan_binding"))
    actual=claim.build_binding(cfg,FAMILY,[j["name"] for j in panel["judges"]],desc["content_hash"])
    if recorded["plan_hash"]!=claim.plan_fingerprint(recorded):
        raise buyer.BuyerError("invalid canonical plan digest")
    claim.verify_binding(recorded,actual)
    from . import lifecycle
    cells=[json.loads(line) for line in buyer_research.verified_bytes(ws,meta,"results").splitlines() if line.strip()]
    conformance=lifecycle.conformance_from(panel,cells,False)
    if conformance!=analysis["conformance_level"] or len(cells)!=analysis["n_cells"]:
        raise buyer.BuyerError("patient score coverage or conformance changed")
    family=pipeline.load_family(FAMILY)
    target=analysis["target"]
    derived=claim.compute(cfg.mode,conformance,(family.get("maturity") or {}).get("level","experimental"),
                          claim.pack_authority(desc),claim.target_provenance(target))
    if (analysis["case_pack"]["content_hash"]!=desc["content_hash"]
            or target["target_id"]!=b["target"]["name"] or target["version"]!=b["target"]["version"]
            or target["is_mock"] is not False or analysis["claim_authority"]!=derived.as_dict()
            or derived.effective_claim not in ("none","demonstration","internal_regression")):
        raise buyer.BuyerError("patient package scope or claim mismatch")
    # The canonical manifest omits some executor-specific auxiliaries. Bind
    # the complete output inventory here, not just analysis.json.
    raw=_read(ws/"episodes.jsonl",buyer_research.MAX_BYTES)
    episodes=[json.loads(line) for line in raw.splitlines() if line.strip()]
    coverage=json.loads(_read(ws/"coverage.json",buyer_research.MAX_BYTES))
    if coverage!=analysis["summary"]["coverage"]:
        raise buyer.BuyerError("coverage artifact differs from analysis")
    _verify_episode_inventory(cases,episodes,cells,coverage)
    if any(e.get("errors") or e["target_id"]!=b["target"]["name"]
           or e["target_version"]!=b["target"]["version"] for e in episodes):
        raise buyer.BuyerError("episode transport error or target mismatch")
    file_hashes={p.relative_to(ws).as_posix():_sha(_read(p,buyer_research.MAX_BYTES))
                 for p in sorted(ws.rglob("*")) if p.is_file()}
    return {"manifest_hash":meta["manifest_hash"],"family":FAMILY,"claim":derived.effective_claim,
            "n_episodes":len(episodes),"n_review_items":analysis["n_review_selected"],
            "output_sha256":file_hashes,"decision_grade":False}


def execute(directory,identifier,approved_digest,*,allow_candidate_calls=False,allow_model_calls=False):
    if allow_candidate_calls is not True:
        raise access.AccessDenied("explicit candidate call approval required")
    with access.db(directory) as db:
        _schema(db)
        db.execute("BEGIN IMMEDIATE")
        row=_row(db,identifier)
        b=row["binding"]
        user=db.execute("SELECT role,enabled FROM users WHERE id=?",(row["owner"],)).fetchone()
        if (not user or not user["enabled"] or user["role"] not in ("admin","operator")
                or row["status"]!="queued" or approved_digest!=row["digest"]
                or b["real_judges"] and allow_model_calls is not True):
            raise access.AccessDenied("current queued job and exact execution approval required")
        db.execute("UPDATE conversation_jobs SET status='running' WHERE id=?",(identifier,))
        access._audit(db,row["owner"],"conversation-claimed",identifier)
    final="failed"
    result={"reason":"execution or verification failed","decision_grade":False}
    try:
        current,files=_material(directory,**{k:b[k] for k in ("plan_id","connector_id","pack_id","panel_id","governance")})
        if current!=b:
            raise buyer.BuyerError("approved inputs changed")
        dest=_root(directory)/identifier
        dest.mkdir(mode=0o700)
        for name,raw in files.items():
            path=dest/name
            path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            fd=os.open(str(path),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,"wb") as f:
                f.write(raw)
        env=os.environ.copy()
        env["PYTHONPATH"]=str(Path(__file__).resolve().parents[1])
        with access.db(directory) as db:
            owner=db.execute("SELECT role,enabled FROM users WHERE id=?",(row["owner"],)).fetchone()
            if not owner or not owner["enabled"] or owner["role"] not in ("admin","operator"):
                raise access.AccessDenied("job owner disabled or role changed")
        proc=subprocess.run([sys.executable,"-m","caeval.cli","run","--project","."],
                            cwd=dest,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,timeout=1800,check=False)
        if proc.returncode!=0:
            raise buyer.BuyerError("canonical patient runner failed")
        result=_verify(directory,row)
        final="completed"
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError,subprocess.SubprocessError):
        pass
    finally:
        with access.db(directory) as db:
            _schema(db)
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE conversation_jobs SET status=?,result=? WHERE id=? AND status='running'",
                       (final,json.dumps(result),identifier))
            access._audit(db,row["owner"],"conversation-"+final,identifier)
    return {"id":identifier,"status":final,"decision_grade":False}


def overview(directory,actor):
    with access.db(directory) as db:
        _schema(db)
        _actor(db,actor,"admin","operator","observer")
        rows=[_row(db,r["id"]) for r in db.execute("SELECT id FROM conversation_jobs ORDER BY rowid DESC LIMIT ?",(MAX_JOBS,)).fetchall()]
    public=[]
    for row in rows:
        state=row["status"]
        result=row["result"]
        if state=="completed":
            try:
                if _verify(directory,row)!=result:
                    raise buyer.BuyerError("output changed")
            except (OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError):
                state="invalid"
        public.append({"id":row["id"],"owner":row["owner"],"status":state,"mode":row["binding"]["mode"],
                       "target_version":row["binding"]["target"]["version"],
                       "binding_hash":row["digest"] if actor["role"]!="observer" else None,
                       "real_judges":row["binding"]["real_judges"],
                       "result":{k:result[k] for k in ("manifest_hash","claim","n_episodes","n_review_items")} if state=="completed" else {},
                       "required_but_not_run":row["binding"]["required_but_not_run"],
                       "decision_grade":False})
    return public
