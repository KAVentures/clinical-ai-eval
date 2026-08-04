"""Clinical RAG bundle — a version-pinned corpus, a retriever, and the two
retrieval families that were previously blocked for want of them.

Why a bundle rather than "point it at your guidelines": a retrieval evaluation is
only interpretable if the corpus is FIXED and ADDRESSABLE. If the corpus can drift,
a regression between two runs confounds the product with its evidence base, and
"the model got worse" is indistinguishable from "someone republished the guideline".
So a bundle is content-addressed as a whole and pinned by version, exactly as the
assessment manifest pins the rest of the environment.

What this bundle is NOT: a clinical knowledge base. The shipped corpus is a small
set of SYNTHETIC documents written to exercise retrieval failure modes. Using real
guidelines would make the demo look authoritative while adding a licensing problem
and no measurement validity. Both families ship `experimental`.
"""
from .corpus import Corpus, Document, build_demo_corpus  # noqa: F401
from .retriever import Retriever, RetrievalResult  # noqa: F401
from .probes import CITATION_PROBES, RETRIEVAL_PROBES, apply_probe  # noqa: F401
