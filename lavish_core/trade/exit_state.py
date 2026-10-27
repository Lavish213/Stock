"""Keep one closing order in flight until its outcome is definitive."""
from lavish_core.utils.safety import finite_number
import uuid

QTY_TOLERANCE = 1e-12

class ExitState:
    def __init__(self, qty, *, db_path=None, symbol=None, venue="paper", position_epoch=...):
        self.db_path = db_path
        self.symbol = symbol
        self.venue = venue
        self.journal_id = None
        self.baseline_qty = 0.0
        self.baseline_notional = 0.0
        self.adopted = False
        self.retry_client_id = None
        from lavish_core.trade.broker import BROKER_PROVIDER
        self.provider = BROKER_PROVIDER
        self.quantity = finite_number(qty, positive=True)
        self.remaining = self.quantity
        self.completed = 0.0
        self.notional = 0.0
        self.pending = None
        self.client_id = None
        self.unknown = False
        self.position_epoch = position_epoch
        if db_path is not None and position_epoch is ...:
            from lavish_core.db.hybrid_store import _positions_lock
            with _positions_lock:
                store = self._store()
                try:
                    self.position_epoch = store.get_position_epoch(symbol)
                finally:
                    store.con.close()

    def _owns_position(self, store):
        return bool(self.position_epoch and self.position_epoch is not ...
                    and store.get_position_epoch(self.symbol) == self.position_epoch)

    def _store(self):
        from lavish_core.db.hybrid_store import HybridStore
        return HybridStore(self.db_path)

    def _start_journal(self):
        if self.db_path is None:
            return True
        from lavish_core.db.hybrid_store import _positions_lock
        with _positions_lock:
            store = self._store()
            try:
                candidates = [
                    order for order in store.get_open_orders(self.symbol)
                    if order["side"] == "sell" and order["meta"].get("exit_state")
                ]
                if not self._owns_position(store):
                    store.set_kill_switch(True, "Exit worker position lifecycle mismatch; reconciliation required")
                    return False
                ambiguous = (
                    len(candidates) > 1
                    or (
                        len(candidates) == 1
                        and (
                            candidates[0]["venue"] != self.venue
                            or not candidates[0]["client_id"]
                            or candidates[0]["status"] == "fill_unverified"
                            or candidates[0]["meta"].get("position_epoch") != self.position_epoch
                        )
                    )
                )
                if ambiguous:
                    for order in candidates:
                        store.set_order_status(order["id"], "fill_unverified")
                    store.set_kill_switch(
                        True,
                        "Closing order identity ambiguous; broker reconciliation required",
                    )
                    return False
                position = store.get_position(self.symbol)
                if (not position
                        or self.remaining - finite_number(position["qty"], positive=True) > QTY_TOLERANCE):
                    # Another close/trim may have finished since this worker
                    # captured its plan. Never send the stale quantity to the
                    # broker, even when the old journal is now terminal.
                    for order in candidates:
                        store.set_order_status(order["id"], "fill_unverified")
                    store.set_kill_switch(True, "Exit plan exceeds confirmed inventory; reconciliation required")
                    return False
                if candidates:
                    order = candidates[0]
                    self.journal_id = order["id"]
                    self.client_id = order["client_id"]
                    self.baseline_qty,self.baseline_notional = store.fetchall("SELECT coalesce(sum(qty),0),coalesce(sum(qty*price),0) FROM fills WHERE order_id=?",(self.journal_id,))[0]
                    self.unknown = True
                    self.adopted = True
                    return True
                self.journal_id = store.submit_order(self.symbol,"sell",self.remaining,venue=self.venue,status="pending_submit",client_id=self.client_id,meta={"exit_state":True,"position_epoch":self.position_epoch})
                return True
            finally:
                store.con.close()

    def _journal_status(self, status):
        if self.db_path is not None and self.journal_id:
            store = self._store()
            try:
                store.set_order_status(self.journal_id,status)
            finally:
                store.con.close()

    def _verify_identity(self, order, expected_id=None):
        if (not isinstance(order, dict) or not order.get('id')
                or (expected_id is not None and order['id'] != expected_id)
                or ('symbol' in order and self.symbol is not None and order['symbol'] != self.symbol)
                or ('side' in order and str(order['side']).lower() != 'sell')
                or ('client_order_id' in order and order['client_order_id'] != self.client_id)):
            raise ValueError('Closing order identity mismatch')
        if 'qty' in order and finite_number(order['qty'],positive=True) != self.remaining+self.baseline_qty:
            raise ValueError('Closing order quantity mismatch')

    def _quarantine_identity(self):
        self._journal_status('fill_unverified')
        if self.db_path is not None:
            store = self._store()
            try:
                store.set_kill_switch(True,'Closing order snapshot unverified; broker reconciliation required')
            finally:
                store.con.close()
        return {'status':'fill_unverified','reason':'reconciliation_required'}

    def _record_snapshot(self, order):
        if self.db_path is None or not self.journal_id:
            return
        from lavish_core.db.hybrid_store import _positions_lock
        with _positions_lock:
            cumulative = finite_number(order.get("filled_qty") or 0, nonnegative=True)
            store = self._store()
            try:
                if not self._owns_position(store):
                    raise ValueError("Exit snapshot position lifecycle mismatch")
                journal = store.fetchall("SELECT status,meta FROM orders WHERE id=?", (self.journal_id,))[0]
                import json
                if (journal[0] == "fill_unverified"
                        or json.loads(journal[1] or '{}').get('position_epoch') != self.position_epoch):
                    raise ValueError("Closing journal ownership unverified")
                requested = store.fetchall("SELECT qty FROM orders WHERE id=?",(self.journal_id,))[0][0]
                if cumulative > requested:
                    raise ValueError("Exit quantity exceeds request")
                if order.get('status') == 'filled' and cumulative != requested:
                    # Validate terminal completeness before booking any delta.
                    # An inconsistent snapshot must not reduce durable exposure.
                    raise ValueError('Filled exit quantity does not match request')
                recorded,notional,recorded_fee=store.fetchall("SELECT coalesce(sum(qty),0),coalesce(sum(qty*price),0),coalesce(sum(fee),0) FROM fills WHERE order_id=?",(self.journal_id,))[0]
                delta = cumulative-recorded
                if delta < 0:
                    raise ValueError("Regressing exit quantity")
                fee=finite_number(order.get("filled_fee",recorded_fee),nonnegative=True)-recorded_fee
                if fee < 0:
                    raise ValueError("Regressing execution fees")
                if not cumulative and fee > 0:
                    raise ValueError("Fee without an execution")
                if delta > 0:
                    position = store.get_position(self.symbol)
                    if (not position
                            or delta - finite_number(position["qty"], positive=True) > QTY_TOLERANCE):
                        raise ValueError("Exit fill exceeds confirmed inventory")
                    # As in generic reconciliation, tolerate only bounded
                    # binary-float residue; never persist a negative position.
                    if delta > position["qty"]:
                        delta = position["qty"]
                    average = finite_number(order.get("filled_avg_price"),positive=True)
                    incremental = finite_number((cumulative*average-notional)/delta,positive=True)
                    store.log_fill(self.journal_id,self.symbol,"sell",delta,incremental,fee=fee,venue=self.venue,event_id=f"reconcile:{order['id']}:{cumulative}")
                elif fee > 0:
                    fid=store.fetchall('SELECT id FROM fills WHERE order_id=? ORDER BY ts DESC LIMIT 1',(self.journal_id,))[0][0]
                    store.execute('UPDATE fills SET fee=fee+? WHERE id=?',(fee,fid))
                store.set_order_status(self.journal_id,order.get("status","submission_unknown"))
            finally:
                store.con.close()

    def attempt(self, submit, lookup, get_order, cancel, poll_attempts, sleep, *, allow_submit=True):
        """Return None while unresolved; never replace an unconfirmed order.

        Cancellation acceptance alone is insufficient: poll a terminal
        broker status before submitting the remaining quantity. Partial
        executions reduce that remaining quantity and contribute to the
        final weighted fill price.
        """
        if self.db_path is not None:
            from lavish_core.db.hybrid_store import _positions_lock
            with _positions_lock:
                store = self._store()
                try:
                    owns_position = self._owns_position(store)
                finally:
                    store.con.close()
            if not owns_position:
                return self._quarantine_identity()
        if self.unknown:
            try:
                remote = lookup(self.client_id)
            except Exception:
                return None
            if remote is None:
                if self.adopted or self.provider == "tradier":
                    return None
                self._journal_status("rejected")
                self.journal_id = None
                self.retry_client_id = self.client_id
                self.unknown = False
            elif isinstance(remote, dict) and remote.get('id'):
                self.pending = remote
                self.unknown = False
            else:
                return None
        if self.pending is None:
            if not allow_submit:
                return None
            self.client_id = self.retry_client_id or ('exit_' + uuid.uuid4().hex[:24])
            self.retry_client_id = None
            if not self._start_journal():
                return {'status':'fill_unverified','reason':'reconciliation_required'}
            if self.adopted:
                return None
            try:
                self.pending = submit(self.remaining, self.client_id)
            except Exception:
                self.unknown = True
                self._journal_status("submission_unknown")
                return None
            if not isinstance(self.pending,dict) or not self.pending.get('id'):
                self.unknown = True
                self.pending = None
                self._journal_status("submission_unknown")
                return None
        order = self.pending
        try:
            self._verify_identity(order)
        except ValueError:
            return self._quarantine_identity()
        expected_id = order['id']
        for _ in range(poll_attempts):
            if order.get('status') in ('filled','canceled','expired','rejected'):
                break
            sleep()
            try:
                result = get_order(order['id'])
                if isinstance(result,dict):
                    candidate = dict(order,**result)
                    try:
                        self._verify_identity(candidate,expected_id)
                    except ValueError:
                        return self._quarantine_identity()
                    order = candidate
                    self.pending = order
            except Exception:
                return None
        status = order.get('status')
        try:
            self._record_snapshot(order)
        except ValueError:
            return self._quarantine_identity()
        if status not in ('filled','canceled','expired','rejected'):
            try:
                cancel(order['id'])
            except Exception:
                pass
            # Even an accepted DELETE is asynchronous; keep this order.
            return None
        try:
            cumulative = finite_number(order.get('filled_qty') or 0, nonnegative=True)
            filled = cumulative-self.baseline_qty
            if filled > self.remaining or (status == 'filled' and filled != self.remaining):
                raise ValueError('Invalid closing fill quantity')
            average = finite_number(order.get('filled_avg_price'), positive=True) if filled else 0
            price = finite_number((cumulative*average-self.baseline_notional)/filled,positive=True) if filled else 0
        except ValueError:
            return self._quarantine_identity()
        self.completed += filled
        self.notional += filled*price
        self.remaining -= filled
        self.pending = None
        self.journal_id = None
        self.baseline_qty = self.baseline_notional = 0.0
        self.adopted = False
        if self.remaining == 0:
            return {'status':'exited','price':self.notional/self.completed,'filled_qty':self.completed,'order':order,'ledger_recorded':self.db_path is not None}
        return None
