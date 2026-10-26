"""Core CI checks optional lab guardrails without installing Nautilus."""
import importlib.util
from pathlib import Path

import pytest


def load_smoke():
    path = Path(__file__).resolve().parents[1] / "research/nautilus/smoke.py"
    spec = importlib.util.spec_from_file_location("nautilus_research_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("latency", [-1, True, 2_000_000_001, .1])
def test_smoke_rejects_invalid_latency_before_loading_engine(latency):
    with pytest.raises(ValueError):
        load_smoke().run(latency_ns=latency)


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5, 4])
def test_smoke_rejects_invalid_quantity_before_loading_engine(quantity):
    with pytest.raises(ValueError):
        load_smoke().run(quantity=quantity)
