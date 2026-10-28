"""Cumulative native-bracket receipts, including partial executions.

This only books observed broker executions. It never submits/replaces/cancels
an order, invents an external fill, or clears a reconciliation halt.
"""
from lavish_core.utils.safety import finite_number


def book_receipt(store, *, order_id, symbol, quantity, epoch, leg_id, leg, venue):
    cumulative = finite_number(leg.get('filled_qty', 0), nonnegative=True)
    if cumulative > quantity or (leg['status'] == 'filled' and cumulative != quantity):
        raise ValueError('Invalid native bracket cumulative quantity')
    identity = f'equity-exit:{leg_id}'
    prefix = identity + ':'
    rows = store.fetchall(
        'SELECT f.id,f.order_id,f.symbol,f.side,f.qty,f.price,f.fee '
        'FROM fill_events e LEFT JOIN fills f ON f.id=e.fill_id '
        'WHERE e.event_id=? OR substr(e.event_id,1,?)=?',
        (identity, len(prefix), prefix))
    if any(row[1] != order_id or row[2] != symbol.upper() or row[3] != 'sell' for row in rows):
        raise ValueError('Native bracket receipt identity changed')
    recorded = sum(row[4] for row in rows)
    notional = sum(row[4] * row[5] for row in rows)
    recorded_fee = sum(row[6] for row in rows)
    fee = finite_number(leg.get('filled_fee', recorded_fee), nonnegative=True)
    delta = cumulative - recorded
    if delta < 0 or fee < recorded_fee or (not cumulative and fee):
        raise ValueError('Regressing or orphan native bracket execution')
    average = finite_number(leg.get('filled_avg_price'), positive=True) if cumulative else 0
    if not delta and cumulative and abs(notional - cumulative * average) > 1e-8:
        raise ValueError('Native bracket execution price changed')
    if delta:
        total = store.fetchall(
            "SELECT coalesce(sum(f.qty),0) FROM fill_events e JOIN fills f ON f.id=e.fill_id "
            "WHERE f.order_id=? AND substr(e.event_id,1,12)='equity-exit:'",
            (order_id,))[0][0]
        if total + delta > quantity:
            raise ValueError('Native bracket legs exceed the original entry')
        position = store.get_position(symbol)
        if (store.get_position_epoch(symbol) != epoch or not position
                or delta > position['qty']):
            raise ValueError('Native bracket execution does not own inventory')
        incremental_price = finite_number((cumulative * average - notional) / delta, positive=True)
        # Partial protection and outcome aggregation require review even if a
        # later snapshot completes. Persist the halt BEFORE changing exposure.
        if cumulative < quantity:
            store.set_kill_switch(True, 'Native bracket partially filled; remaining protection requires reconciliation')
        store.log_fill(order_id, symbol, 'sell', delta, incremental_price,
                       fee=fee-recorded_fee, venue=venue,
                       event_id=identity if not rows and cumulative == quantity else prefix + format(cumulative, '.17g'))
    elif fee > recorded_fee:
        store.set_kill_switch(True, 'Native bracket fee changed; outcome reconciliation required')
        store.execute('UPDATE fills SET fee=fee+? WHERE id=?', (fee-recorded_fee, rows[-1][0]))
        return dict(fee_delta=fee-recorded_fee, review_required=True)
    return dict(cumulative=cumulative, notional=cumulative*average, fee=fee,
                newly_recorded=bool(delta), review_required=False)
