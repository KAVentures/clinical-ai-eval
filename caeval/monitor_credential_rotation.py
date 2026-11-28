"""Explicit, atomic replacement of a pre-issued local polling credential.

No grant is issued or revoked here. Keep the old grant active until the
institution has verified a successful live cycle using the replacement.
"""
import hashlib
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from . import monitor_alert_relay as relay, monitor_alert_worker as worker


def _private_dir(path):
    p=Path(path).absolute()
    relay.require(os.name=='posix' and p.is_dir() and not p.is_symlink() and
                  not any(a.is_symlink() for a in p.parents), 'private POSIX credential directory required')
    info=p.stat()
    relay.require(info.st_uid==os.geteuid() and not info.st_mode & 0o077,'credential directory must be owned by caller and private')
    return p


def _owned_secret(path):
    value=worker._secret(path)
    relay.require(path.stat().st_uid==os.geteuid(),'credential file must be owned by caller')
    return value


def _hash(token):
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def _preview(directory,config_digest,validate):
    relay.require(relay.hex64(config_digest),'approved deployment digest required')
    current=_owned_secret(directory/'check-token')
    candidate=_owned_secret(directory/'next-check-token')
    sink=_owned_secret(directory/'relay-token')
    relay.require(current!=candidate and candidate!=sink and current!=sink,'distinct current, staged and relay credentials required')
    status=validate(candidate)
    relay.require(status['schema']=='monitor-polling-credential-status/1' and status['scope']=='check' and
                  status['source_authentication_checked'] is True and type(status['expires']) is int,'invalid staged credential validation')
    binding={'schema':'monitor-credential-rotation/1','config_digest':config_digest,
             'current_hash':_hash(current),'candidate_hash':_hash(candidate),'sink_hash':_hash(sink),
             'candidate_expires':status['expires']}
    return {'schema':'monitor-credential-rotation-preview/1','approval_digest':relay.digest(binding),
            'candidate_expires':status['expires'],'monitor_id':status['monitor_id'],'plan_digest':status['plan_digest'],
            'old_grant_revoked':False,'live_http_checked':False}, candidate


def preview(directory,config_digest,validate):
    return _preview(_private_dir(directory),config_digest,validate)[0]


@contextmanager
def _lock(directory):
    import fcntl
    path=directory/'rotation.lock'
    fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        relay.require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==os.geteuid() and
                      not info.st_mode & 0o077,'invalid rotation lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def rotate(directory,config_digest,approval,validate,*,confirmed=False):
    relay.require(confirmed is True and relay.hex64(approval),'explicit rotation approval required')
    directory=_private_dir(directory)
    with _lock(directory):
        doc,candidate=_preview(directory,config_digest,validate)
        relay.require(doc['approval_digest']==approval,'rotation changed; preview again')
        fd,path=tempfile.mkstemp(prefix='.check-token-',dir=directory)
        try:
            with os.fdopen(fd,'wb') as handle:
                handle.write(candidate.encode('ascii')+b'\n')
                handle.flush()
                os.fsync(handle.fileno())
            # Refuse changed staged/current credentials, expiry or scope before replace.
            latest,value=_preview(directory,config_digest,validate)
            relay.require(latest==doc and value==candidate,'rotation changed before replacement')
            os.replace(path,directory/'check-token')
            dirfd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        finally:
            if os.path.exists(path):
                os.unlink(path)
        return {**doc,'schema':'monitor-credential-rotation-result/1','credential_replaced':True,
                'staged_file_retained':True}
