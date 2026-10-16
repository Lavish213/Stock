"""Keep one closing order in flight until its outcome is definitive."""
from lavish_core.utils.safety import finite_number
import uuid

class ExitState:
    def __init__(self, qty, *, db_path=None, symbol=None, venue="paper"):
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

    def _store(self):
        from lavish_core.db.hybrid_store import HybridStore
        return HybridStore(self.db_path)

    def _start_journal(self):
        if self.db_path is None:
            return
        from lavish_core.db.hybrid_store import _positions_lock
        with _positions_lock:
            store = self._store()
            try:
                for order in store.get_open_orders(self.symbol):
                    if order["side"] == "sell" and order["meta"].get("exit_state"):
                        self.journal_id = order["id"]
                        self.client_id = order["client_id"]
                        self.baseline_qty,self.baseline_notional = store.fetchall("SELECT coalesce(sum(qty),0),coalesce(sum(qty*price),0) FROM fills WHERE order_id=?",(self.journal_id,))[0]
                        self.unknown = True
                        self.adopted = True
                        return
                self.journal_id = store.submit_order(self.symbol,"sell",self.remaining,venue=self.venue,status="pending_submit",client_id=self.client_id,meta={"exit_state":True})
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
            self._start_journal()
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
