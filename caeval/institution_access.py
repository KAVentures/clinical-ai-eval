"""Single-organisation identity, assignment and audit store.

Separate from the loopback buyer console. Accounts are provisioned by a trusted
server administrator. A database-backed session never trusts a role or reviewer
identity supplied by a browser. OS administrators remain trusted.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from . import buyer_agent as buyer, buyer_manual as manual, buyer_jobs as jobs
from . import buyer_physician_review as review, buyer_plan

ROLES = ("admin", "operator", "observer", "reviewer", "vendor")
SESSION_SECONDS = 8 * 3600
MAX_FAILURES = 5
WINDOW_SECONDS = 900


class AccessDenied(buyer.BuyerError):
    pass


def _root(directory):
    buyer.load(directory)
    root = manual._directory(directory, "institution")
    root.mkdir(mode=0o700, exist_ok=True)
    return root


@contextmanager
def db(directory):
    p = _root(directory) / "access.sqlite3"
    if p.is_symlink() or (p.exists() and not p.is_file()):
        raise AccessDenied("invalid access store")
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    conn = sqlite3.connect(p, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          id TEXT PRIMARY KEY, role TEXT NOT NULL, salt TEXT NOT NULL,
          password_hash TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS sessions(
          digest TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
          csrf TEXT NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS failures(
          user_key TEXT NOT NULL, at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS assignments(
          reviewer TEXT NOT NULL REFERENCES users(id), job TEXT NOT NULL,
          family TEXT NOT NULL, manifest TEXT NOT NULL,
          PRIMARY KEY(reviewer,job,family));
        CREATE TABLE IF NOT EXISTS submissions(
          reviewer TEXT NOT NULL, job TEXT NOT NULL, family TEXT NOT NULL,
          manifest TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL,
          PRIMARY KEY(reviewer,job,family));
        CREATE TABLE IF NOT EXISTS audit(
          seq INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL,
          actor TEXT NOT NULL, action TEXT NOT NULL, object_id TEXT NOT NULL,
          outcome TEXT NOT NULL);
        """)
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _audit(conn, actor, action, object_id="", outcome="ok"):
    conn.execute("INSERT INTO audit(at,actor,action,object_id,outcome) VALUES(?,?,?,?,?)",
                 (int(time.time()), actor, action, object_id, outcome))


def event(directory, actor, action, object_id="", outcome="ok"):
    with db(directory) as conn:
        _audit(conn, actor, action, object_id, outcome)


def _password(password, salt):
    if not isinstance(password, str) or len(password.encode("utf-8")) > 1024:
        raise AccessDenied("invalid password")
    return hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt),
                          n=32768, r=8, p=1, maxmem=64*1024*1024).hex()


def provision(directory, user_id, role, password):
    """Trusted OS administrator only; never an exposed browser action."""
    manual._identifier(user_id)
    if role not in ROLES or not isinstance(password, str) or len(password) < 14:
        raise AccessDenied("known role and password of at least 14 characters required")
    salt = secrets.token_hex(16)
    digest = _password(password, salt)
    with db(directory) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("INSERT INTO users(id,role,salt,password_hash) VALUES(?,?,?,?)",
                         (user_id, role, salt, digest))
        except sqlite3.IntegrityError as exc:
            raise AccessDenied("account already exists") from exc
        _audit(conn, "local-admin", "account-created", user_id)
    return {"user_id": user_id, "role": role}


def disable(directory, user_id):
    manual._identifier(user_id)
    with db(directory) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("UPDATE users SET enabled=0 WHERE id=?", (user_id,)).rowcount != 1:
            raise AccessDenied("unknown account")
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        _audit(conn, "local-admin", "account-disabled", user_id)


def login(directory, user_id, password):
    """Serialize throttled authentication; generic failures, including unknown IDs."""
    if not isinstance(user_id, str) or len(user_id) > 128 or not isinstance(password, str):
        raise AccessDenied("invalid credentials")
    key = hashlib.sha256(user_id.encode()).hexdigest()
    now = int(time.time())
    denied = False
    token = None
    with db(directory) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM failures WHERE at<?", (now-WINDOW_SECONDS,))
        conn.execute("DELETE FROM sessions WHERE expires<=?", (now,))
        n = conn.execute("SELECT count(*) FROM failures WHERE user_key=?", (key,)).fetchone()[0]
        total = conn.execute("SELECT count(*) FROM failures").fetchone()[0]
        if n >= MAX_FAILURES or total >= 100:
            denied = True
        else:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            salt = row["salt"] if row else "00"*16
            expected = row["password_hash"] if row else "0"*128
            try:
                valid = hmac.compare_digest(_password(password, salt), expected)
            except (ValueError, AccessDenied):
                valid = False
            if not valid or not row or not row["enabled"] or row["role"] == "vendor":
                conn.execute("INSERT INTO failures(user_key,at) VALUES(?,?)", (key, now))
                _audit(conn, "anonymous", "login", "", "denied")
                denied = True
            else:
                token = secrets.token_urlsafe(32)
                conn.execute("DELETE FROM failures WHERE user_key=?", (key,))
                conn.execute("INSERT INTO sessions VALUES(?,?,?,?)",
                             (_digest(token), user_id, secrets.token_urlsafe(32), now+SESSION_SECONDS))
                _audit(conn, user_id, "login")
    if denied:
        raise AccessDenied("invalid credentials or temporarily throttled")
    return token


def _digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def session(directory, token):
    if not isinstance(token, str) or len(token) != 43 or not token.isascii():
        raise AccessDenied("authentication required")
    with db(directory) as conn:
        row = conn.execute("""SELECT u.id AS user_id,u.role,s.csrf FROM sessions s
          JOIN users u ON u.id=s.user_id WHERE s.digest=? AND s.expires>?
          AND u.enabled=1 AND u.role!='vendor'""", (_digest(token), int(time.time()))).fetchone()
    if not row:
        raise AccessDenied("authentication required")
    return dict(row)


def logout(directory, token):
    actor = session(directory, token)
    with db(directory) as conn:
        conn.execute("DELETE FROM sessions WHERE digest=?", (_digest(token),))
        _audit(conn, actor["user_id"], "logout")


def require(actor, *roles):
    if actor["role"] not in roles:
        raise AccessDenied("role not permitted")


def assign(directory, reviewer_id, job_id, family):
    """Trusted local administrator assigns one immutable package to a reviewer."""
    manual._identifier(reviewer_id)
    doc = review.template(directory, job_id, family)
    digest = doc["request"]["manifest_hash"]
    with db(directory) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT role,enabled FROM users WHERE id=?", (reviewer_id,)).fetchone()
        if not row or row["role"] != "reviewer" or not row["enabled"]:
            raise AccessDenied("active reviewer account required")
        old = conn.execute("SELECT manifest FROM assignments WHERE reviewer=? AND job=? AND family=?",
                           (reviewer_id, job_id, family)).fetchone()
        if old and old["manifest"] != digest:
            raise AccessDenied("assignment bound to different evidence; use a fresh job")
        conn.execute("INSERT OR IGNORE INTO assignments VALUES(?,?,?,?)",
                     (reviewer_id, job_id, family, digest))
        _audit(conn, "local-admin", "review-assigned", job_id)
    return {"reviewer": reviewer_id, "job": job_id, "family": family, "manifest": digest}


def assignments(directory, actor):
    require(actor, "reviewer")
    with db(directory) as conn:
        rows = conn.execute("""SELECT a.job,a.family,a.manifest,
          CASE WHEN s.digest IS NULL THEN 0 ELSE 1 END AS submitted
          FROM assignments a LEFT JOIN submissions s ON a.reviewer=s.reviewer
          AND a.job=s.job AND a.family=s.family WHERE a.reviewer=? ORDER BY a.job,a.family""",
                            (actor["user_id"],)).fetchall()
    return [dict(r) for r in rows]


def _assigned(conn, actor, job_id, family):
    require(actor, "reviewer")
    row = conn.execute("""SELECT a.manifest FROM assignments a JOIN users u ON a.reviewer=u.id
      WHERE a.reviewer=? AND a.job=? AND a.family=? AND u.enabled=1 AND u.role='reviewer'""",
                       (actor["user_id"], job_id, family)).fetchone()
    if not row:
        raise AccessDenied("review assignment required")
    return row["manifest"]


def review_template(directory, actor, job_id, family):
    with db(directory) as conn:
        bound = _assigned(conn, actor, job_id, family)
    doc = review.template(directory, job_id, family)
    if doc["request"]["manifest_hash"] != bound:
        raise AccessDenied("assigned evidence changed")
    event(directory, actor["user_id"], "review-opened", job_id)
    return doc


def submit(directory, actor, job_id, family, manifest_hash, verdicts):
    doc = review_template(directory, actor, job_id, family)
    req = doc["request"]
    if req["manifest_hash"] != manifest_hash:
        raise AccessDenied("stale review")
    expected = {i["slot"] for i in req["items"]}
    if not expected or not isinstance(verdicts, list) or len(verdicts) != len(expected):
        raise AccessDenied("one verdict per assigned item required")
    seen = set()
    for row in verdicts:
        if (not isinstance(row, dict) or set(row) != {"slot","verdict","notes"}
                or not isinstance(row["slot"], str) or row["slot"] not in expected
                or row["slot"] in seen or row["verdict"] not in review.VERDICTS
                or not isinstance(row["notes"], str) or len(row["notes"]) > 4000):
            raise AccessDenied("invalid review verdict")
        seen.add(row["slot"])
    payload = {"schema":"institution-review/1", "reviewer":actor["user_id"], "job":job_id,
               "family":family, "manifest":manifest_hash,
               "verdicts":sorted(verdicts,key=lambda r:r["slot"]),
               "identity": "authenticated_local_account",
               "clinical_credentials_verified":False,"adjudicated":False,"decision_grade":False}
    digest = buyer_plan._digest(payload)
    with db(directory) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if _assigned(conn, actor, job_id, family) != manifest_hash:
            raise AccessDenied("review assignment changed")
        previous = conn.execute("SELECT digest FROM submissions WHERE reviewer=? AND job=? AND family=?",
                                (actor["user_id"],job_id,family)).fetchone()
        if previous and previous["digest"] != digest:
            raise AccessDenied("review already submitted; no overwrite")
        conn.execute("INSERT OR IGNORE INTO submissions VALUES(?,?,?,?,?,?)",
                     (actor["user_id"],job_id,family,manifest_hash,digest,json.dumps(payload,ensure_ascii=False)))
        _audit(conn, actor["user_id"], "review-submitted", job_id)
    return {"digest":digest,"decision_grade":False}


def audit(directory, actor):
    require(actor,"admin")
    with db(directory) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM audit ORDER BY seq DESC LIMIT 200")]


def summary(directory, actor):
    require(actor,"admin","operator","observer")
    # Deliberately omit raw responses, model credentials, case text, reviewer
    # identities, individual verdicts and supplier-sensitive reports.
    result=[]
    for stored in jobs.list_jobs(directory):
        row=jobs.status(directory,stored["id"])
        result.append({"job":row["id"],"status":row["status"],"mode":row["mode"],
                       "n_packages":len(row["result"].get("packages",[]))})
    event(directory,actor["user_id"],"job-summary-opened")
    return result
