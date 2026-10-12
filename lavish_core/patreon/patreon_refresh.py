"""Serialized OAuth rotation with durable, private token storage."""
import os
import re
import tempfile
import threading
from pathlib import Path
import requests
from dotenv import load_dotenv
from lavish_core.logger_setup import get_logger

log = get_logger('patreon_refresh', log_dir='lavish_core/patreon/logs')
ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / '.env')
PATREON_TOKEN_URL = 'https://www.patreon.com/api/oauth2/token'
_refresh_lock = threading.Lock()

def _update_env(key, value=None):
    mapping = key if isinstance(key, dict) else {key: value}
    for name, token in mapping.items():
        if not re.fullmatch(r'[A-Z_][A-Z0-9_]*', name) or not isinstance(token,str) or '\n' in token or '\r' in token:
            raise ValueError('Invalid environment entry')
    target = Path(os.getenv('LAVISH_ENV_FILE', str(ROOT / '.env'))).resolve()
    lines = target.read_text().splitlines(True) if target.exists() else []
    lines = [line for line in lines if not any(re.match(r'(?:export\s+)?'+re.escape(name)+r'\s*=',line) for name in mapping)]
    for name, token in mapping.items():
        escaped = token.replace('\\','\\\\').replace('"','\\"')
        lines.append(f'{name}="{escaped}"\n')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary,0o600)
            handle.writelines(lines)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary,target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

def refresh_patreon_token():
    with _refresh_lock:
        credentials = {name:os.getenv('PATREON_'+name.upper()) for name in ('client_id','client_secret','refresh_token')}
        if not all(credentials.values()):
            log.warning('Missing Patreon credentials')
            return None
        try:
            response = requests.post(PATREON_TOKEN_URL,data=dict(grant_type='refresh_token',**credentials),timeout=15)
            if response.status_code != 200:
                log.warning('Patreon refresh rejected (HTTP %s)',response.status_code)
                return None
            data = response.json()
            if not isinstance(data,dict) or data.get('error'):
                raise ValueError('Invalid OAuth response')
            tokens = {name:data.get(name) for name in ('access_token','refresh_token')}
            if not all(isinstance(token,str) and token.strip() and '\n' not in token and '\r' not in token for token in tokens.values()):
                raise ValueError('Incomplete OAuth response')
            mapping = {'PATREON_'+name.upper():token for name,token in tokens.items()}
            _update_env(mapping)
            os.environ.update(mapping)
            return tokens['access_token']
        except (requests.RequestException, ValueError, TypeError, OSError):
            log.warning('Patreon refresh failed; credentials retained')
            return None
