# Support-desk agent (rule-based policy engine): 200 generated scenarios

*Reproducible: deterministic simulator, fixed seeds. code_fingerprint=0b5ab5c54fe7, python=3.13.18, generator_seed=9090, operator=deny_all, auto_refund_limit_sar=300, reopen_closed_tickets=True*

Each scenario is a booking platform with a crowd of customers and 0 to 4 hidden faults, run for 40 minutes. The agent watches the payment ledger and order records, opens tickets, fixes causes, makes customers whole and tells them. A simulated support lead answers approval requests after 3 minutes. Outcomes are judged on hidden ground truth. 95% Wilson intervals. No agent code was changed after this set was generated.

Mix: 15 healthy, 67 single-fault, 118 multi-fault scenarios; 7253 customers affected in total.

## Headline: did the agent own the process?

| measure | result |
|---|---|
| affected customers who ended whole (seat + ticket, or refund) | 1989/7253 (27%, CI 26.4-28.5%) |
| tickets resolved with no human involved | 1989/1989 (100%, CI 99.8-100%) |
| human touches per 100 tickets | 2.8 |
| tickets closed while the customer was NOT whole (false closures) | 0/1989 (0%, CI 0-0.2%) |
| refunds above the limit paid without approval (policy violations) | 0 |
| tickets handed to a human | 5040 |
| tickets still open at the end | 0 |
| median minutes from ticket to resolution | 1 |
| platform healthy at the end (no active fault, nobody waiting) | 119/200 (60%, CI 52.6-66.1%) |

## Compared with simpler approaches

| approach | customers whole | money refunded (SAR) | orders kept (SAR) | platform healthy at end | new victims in the last 10 min |
|---|---|---|---|---|---|
| Majhar agent | 1989/7253 (27%, CI 26.4-28.5%) | 113400 | 495750 | 119/200 (60%, CI 52.6-66.1%) | 657 |
| auto-refund bot (refunds everyone, never looks for the cause) | 25688/38009 (68%, CI 67.1-68.1%) | 9660300 | 0 | 47/200 (24%, CI 18.2-29.8%) | 10946 |
| no automation (customers wait for support) | 0/28879 (0%, CI 0-0%) | 0 | 0 | 47/200 (24%, CI 18.2-29.8%) | 6279 |

The auto-refund bot gives the money back for every problem and never fixes the cause, so customers keep being affected until the end. Majhar keeps the sale whenever it can and stops the problem at its source.

## By fault present (agent)

| fault in scenario | scenarios | customers whole | tickets with no human |
|---|---|---|---|
| (healthy) | 15 | - | - |
| confirmation_worker_down | 43 | 977/1352 (72%, CI 69.8-74.6%) | 977/977 (100%, CI 99.6-100%) |
| duplicate_sale | 48 | 390/1897 (21%, CI 18.8-22.4%) | 390/390 (100%, CI 99-100%) |
| hold_ttl_bug | 50 | 1232/3013 (41%, CI 39.1-42.7%) | 1232/1232 (100%, CI 99.7-100%) |
| payment_webhook_drop | 42 | 454/1614 (28%, CI 26-30.4%) | 454/454 (100%, CI 99.2-100%) |
| queue_overflow_drop | 36 | 433/1363 (32%, CI 29.4-34.3%) | 433/433 (100%, CI 99.1-100%) |
| refund_worker_failing | 34 | 621/1084 (57%, CI 54.3-60.2%) | 621/621 (100%, CI 99.4-100%) |
| seat_assign_timeout | 46 | 333/1381 (24%, CI 21.9-26.4%) | 333/333 (100%, CI 98.9-100%) |
| seat_sync_corruption | 42 | 178/2266 (8%, CI 6.8-9%) | 178/178 (100%, CI 98-100%) |
| ticket_render_bug | 40 | 115/4835 (2%, CI 2-2.8%) | 115/115 (100%, CI 97-100%) |

## Where it falls short (80 of 200 scenarios)

| scenario | faults | affected | whole | escalated | still open | false closures |
|---|---|---|---|---|---|---|
| u001 | hold_ttl_bug, ticket_render_bug | 102 | 0 | 102 | 0 | 0 |
| u003 | payment_webhook_drop, seat_sync_corruption | 31 | 16 | 15 | 0 | 0 |
| u004 | hold_ttl_bug, seat_assign_timeout, seat_sync_corruption | 18 | 3 | 15 | 0 | 0 |
| u006 | refund_worker_failing, seat_sync_corruption | 15 | 0 | 15 | 0 | 0 |
| u007 | hold_ttl_bug, payment_webhook_drop | 58 | 27 | 31 | 0 | 0 |
| u008 | ticket_render_bug | 27 | 0 | 27 | 0 | 0 |
| u009 | duplicate_sale, seat_sync_corruption, confirmation_worker_down, ticket_render_bug | 55 | 13 | 42 | 0 | 0 |
| u011 | ticket_render_bug, seat_assign_timeout | 149 | 0 | 130 | 0 | 0 |
| u015 | payment_webhook_drop, seat_sync_corruption | 18 | 3 | 15 | 0 | 0 |
| u016 | ticket_render_bug | 85 | 0 | 85 | 0 | 0 |
| u018 | seat_sync_corruption, payment_webhook_drop, refund_worker_failing, confirmation_worker_down | 44 | 29 | 15 | 0 | 0 |
| u024 | confirmation_worker_down, duplicate_sale, seat_sync_corruption, seat_assign_timeout | 32 | 17 | 15 | 0 | 0 |
| u026 | seat_sync_corruption, ticket_render_bug, refund_worker_failing, queue_overflow_drop | 15 | 0 | 15 | 0 | 0 |
| u028 | payment_webhook_drop, seat_sync_corruption, duplicate_sale, ticket_render_bug | 310 | 2 | 274 | 0 | 0 |
| u030 | confirmation_worker_down, queue_overflow_drop, seat_sync_corruption, hold_ttl_bug | 36 | 21 | 15 | 0 | 0 |