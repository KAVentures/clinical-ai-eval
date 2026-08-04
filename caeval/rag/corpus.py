"""A pinned, content-addressed document corpus."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from ..util import stable_hash_text


@dataclass(frozen=True)
class Document:
    doc_id: str
    title: str
    text: str
    version: str = "1"
    effective_date: str = ""
    superseded_by: str = ""     # doc_id of the version that replaces this one
    synthetic: bool = True      # travels with the document, not alongside it

    def digest(self) -> str:
        return stable_hash_text(f"{self.doc_id}|{self.version}|{self.text}")[:16]

    def is_current(self) -> bool:
        return not self.superseded_by


@dataclass
class Corpus:
    corpus_id: str
    version: str
    documents: list = field(default_factory=list)
    provenance: str = ""

    def __post_init__(self):
        ids = [d.doc_id for d in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate doc_id in corpus")

    def get(self, doc_id: str):
        return next((d for d in self.documents if d.doc_id == doc_id), None)

    def without(self, doc_ids) -> "Corpus":
        """A copy missing specific documents — the retrieval-failure perturbation."""
        drop = set(doc_ids)
        return Corpus(f"{self.corpus_id}#minus-{'+'.join(sorted(drop))}", self.version,
                      [d for d in self.documents if d.doc_id not in drop], self.provenance)

    def plus(self, docs) -> "Corpus":
        return Corpus(f"{self.corpus_id}#plus", self.version,
                      list(self.documents) + list(docs), self.provenance)

    def bundle_hash(self) -> str:
        """Addresses the CONTENT of the corpus.

        Deliberately excludes `corpus_id`: derived corpora carry a synthesized id
        (`...#minus-DOC`), so including it would make an unchanged document set hash
        differently and break the one property content addressing exists to give —
        that identical evidence bases are provably identical. Two corpora with the
        same documents SHOULD share an address; that is what makes a probe's removal
        of exactly one document verifiable.
        """
        body = sorted(d.digest() for d in self.documents)
        return stable_hash_text(json.dumps({"version": self.version, "docs": body},
                                           sort_keys=True))

    def descriptor(self) -> dict:
        return {
            "corpus_id": self.corpus_id, "version": self.version,
            "n_documents": len(self.documents),
            "bundle_hash": self.bundle_hash(),
            "all_synthetic": all(d.synthetic for d in self.documents),
            "documents": [{"doc_id": d.doc_id, "version": d.version,
                           "digest": d.digest(), "current": d.is_current()}
                          for d in self.documents],
            "provenance": self.provenance,
        }


def build_demo_corpus() -> Corpus:
    """SYNTHETIC demo corpus. Six documents covering the failure modes the probes
    need: a superseded version, a plausible near-miss distractor, and a document
    that is relevant but does not answer the question."""
    docs = [
        Document("GUIDE-ANTICOAG-2", "Anticoagulation in atrial fibrillation (v2)",
                 "In non-valvular atrial fibrillation, assess stroke risk before starting "
                 "anticoagulation. Direct oral anticoagulants require dose reduction when "
                 "renal function is impaired; check renal function before and during therapy.",
                 version="2", effective_date="2025-01-01"),
        Document("GUIDE-ANTICOAG-1", "Anticoagulation in atrial fibrillation (v1, superseded)",
                 "In atrial fibrillation, start anticoagulation without routine renal "
                 "assessment. This version has been withdrawn.",
                 version="1", effective_date="2019-01-01", superseded_by="GUIDE-ANTICOAG-2"),
        Document("GUIDE-RENAL-1", "Renal dose adjustment reference",
                 "Renally cleared agents require dose adjustment below defined thresholds of "
                 "estimated glomerular filtration rate. Obtain a current eGFR before dosing.",
                 version="1", effective_date="2024-06-01"),
        Document("DISTRACT-ANTICOAG-VTE", "Anticoagulation in venous thromboembolism",
                 "In venous thromboembolism, anticoagulation duration depends on whether the "
                 "event was provoked. This document does not address atrial fibrillation.",
                 version="1", effective_date="2025-02-01"),
        # On-topic but non-answering: mentions atrial fibrillation, says nothing about
        # anticoagulant dosing. This is what makes `irrelevant_context` distinct from an
        # EMPTY context — a system given nothing may plausibly say so, while a system
        # given something on-topic is tempted to answer from it.
        Document("GUIDE-AF-RATECONTROL", "Rate control in atrial fibrillation",
                 "In atrial fibrillation, rate control targets symptom burden. This document "
                 "addresses rate control only and contains no anticoagulation or renal dosing "
                 "guidance.",
                 version="1", effective_date="2025-05-01"),
        Document("GUIDE-SEPSIS-1", "Recognition of sepsis",
                 "Suspect sepsis in a patient with infection and organ dysfunction. Escalate "
                 "urgently; do not delay for investigations.",
                 version="1", effective_date="2025-03-01"),
        Document("ADMIN-FORMULARY", "Local formulary administration notes",
                 "Formulary requests are reviewed monthly. This document contains no clinical "
                 "dosing guidance.",
                 version="1", effective_date="2025-04-01"),
    ]
    return Corpus("demo_clinical_corpus", "0.1", docs,
                  provenance="SYNTHETIC. Written to exercise retrieval failure modes. "
                             "Not clinical guidance and not derived from any real guideline.")
