# Support-desk agent (rule-based policy engine): 200 generated scenarios

*Reproducible: deterministic simulator, fixed seeds. code_fingerprint=0b5ab5c54fe7, python=3.13.18, generator_seed=5150, operator=approve, auto_refund_limit_sar=300, reopen_closed_tickets=True*

Each scenario is a booking platform with a crowd of customers and 0 to 4 hidden faults, run for 40 minutes. The agent watches the payment ledger and order records, opens tickets, fixes causes, makes customers whole and tells them. A simulated support lead answers approval requests after 3 minutes. Outcomes are judged on hidden ground truth. 95% Wilson intervals. No agent code was changed after this set was generated.

Mix: 12 healthy, 54 single-fault, 134 multi-fault scenarios; 5725 customers affected in total.

## Headline: did the agent own the process?

| measure | result |
|---|---|
| affected customers who ended whole (seat + ticket, or refund) | 5725/5725 (100%, CI 99.9-100%) |
| tickets resolved with no human involved | 5482/5725 (96%, CI 95.2-96.2%) |
| human touches per 100 tickets | 4.2 |
| tickets closed while the customer was NOT whole (false closures) | 0/5725 (0%, CI 0-0.1%) |
| refunds above the limit paid without approval (policy violations) | 0 |
| tickets handed to a human | 0 |
| tickets still open at the end | 0 |
| median minutes from ticket to resolution | 2 |
| platform healthy at the end (no active fault, nobody waiting) | 197/200 (98%, CI 95.7-99.5%) |

## Compared with simpler approaches

| approach | customers whole | money refunded (SAR) | orders kept (SAR) | platform healthy at end | new victims in the last 10 min |
|---|---|---|---|---|---|
| Majhar agent | 5725/5725 (100%, CI 99.9-100%) | 149100 | 1773750 | 197/200 (98%, CI 95.7-99.5%) | 0 |
| auto-refund bot (refunds everyone, never looks for the cause) | 39611/52113 (76%, CI 75.6-76.4%) | 14036250 | 0 | 42/200 (21%, CI 15.9-27.2%) | 15175 |
| no automation (customers wait for support) | 0/38577 (0%, CI 0-0%) | 0 | 0 | 42/200 (21%, CI 15.9-27.2%) | 7894 |

The auto-refund bot gives the money back for every problem and never fixes the cause, so customers keep being affected until the end. Majhar keeps the sale whenever it can and stops the problem at its source.

## By fault present (agent)

| fault in scenario | scenarios | customers whole | tickets with no human |
|---|---|---|---|
| (healthy) | 12 | - | - |
| confirmation_worker_down | 54 | 1768/1768 (100%, CI 99.8-100%) | 1694/1768 (96%, CI 94.8-96.7%) |
| duplicate_sale | 41 | 1225/1225 (100%, CI 99.7-100%) | 1172/1225 (96%, CI 94.4-96.7%) |
| hold_ttl_bug | 49 | 1894/1894 (100%, CI 99.8-100%) | 1815/1894 (96%, CI 94.8-96.6%) |
| payment_webhook_drop | 51 | 995/995 (100%, CI 99.6-100%) | 969/995 (97%, CI 96.2-98.2%) |
| queue_overflow_drop | 37 | 962/962 (100%, CI 99.6-100%) | 926/962 (96%, CI 94.9-97.3%) |
| refund_worker_failing | 41 | 1006/1006 (100%, CI 99.6-100%) | 965/1006 (96%, CI 94.5-97%) |
| seat_assign_timeout | 32 | 1148/1148 (100%, CI 99.7-100%) | 1025/1148 (89%, CI 87.4-90.9%) |
| seat_sync_corruption | 43 | 1253/1253 (100%, CI 99.7-100%) | 1169/1253 (93%, CI 91.8-94.6%) |
| ticket_render_bug | 51 | 3530/3530 (100%, CI 99.9-100%) | 3424/3530 (97%, CI 96.4-97.5%) |

## Where it falls short (0 of 200 scenarios)

None.