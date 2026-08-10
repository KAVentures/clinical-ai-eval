"""Platform-issued, run-bound review packets.

WHY THIS EXISTS
---------------
v0.10 marked mock reviews with CSV columns (`review_provenance: synthetic_mock`).
That is **self-declared and removable** — the v0.10 test suite itself contained a
`_derandomize()` helper that stripped those columns so a mock file "looked like a
real clinician's", and a spreadsheet round-trip can drop them by accident. Whether a
submission is synthetic must not be assertable (or deniable) by whoever returns it.

So packets are ISSUED by the platform, not described by the submitter:

    issue_packet(...)  ->  packet.json + review CSV, carrying a signature over
                           {run_id, manifest_hash, reviewer_id, role, packet_id,
                            synthetic, payload_hash}

`synthetic` is INSIDE the signed payload. Removing the column does not make a
synthetic packet look real — it makes the signature fail to verify, which is an
integrity failure. Forging one requires the run secret.

SCOPE, STATED PLAINLY
---------------------
This is an HMAC over a locally-stored per-run secret. It defeats accidental column
loss, casual editing, packet swapping between reviewers or runs, and replay against
a different manifest. It is NOT a PKI, and anyone with filesystem access to the run
secret can mint packets. Do not describe it as cryptographic proof of clinician
identity.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path

from .util import stable_hash_text, utc_now_iso

SECRET_FILE = "run_secret.key"          # git-ignored; never leaves the workspace
PACKET_FIELDS = ("run_id", "manifest_hash", "reviewer_id", "reviewer_role",
                 "packet_id", "synthetic", "payload_hash", "issued_at")


class PacketError(RuntimeError):
    """A review packet is missing, unverifiable, or bound to something else."""


def _secret_path(workspace: Path) -> Path:
    return Path(workspace) / SECRET_FILE


def ensure_run_secret(workspace: Path) -> bytes:
    """Create the per-run signing secret once; reuse it thereafter."""
    p = _secret_path(workspace)
    if not p.exists():
        p.write_text(secrets.token_hex(32))
        try:
            p.chmod(0o600)
        except OSError:
            pass
    return p.read_text().strip().encode()


def _sign(secret: bytes, payload: dict) -> str:
    canonical = json.dumps({k: payload[k] for k in PACKET_FIELDS}, sort_keys=True, default=str)
    return hmac.new(secret, canonical.encode(), hashlib.sha256).hexdigest()


def issue_packet(workspace, run_id: str, manifest_hash: str, reviewer_id: str,
                 reviewer_role: str, rows: list, synthetic: bool = False) -> dict:
    """Issue a signed packet for ONE reviewer over an exact set of review rows."""
    ws = Path(workspace)
    secret = ensure_run_secret(ws)
    payload = {
        "run_id": run_id,
        "manifest_hash": manifest_hash,
        "reviewer_id": reviewer_id,
        "reviewer_role": reviewer_role,
        "packet_id": f"{run_id}:{reviewer_id}:{stable_hash_text(manifest_hash + reviewer_id)[:12]}",
        # INSIDE the signature: a synthetic packet cannot be laundered by editing
        # or deleting a column, only by forging with the run secret.
        "synthetic": bool(synthetic),
        "payload_hash": stable_hash_text(json.dumps(sorted(r["cell_id"] for r in rows), sort_keys=True)),
        "issued_at": utc_now_iso(),
    }
    payload["signature"] = _sign(secret, payload)
    return payload


def read_run_secret(workspace) -> bytes | None:
    """Read the secret WITHOUT creating one. Verification must never mutate an
    evidence package: `ensure_run_secret()` would mint a fresh key and then fail
    every signature against it, turning "the key is missing" into "the packets are
    forged" while silently writing to the package under audit."""
    p = _secret_path(Path(workspace))
    return p.read_bytes() if p.exists() else None


def verify_packet(workspace, packet: dict, expected_run_id: str,
                  expected_manifest_hash: str, submitted_cells: list,
                  expected_reviewer_id: str | None = None) -> list:
    """Return a list of problems; empty means the packet is trustworthy."""
    problems = []
    if not isinstance(packet, dict):
        return ["review packet is missing or not an object"]
    missing = [f for f in PACKET_FIELDS + ("signature",) if f not in packet]
    if missing:
        return [f"review packet is missing field(s) {missing}"]

    # --- F6: bind the packet to the reviewer it is being used FOR -----------
    # The signature covers `reviewer_id`, so a packet cannot be edited — but
    # nothing checked that the packet handed in alongside reviewer X's CSV was
    # ISSUED to X. Two reviewers' packets could be swapped and both verify.
    if expected_reviewer_id is not None and packet.get("reviewer_id") != expected_reviewer_id:
        problems.append(
            f"packet was issued to reviewer {packet.get('reviewer_id')!r} but is being "
            f"used for {expected_reviewer_id!r}: a validly signed packet for a "
            f"DIFFERENT reviewer is not evidence about this one")

    secret = read_run_secret(workspace)
    if secret is None:
        return problems + [
            f"no {SECRET_FILE} in this workspace, so packet signatures cannot be "
            f"checked. Refusing to create one: verification must not modify the "
            f"package it is verifying."]
    if not hmac.compare_digest(_sign(secret, packet), str(packet["signature"])):
        problems.append(
            f"packet {packet.get('packet_id')!r} signature does not verify — it was edited, "
            f"issued for another run, or hand-authored. Note this ALSO fires when the "
            f"`synthetic` marker was altered, which is the point.")
    if packet["run_id"] != expected_run_id:
        problems.append(f"packet is bound to run {packet['run_id']!r}, not {expected_run_id!r}")
    if packet["manifest_hash"] != expected_manifest_hash:
        problems.append("packet was issued against a DIFFERENT review manifest "
                        "(the queue changed after issue)")
    got = stable_hash_text(json.dumps(sorted(submitted_cells), sort_keys=True))
    if got != packet["payload_hash"]:
        problems.append(f"reviewer {packet['reviewer_id']!r} returned a different cell set "
                        f"than was issued (rows added or removed)")
    return problems


def _safe_reviewer_id(reviewer_id: str) -> str:
    """Reviewer ids become filenames. A separator or `..` would write outside the
    packet directory."""
    rid = str(reviewer_id)
    if not rid.strip() or any(c in rid for c in ("/", "\\", "\0")) or rid in (".", "..") \
            or rid.startswith("."):
        raise ValueError(
            f"invalid reviewer_id {reviewer_id!r}: must be non-empty and contain no path "
            f"separators, leading dot, or traversal component")
    return rid


def write_packet(workspace, packet: dict) -> Path:
    ws = Path(workspace) / "review_packets"
    ws.mkdir(parents=True, exist_ok=True)
    p = ws / f"{_safe_reviewer_id(packet['reviewer_id'])}.packet.json"
    p.write_text(json.dumps(packet, indent=2))
    return p


def load_packet(workspace, reviewer_id: str) -> dict | None:
    p = Path(workspace) / "review_packets" / f"{_safe_reviewer_id(reviewer_id)}.packet.json"
    return json.loads(p.read_text()) if p.exists() else None
