"""Federation boundary for an approved upstream OIDC/SAML authentication gateway.

The upstream gateway, NOT this module, validates identity-provider signatures,
audience, expiry and the institution's MFA policy. The backend accepts only
gateway-authenticated requests on a protected hop, with a separately provisioned
shared secret. Never expose the backend directly or forward client headers.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import stat
import time
import urllib.parse
from pathlib import Path

from . import institution_access as access, buyer_manual as manual

SECRET = re.compile(r"[0-9a-f]{64}")
SUBJECT = re.compile(r"[\x21-\x7e]{1,255}")
MAX_SESSION = 3600


def validate_issuer(issuer):
    try:
        if not isinstance(issuer,str) or len(issuer)>512 or not issuer.isascii() or any(c.isspace() for c in issuer):
            raise ValueError()
        p=urllib.parse.urlsplit(issuer)
        if p.scheme!="https" or not p.hostname or p.username is not None or p.password is not None or p.query or p.fragment:
            raise ValueError()
        if any(c in issuer for c in ("\\",",","#")):
            raise ValueError()
        _=p.port
    except (ValueError,TypeError):
        raise access.AccessDenied("exact approved HTTPS issuer required") from None
    return issuer


def subject_hash(issuer,subject):
    validate_issuer(issuer)
    if not isinstance(subject,str) or not SUBJECT.fullmatch(subject) or "," in subject:
        raise access.AccessDenied("invalid stable upstream subject")
    return hashlib.sha256((issuer+"\0"+subject).encode("ascii")).hexdigest()


def read_secret(path):
    p=Path(path)
    flags=os.O_RDONLY|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_BINARY",0)
    if p.is_symlink() or not p.is_file():
        raise access.AccessDenied("protected gateway secret file required")
    fd=os.open(str(p),flags)
    with os.fdopen(fd,"rb") as f:
        info=os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or (os.name=="posix" and stat.S_IMODE(info.st_mode)&0o077):
            raise access.AccessDenied("gateway secret must be a private regular file")
        raw=f.read(128)
    try:
        value=raw.decode("ascii").strip()
    except UnicodeError:
        raise access.AccessDenied("invalid gateway secret") from None
    if not SECRET.fullmatch(value):
        raise access.AccessDenied("gateway secret must contain 32 random bytes as lowercase hex")
    return value


def provision(directory,user_id,role,issuer,subject):
    """Trusted OS administrator provisions a new SSO-only account.

    No email-based linking, auto-provisioning, group-to-role mapping, or takeover
    of existing local accounts. Roles are assigned only in the local store.
    """
    manual._identifier(user_id)
    key=subject_hash(issuer,subject)
    if role not in ("admin","operator","observer","reviewer"):
        raise access.AccessDenied("explicit institutional role required")
    # Unusable random local password and explicit local login exclusion.
    salt=secrets.token_hex(16)
    digest=access._password(secrets.token_urlsafe(48),salt)
    with access.db(directory) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("INSERT INTO users(id,role,salt,password_hash) VALUES(?,?,?,?)",(user_id,role,salt,digest))
            conn.execute("INSERT INTO federated_identities VALUES(?,?,?)",(issuer,key,user_id))
        except sqlite3.IntegrityError:
            raise access.AccessDenied("account or upstream subject already assigned") from None
        access._audit(conn,"local-admin","federated-account-created",user_id)
    return {"user_id":user_id,"role":role,"login":"federated_only"}


class GatewayIdentity:
    def __init__(self,issuer,secret):
        self.issuer=validate_issuer(issuer)
        if not isinstance(secret,str) or not SECRET.fullmatch(secret):
            raise access.AccessDenied("invalid gateway authentication secret")
        self._secret=secret

    def principal(self,env):
        supplied=env.get("HTTP_X_CAEVAL_GATEWAY_SECRET","")
        issuer=env.get("HTTP_X_CAEVAL_ISSUER","")
        subject=env.get("HTTP_X_CAEVAL_SUBJECT","")
        if (not isinstance(supplied,str) or not SECRET.fullmatch(supplied)
                or not hmac.compare_digest(supplied,self._secret)
                or issuer!=self.issuer
                or env.get("HTTP_X_CAEVAL_ASSURANCE")!="institutional-mfa"):
            raise access.AccessDenied("approved gateway authentication required")
        return subject_hash(issuer,subject)

    def _account(self,conn,key):
        row=conn.execute("""SELECT u.id AS user_id,u.role FROM federated_identities f
             JOIN users u ON u.id=f.user_id
             WHERE f.issuer=? AND f.subject_hash=? AND u.enabled=1 AND u.role!='vendor'""",
             (self.issuer,key)).fetchone()
        if not row:
            raise access.AccessDenied("federated account not provisioned or disabled")
        return dict(row)

    def login(self,directory,env,previous=""):
        key=self.principal(env)
        now=int(time.time())
        token=secrets.token_urlsafe(32)
        digest=access._digest(token)
        with access.db(directory) as conn:
            conn.execute("BEGIN IMMEDIATE")
            actor=self._account(conn,key)
            conn.execute("DELETE FROM sessions WHERE expires<=?",(now,))
            # Rotate only an earlier session belonging to this same principal.
            if isinstance(previous,str) and len(previous)==43 and previous.isascii():
                conn.execute("""DELETE FROM sessions WHERE digest=? AND digest IN
                    (SELECT digest FROM federated_sessions WHERE issuer=? AND subject_hash=?)""",
                    (access._digest(previous),self.issuer,key))
            conn.execute("INSERT INTO sessions VALUES(?,?,?,?)",
                         (digest,actor["user_id"],secrets.token_urlsafe(32),now+MAX_SESSION))
            conn.execute("INSERT INTO federated_sessions VALUES(?,?,?)",(digest,self.issuer,key))
            access._audit(conn,actor["user_id"],"federated-login")
        return token

    def session(self,directory,env,token):
        key=self.principal(env)
        if not isinstance(token,str) or len(token)!=43 or not token.isascii():
            raise access.AccessDenied("authentication required")
        with access.db(directory) as conn:
            actor=self._account(conn,key)
            row=conn.execute("""SELECT s.csrf FROM sessions s JOIN federated_sessions f
              ON s.digest=f.digest WHERE s.digest=? AND s.expires>? AND s.user_id=?
              AND f.issuer=? AND f.subject_hash=?""",
              (access._digest(token),int(time.time()),actor["user_id"],self.issuer,key)).fetchone()
        if not row:
            raise access.AccessDenied("authentication required")
        return {**actor,"csrf":row["csrf"]}

    def logout(self,directory,env,token):
        actor=self.session(directory,env,token)
        with access.db(directory) as conn:
            conn.execute("DELETE FROM sessions WHERE digest=?",(access._digest(token),))
            access._audit(conn,actor["user_id"],"federated-logout")
