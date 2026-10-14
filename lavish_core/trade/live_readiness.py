"""Expiring, code-bound operator evidence approval before opening live risk.

Simulations never generate approval. Approval is not a profit guarantee.
"""
import hashlib,json,os,re
from datetime import datetime,timezone
from pathlib import Path
from lavish_core.utils.safety import finite_number

REQUIRED={
 'source_delivery':{'authenticated-source'},
 'cash_reconciliation':{'authenticated-paper'},
 'authenticated_paper_lifecycle':{'authenticated-paper'},
 'deployment_recovery':{'deployed-observation'},
 'notifications':{'deployed-observation'},
 'strategy_evaluation':{'broker-export'},
 'key_rotation':{'operator-review'},
 'option_settlement_review':{'operator-review','authenticated-paper'},
}

def source_config_digest():
    """Bind evidence to configured source identities; never persist tokens."""
    sources=[]
    campaign=os.getenv('PATREON_CAMPAIGN_ID','')
    patreon_auth=bool(os.getenv('PATREON_ACCESS_TOKEN') or all(os.getenv(k) for k in ('PATREON_CLIENT_ID','PATREON_CLIENT_SECRET','PATREON_REFRESH_TOKEN')))
    if patreon_auth and re.fullmatch(r'[1-9][0-9]*',campaign):
        sources.append({'kind':'patreon','campaign':campaign})
    def ids(name):
        values=[v.strip() for v in os.getenv(name,'').split(',') if v.strip()]
        return sorted(set(values)) if values and all(re.fullmatch(r'[1-9][0-9]*',v) for v in values) else []
    if os.getenv('DISCORD_USER_TOKEN') and os.getenv('DISCORD_INGEST_ACCEPT_TOS_RISK')=='1':
        channels=ids('DISCORD_WATCH_CHANNEL_IDS');authors=ids('DISCORD_WATCH_AUTHOR_IDS');webhooks=ids('DISCORD_WATCH_WEBHOOK_IDS')
        if channels and (authors or webhooks):sources.append({'kind':'discord','channels':channels,'authors':authors,'webhooks':webhooks})
    # The private receiver supports paper/dry only; it is not a live source.
    return hashlib.sha256(json.dumps(sources,sort_keys=True,separators=(',',':')).encode()).hexdigest() if sources else None

def runtime_digest():
    root=Path(__file__).resolve().parents[2]
    paths=[root/'requirements-deploy.txt']
    paths.extend(path for path in root.glob('*.py') if not path.name.startswith('test'))
    for folder in ('lavish_core','ml'):paths.extend((root/folder).rglob('*.py'))
    digest=hashlib.sha256()
    for path in sorted(paths):digest.update(str(path.relative_to(root)).encode());digest.update(path.read_bytes())
    return digest.hexdigest()

def readiness_path():
    return Path(os.getenv('LAVISH_READINESS_FILE',str(Path(__file__).resolve().parents[2]/'data/live-readiness.json')))

def validate_approval(path=None,*,now=None,notional=None,account_id=None):
    path=Path(path or readiness_path());now=now or datetime.now(timezone.utc);reasons=[]
    try:
        if path.stat().st_size>1024*1024:raise ValueError('Oversized approval')
        approval=json.loads(path.read_text())
        if approval.get('schema_version')!=1 or approval.get('operator_approved') is not True:raise ValueError('Explicit approval required')
        issued=datetime.fromisoformat(approval['issued_at'].replace('Z','+00:00'));expiry=datetime.fromisoformat(approval['expires_at'].replace('Z','+00:00'))
        if issued.tzinfo is None or expiry.tzinfo is None or not issued<=now<expiry or (expiry-issued).total_seconds()>7*86400:raise ValueError('Invalid approval dates')
        if approval.get('runtime_sha256')!=runtime_digest():raise ValueError('Approved runtime changed')
        source_hash=source_config_digest()
        if source_hash is None or approval.get('source_id_hash')!=source_hash:raise ValueError('Source configuration missing or changed')
        fingerprint=approval.get('live_account_id_hash','')
        if not re.fullmatch(r'[0-9a-f]{64}',fingerprint):raise ValueError('Missing approved account')
        if account_id is not None and hashlib.sha256(str(account_id).encode()).hexdigest()!=fingerprint:raise ValueError('Different account')
        paper_fingerprint=approval.get('paper_account_id_hash','')
        if not re.fullmatch(r'[0-9a-f]{64}',paper_fingerprint):raise ValueError('Missing approved paper evidence account')
        limits={key:finite_number(approval['limits'][key],positive=True) for key in ('max_trade_usd','max_gross_exposure','max_daily_loss_usd')}
        if limits['max_trade_usd']>limits['max_gross_exposure']:raise ValueError('Invalid caps')
        if notional is not None and finite_number(notional,positive=True)>limits['max_trade_usd']:raise ValueError('Trade cap exceeded')
        for check,sources in REQUIRED.items():
            record=approval['checks'][check]
            if record.get('status')!='passed' or record.get('source') not in sources:reasons.append(check+':missing_verified_evidence');continue
            evidence=Path(record['evidence_path'])
            if not evidence.is_absolute():evidence=path.parent/evidence
            if evidence.stat().st_size>10*1024*1024:raise ValueError('Oversized evidence')
            payload=evidence.read_bytes()
            if hashlib.sha256(payload).hexdigest()!=record['sha256']:reasons.append(check+':evidence_changed');continue
            data=json.loads(payload)
            if data.get('synthetic') is not False or data.get('passed') is not True or data.get('check')!=check or data.get('source')!=record.get('source'):reasons.append(check+':invalid_provenance')
            observed=datetime.fromisoformat(data['observed_at'].replace('Z','+00:00'))
            if observed.tzinfo is None or observed>issued or observed>now or (now-observed).total_seconds()>30*86400:reasons.append(check+':stale_or_unreviewed_evidence')
            if data.get('runtime_sha256')!=approval['runtime_sha256']:reasons.append(check+':different_runtime')
            if check=='source_delivery' and data.get('source_id_hash')!=source_hash:reasons.append(check+':different_source')
            if record['source']=='authenticated-paper' and data.get('account_id_hash')!=paper_fingerprint:reasons.append(check+':different_paper_account')
        return dict(live_ready=not reasons,reasons=reasons,limits=limits)
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        return dict(live_ready=False,reasons=['missing_invalid_or_expired_live_approval'],limits={})

def assert_live_ready(*,notional=None,account_id=None):
    result=validate_approval(notional=notional,account_id=account_id)
    if not result['live_ready']:raise RuntimeError('Live entries blocked: '+', '.join(result['reasons']))
    return result

def approved_entry_limits(store,notional,equity,account_id=None):
    if not isinstance(account_id,str) or not account_id:raise RuntimeError('Live account identity unavailable')
    approval=assert_live_ready(notional=notional,account_id=account_id);limits=approval['limits']
    baseline=store.get_risk_limits().get('circuit_breaker_day_baseline_equity')
    if baseline is None:raise RuntimeError('Daily equity baseline unavailable')
    if finite_number(baseline,positive=True)-finite_number(equity,positive=True)>=limits['max_daily_loss_usd']:
        store.set_kill_switch(True,'approved daily loss limit reached');raise RuntimeError('Approved daily loss limit reached')
    return limits
