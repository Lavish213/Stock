# Production options exit trigger retry correction

## Reproduced defect

The monitor recomputed its target/stop decision every iteration. If an exit attempt was unresolved and price recovered, the next loop no longer called the same ExitState. A subsequent underlying-feed outage could also prevent retrying an already requested exit. The order identity was retained in memory, but the monitor stopped progressing it until another trigger or the maximum-hold timeout.

Three offline regressions failed on the prior code: stop/recovery, target/reversal and feed outage after a triggered exit. They reached the timeout close instead of completing the original requested exit. The correction latches the first exit reason and its underlying trigger price, retries the existing ExitState without requiring another underlying quote, and retains the reason across the monitor deadline. Target levels, stop percentages, quantities, entry rules and account settings are unchanged. Recorded underlying price on a retry is the original trigger observation, not a new market quote.

## Verification and scope

The three regressions passed after the fix. An additional integration test uses the real ExitState and HybridStore with a simulated lost acknowledgment: exactly one closing submission attempt, recovery by the same client ID, one sell fill, the supplied synthetic $0.65 fee recorded once, one completion callback and a flat local position. All broker interactions are mocks; no real orders were submitted.

Initial focused fill/protection/latch run: 48 passed in 4.94 seconds. Expanded latch file: four tests passed in 1.04 seconds. Full validation: 3,186 passed in 345.18 seconds; that collection included the first three regressions, with the fourth integration test verified separately afterward. These scopes overlap and must not be added together. Bandit found no issues in the changed production module.

This is in-process retry hardening, not a complete durable exit-intent protocol. A crash before a closing order is durably journaled can still lose the trigger. Creator close/trim parsing, authenticated intent routing, monitor coordination, trim quantities, restart intent recovery and the intermittent historical replay ledger anomaly remain separate unfinished work. Existing protection and halt behavior stays active. Do not interpret this repair as an autonomy or profitability certification.
