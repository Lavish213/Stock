"""Measure misses and unsafe routing separately; never touch a broker."""
import pytest
from lavish_core.validation.intent_benchmark import corpus,randomized_corpus,evaluate


@pytest.mark.parametrize('challenge',[False,True])
def test_labeled_development_and_challenge_corpus(challenge):
    result=evaluate(corpus(challenge=challenge))
    assert result['passed'], result['strata']
    assert result['incorrect']==0
    assert result['unsafe_dispatches']==0
    assert result['real_broker_orders']==0


def test_new_seed_randomized_identity_fields_and_hidden_negations():
    result=evaluate(randomized_corpus(seed=32452843))
    assert result['passed'], result['strata']
    assert result['incorrect']==0
    assert result['real_creator_accuracy_established'] is False


def test_benchmark_rejection_of_everything_cannot_pass(monkeypatch):
    from lavish_core.trading import alert_handler
    monkeypatch.setattr(alert_handler,'handle_alert_text',lambda *args,**kwargs:None)
    result=evaluate(corpus()[:200])
    assert not result['passed']
    assert result['incorrect']>0
