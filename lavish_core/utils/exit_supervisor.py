"""Supervise an exit worker without treating thread existence as protection."""
import logging
import threading

log = logging.getLogger(__name__)

def start_exit_worker(target, args, kwargs, *, symbol, db_path, callback_index, bind_position_epoch=False):
    args = list(args)
    kwargs = dict(kwargs)
    callback = args[callback_index] if len(args) > callback_index else kwargs.get('on_exit')
    reported = []

    def halt():
        try:
            from lavish_core.db.hybrid_store import HybridStore
            store = HybridStore(db_path)
            try:
                store.set_kill_switch(True, 'Exit monitor failed or ended without a verified close')
            finally:
                store.con.close()
        except Exception as exc:
            log.error('Unable to persist exit-monitor halt (%s)', type(exc).__name__)

    def notify(result):
        if reported:
            return
        reported.append(result)
        if not isinstance(result, dict) or result.get('status') != 'exited':
            halt()
        if callback is not None:
            callback(result)

    if bind_position_epoch and 'position_epoch' not in kwargs:
        # Capture before scheduling the thread, not after a delayed start
        # could observe a different position with the same symbol/quantity.
        try:
            from lavish_core.db.hybrid_store import HybridStore, _positions_lock
            with _positions_lock:
                store = HybridStore(db_path)
                try:
                    kwargs['position_epoch'] = store.get_position_epoch(symbol)
                finally:
                    store.con.close()
        except Exception:
            halt()
            raise

    if len(args) > callback_index:
        args[callback_index] = notify
    else:
        kwargs['on_exit'] = notify

    def run():
        try:
            result = target(*args, **kwargs)
            if not isinstance(result, dict) or not result.get('status'):
                raise ValueError('Invalid exit-monitor outcome')
            if reported and reported[0].get('status') != result['status']:
                raise ValueError('Conflicting exit-monitor outcomes')
            notify(result)
        except Exception as exc:
            halt()
            log.error('Exit monitor failed for %s (%s)', symbol, type(exc).__name__)
            # A callback may already have booked a fill before raising.
            # Never call it again or expose the original exception body.
            if not reported:
                try:
                    notify({'status':'monitor_failed','error_type':type(exc).__name__})
                except Exception as callback_error:
                    log.error('Exit failure callback failed (%s)', type(callback_error).__name__)
        finally:
            from lavish_core.trade.reconcile import unmark_watched
            unmark_watched(symbol)

    thread = threading.Thread(target=run, daemon=True)
    try:
        thread.start()
    except Exception:
        halt()
        from lavish_core.trade.reconcile import unmark_watched
        unmark_watched(symbol)
        raise
    return thread
