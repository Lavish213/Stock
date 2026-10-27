"""Compare broker positions with confirmed local executions; never invent fills."""
from lavish_core.utils.safety import finite_number

def audit_state(store,broker_positions,*,halt_on_mismatch=True):
    discrepancies=[];remote={}
    try:
        if not isinstance(broker_positions,list):raise ValueError('Invalid positions response')
        for position in broker_positions:
            symbol=position['symbol']
            if not isinstance(symbol,str) or not symbol or symbol.upper() in remote:raise ValueError('Invalid or duplicate symbol')
            qty=finite_number(position['qty'])
            if position.get('side')=='short' and qty>0:qty=-qty
            remote[symbol.upper()]=qty
        local={symbol:finite_number(qty) for symbol,qty in store.fetchall('SELECT symbol,qty FROM positions')}
        for symbol in sorted(remote.keys()|local.keys()):
            if abs(remote.get(symbol,0)-local.get(symbol,0))>1e-8:
                discrepancies.append(dict(symbol=symbol,broker_qty=remote.get(symbol,0),ledger_qty=local.get(symbol,0)))
                if halt_on_mismatch:
                    store.revoke_position_epoch(symbol)
    except (KeyError,ValueError,TypeError):discrepancies.append(dict(error='Invalid broker or ledger position data'))
    unresolved=store.get_open_orders();reasons=[]
    if discrepancies:reasons.append('broker_position_mismatch')
    if any(o['status'] in ('submission_unknown','fill_unverified','pending_submit') for o in unresolved):reasons.append('unresolved_order_outcome')
    if reasons and halt_on_mismatch:store.set_kill_switch(True,'state_audit: '+', '.join(reasons))
    return dict(ok=not reasons,discrepancies=discrepancies,unresolved_orders=len(unresolved),reasons=reasons)
