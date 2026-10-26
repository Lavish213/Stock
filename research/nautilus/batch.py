"""Run a fixed 9-case engine-compatibility matrix, never optimize a strategy."""
import argparse
import hashlib
import json
from pathlib import Path

from smoke import run


def batch(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    outcomes = []
    for quantity in (1, 2, 3):
        for latency in (0, 100_000_000, 1_500_000_000):
            name = f"q{quantity}-latency{latency}.json"
            try:
                result = run(latency_ns=latency, quantity=quantity)
                payload = json.dumps(result, indent=2).encode()
                with (output / name).open("xb") as receipt:
                    receipt.write(payload)
                outcomes.append(dict(file=name, quantity=quantity, latency_ns=latency,
                    passed=result["passed"], sha256=hashlib.sha256(payload).hexdigest()))
            except Exception as error:
                outcomes.append(dict(file=name, quantity=quantity, latency_ns=latency,
                    passed=False, error_type=type(error).__name__))
    result = dict(synthetic=True, creator_replay=False, profitability_evidence=False,
        real_broker_orders=0, expected_cases=9, observed_cases=len(outcomes),
        passed=len(outcomes) == 9 and all(row["passed"] for row in outcomes),
        outcomes=outcomes,
        scope="One invented options tape; nine parameter variations, not nine independent market samples")
    with (output / "summary.json").open("x") as summary:
        json.dump(result, summary, indent=2)
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    result = batch(args.output)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
