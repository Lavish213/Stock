from unittest.mock import Mock
import pytest
from lavish_core.trade import reconcile as rc, equity_exit_monitor as em

@pytest.fixture
def recovery(store, monkeypatch):
    monkeypatch.setattr(rc,'DEFAULT_DB',store.db_path)
    monkeypatch.setattr(rc,'_watched',set())
    oid=store.submit_order('AAPL','buy',2,venue='paper',status='filled',
                           meta={'broker_id':'parent','take_profit':110,'stop_loss':90})
    store.log_fill(oid,'AAPL','buy',2,100)
    monkeypatch.setattr(rc,'get_positions',lambda:[dict(symbol='AAPL',asset_class='us_equity',side='long',qty=2,avg_entry_price=100)])
    monkeypatch.setattr(rc,'get_order',lambda _:{'id':'parent','symbol':'AAPL','legs':[{'id':'tp','status':'new'},{'id':'sl','status':'new'}]})
    launch=Mock();monkeypatch.setattr(em,'watch_bracket_async',launch);monkeypatch.setattr(em,'watch_and_exit_active_async',launch)
    return launch

@pytest.mark.parametrize('native',[True,False])
def test_equity_restart_restores_original_plan_once(store,monkeypatch,recovery,native):
    monkeypatch.setattr(rc,'SUPPORTS_NATIVE_BRACKETS',native)
    assert rc.reconcile_open_positions()==1
    assert rc.reconcile_open_positions()==0
    assert recovery.call_count==1
    if native:assert recovery.call_args.kwargs['leg_ids']==['tp','sl']
    else:assert recovery.call_args.kwargs['take_profit']==110 and recovery.call_args.kwargs['stop_loss']==90

@pytest.mark.parametrize('fault',['missing_entry','quantity','parent_id','legs','launch'])
def test_uncertain_equity_restart_halts_without_guessing(store,monkeypatch,recovery,fault):
    monkeypatch.setattr(rc,'SUPPORTS_NATIVE_BRACKETS',True)
    if fault=='missing_entry':store.execute("UPDATE orders SET status='rejected'")
    elif fault=='quantity':monkeypatch.setattr(rc,'get_positions',lambda:[dict(symbol='AAPL',asset_class='us_equity',side='long',qty=3,avg_entry_price=100)])
    elif fault=='parent_id':monkeypatch.setattr(rc,'get_order',lambda _:{'id':'foreign','legs':[{'id':'tp'}]})
    elif fault=='legs':monkeypatch.setattr(rc,'get_order',lambda _:{'id':'parent','legs':[]})
    else:recovery.side_effect=RuntimeError('failed to start')
    assert rc.reconcile_open_positions()==0
    assert store.get_kill_switch()[0]
    assert 'AAPL' not in rc._watched
