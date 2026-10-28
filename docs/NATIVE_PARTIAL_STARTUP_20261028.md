# Native partial startup handoff

Follow-up to PR43: the real startup reconciler still required the original
entry quantity to equal today's remaining position, so it could not restart
observation after a confirmed native partial. One new synthetic integration
case reproduced that failure before this change.

For native brackets only, reconcile the broker's remaining quantity with the
local position and original entry's net confirmed fills. Verify the entry
epoch, original buy receipts, venue, parent order identity/side/quantity and
known child IDs. Pass ORIGINAL requested quantity plus the immutable epoch to
the watcher, which books only the unrecorded cumulative delta. Changed or
unverifiable holdings still halt; an active non-native plan with a changed
quantity is not silently resized.

Tests include a real reconciler-to-watcher handoff (deterministic scheduling,
mocked broker reads), completion cash/notional/fees, and parent/epoch/remaining
inventory rejection controls. No broker orders, halt clearing, protective
replacement, manual-source inference or execution activation. Same-quantity
external round trips and complete creator/native/manual orchestration remain
unverified/unfinished, not solved by quantity reconciliation.
