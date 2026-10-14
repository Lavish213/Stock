"""Atomic cross-process entry halt independent of DuckDB's writer lock."""
import json,os,tempfile
from datetime import datetime,timezone
from pathlib import Path

def control_path(db_path):
    return Path(str(Path(db_path).resolve())+'.control.json')

def read_control(db_path):
    path=control_path(db_path)
    if not path.exists():return None
    try:
        if path.stat().st_size>65536:raise ValueError('Oversized control state')
        data=json.loads(path.read_text())
        if type(data.get('enabled')) is not bool or not isinstance(data.get('reason'),str):raise ValueError('Invalid control state')
        return data['enabled'],data['reason']
    except (OSError,ValueError,TypeError,AttributeError):
        return True,'control state unreadable or corrupt; manual review required'

def write_control(db_path,enabled,reason=''):
    if type(enabled) is not bool or not isinstance(reason,str):raise ValueError('Invalid halt state')
    path=control_path(db_path);path.parent.mkdir(parents=True,exist_ok=True);temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as handle:
            temporary=Path(handle.name);os.chmod(temporary,0o600)
            json.dump(dict(enabled=enabled,reason=reason,updated_at=datetime.now(timezone.utc).isoformat()),handle)
            handle.flush();os.fsync(handle.fileno())
        os.replace(temporary,path)
        descriptor=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
