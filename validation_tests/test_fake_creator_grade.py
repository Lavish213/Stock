from lavish_core.validation.fake_creator import grade
import pytest

def row(accepted=True,latency=100,profit=20):
    return dict(passed=True,entry_expected=accepted,dispatch_wall_ms=latency,
                monitor_poll_seconds=.05,first_eligible_exit_at=.1,
                events=[{'elapsed':0},{'elapsed':.1}],final_cash=100000+profit)

def test_rejecting_everything_cannot_get_a():
    assert grade([row(False)],3000)['benchmark_grade']=='F'

def test_profitable_failure_cannot_get_a():
    assert grade([row(profit=9999),{'passed':False}],3000)['benchmark_grade']=='F'

def test_correct_losses_do_not_reduce_correctness_grade():
    assert grade([row(profit=-20),row(False)],3000)['benchmark_grade']=='A'

def test_latency_failure_downgrades_grade():
    assert grade([row(latency=4000),row(False)],3000)['benchmark_grade']=='B'

@pytest.mark.parametrize('budget',[float('nan'),float('inf'),0,-1])
def test_invalid_latency_budget_cannot_suppress_failures(budget):
    with pytest.raises(ValueError):grade([row()],budget)
