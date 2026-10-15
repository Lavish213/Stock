"""One-year synthetic mechanics stress; never historical or profit evidence."""
import argparse
import json
import statistics
from pathlib import Path
from .options_creator_month import generate, offline, parse, run


def experiment(trials=20):
    if type(trials) is not int or not 1 <= trials <= 200:
        raise ValueError('Trials must be 1..200')
    rows = []
    cache = {}
    def cached(text):
        if text not in cache:
            cache[text] = parse(text)
        return cache[text]
    with offline():
        for i in range(trials):
            seed = 20261015 + i
            cases = generate(seed, days=365)
            for initial in (100, 1000):
                for delay in (0, 60, 180, 360):
                    result = run(seed, delay, cached, initial, cases)
                    trades = result['trades']
                    peak = initial
                    drawdown = 0
                    for trade in trades:
                        peak = max(peak, trade['balance'])
                        drawdown = max(drawdown, 1 - trade['balance'] / peak)
                    rows.append(dict(seed=seed, initial=initial, delay=delay,
                        final=result['final'], trades=len(trades),
                        fees=round(sum(t['fees'] for t in trades), 2),
                        realized_max_drawdown=drawdown,
                        cash_error=result['cash_error'], real_orders=0,
                        statuses={s: sum(r['status'] == s for r in result['records'])
                                  for s in sorted({r['status'] for r in result['records']})}))
    summary = []
    for initial in (100, 1000):
        for delay in (0, 60, 180, 360):
            group = [r for r in rows if r['initial'] == initial and r['delay'] == delay]
            balances = [r['final'] for r in group]
            summary.append(dict(initial=initial, delay_seconds=delay, trials=trials,
                profitable=sum(x > initial for x in balances),
                median=statistics.median(balances), worst=min(balances), best=max(balances),
                trades=sum(r['trades'] for r in group)))
    return dict(synthetic=True, profitability_evidence=False,
        period='2026-10-01 through 2027-09-30',
        limitations=['Toy uncalibrated European call quotes, not authentic SPY contracts',
            'Weekday-only calendar: holidays and early closes not modeled',
            'Independent daily spot resets; not a continuous historical price series',
            'Production entry parser; synthetic account and exit adapter',
            'Drawdown uses closed balances, not intratrade mark-to-market',
            'No automatic learning, production execution or strategy promotion',
            '10% day loss and three-loss pause thresholds remain simulation assumptions'],
        summary=summary, outcomes=rows)


if __name__ == '__main__':
    cli = argparse.ArgumentParser()
    cli.add_argument('--output', required=True)
    cli.add_argument('--trials', type=int, default=20)
    args = cli.parse_args()
    result = experiment(args.trials)
    with Path(args.output).open('x') as target:
        json.dump(result, target, indent=2)
    print(json.dumps(result['summary'], indent=2))
