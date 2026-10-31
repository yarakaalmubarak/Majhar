# Support-desk agent (rule-based policy engine): 200 generated scenarios

*Reproducible: deterministic simulator, fixed seeds. code_fingerprint=0b5ab5c54fe7, python=3.13.18, generator_seed=5150, operator=deny_all, auto_refund_limit_sar=300, reopen_closed_tickets=False*

Each scenario is a booking platform with a crowd of customers and 0 to 4 hidden faults, run for 40 minutes. The agent watches the payment ledger and order records, opens tickets, fixes causes, makes customers whole and tells them. A simulated support lead answers approval requests after 3 minutes. Outcomes are judged on hidden ground truth. 95% Wilson intervals. No agent code was changed after this set was generated.

Mix: 12 healthy, 54 single-fault, 134 multi-fault scenarios; 10362 customers affected in total.

## Headline: did the agent own the process?

| measure | result |
|---|---|
| affected customers who ended whole (seat + ticket, or refund) | 1816/10362 (18%, CI 16.8-18.3%) |
| tickets resolved with no human involved | 1817/1817 (100%, CI 99.8-100%) |
| human touches per 100 tickets | 2.4 |
| tickets closed while the customer was NOT whole (false closures) | 1/1817 (0%, CI 0-0.3%) |
| refunds above the limit paid without approval (policy violations) | 0 |
| tickets handed to a human | 8174 |
| tickets still open at the end | 0 |
| median minutes from ticket to resolution | 1 |
| platform healthy at the end (no active fault, nobody waiting) | 106/200 (53%, CI 46.1-59.8%) |

## Compared with simpler approaches

| approach | customers whole | money refunded (SAR) | orders kept (SAR) | platform healthy at end | new victims in the last 10 min |
|---|---|---|---|---|---|
| Majhar agent | 1816/10362 (18%, CI 16.8-18.3%) | 79350 | 522900 | 106/200 (53%, CI 46.1-59.8%) | 1143 |
| auto-refund bot (refunds everyone, never looks for the cause) | 39611/52113 (76%, CI 75.6-76.4%) | 14036250 | 0 | 42/200 (21%, CI 15.9-27.2%) | 15175 |
| no automation (customers wait for support) | 0/38577 (0%, CI 0-0%) | 0 | 0 | 42/200 (21%, CI 15.9-27.2%) | 7894 |

The auto-refund bot gives the money back for every problem and never fixes the cause, so customers keep being affected until the end. Majhar keeps the sale whenever it can and stops the problem at its source.

## By fault present (agent)

| fault in scenario | scenarios | customers whole | tickets with no human |
|---|---|---|---|
| (healthy) | 12 | - | - |
| confirmation_worker_down | 54 | 874/2263 (39%, CI 36.6-40.6%) | 874/874 (100%, CI 99.6-100%) |
| duplicate_sale | 41 | 240/2393 (10%, CI 8.9-11.3%) | 241/241 (100%, CI 98.4-100%) |
| hold_ttl_bug | 49 | 951/2914 (33%, CI 31-34.4%) | 951/951 (100%, CI 99.6-100%) |
| payment_webhook_drop | 51 | 502/1958 (26%, CI 23.8-27.6%) | 502/502 (100%, CI 99.2-100%) |
| queue_overflow_drop | 37 | 384/2141 (18%, CI 16.4-19.6%) | 384/384 (100%, CI 99-100%) |
| refund_worker_failing | 41 | 211/1738 (12%, CI 10.7-13.8%) | 212/212 (100%, CI 98.2-100%) |
| seat_assign_timeout | 32 | 382/1915 (20%, CI 18.2-21.8%) | 383/383 (100%, CI 99-100%) |
| seat_sync_corruption | 43 | 214/1656 (13%, CI 11.4-14.6%) | 215/215 (100%, CI 98.2-100%) |
| ticket_render_bug | 51 | 248/8167 (3%, CI 2.7-3.4%) | 248/248 (100%, CI 98.5-100%) |

## Where it falls short (92 of 200 scenarios)

| scenario | faults | affected | whole | escalated | still open | false closures |
|---|---|---|---|---|---|---|
| u002 | seat_assign_timeout, seat_sync_corruption | 39 | 4 | 35 | 0 | 0 |
| u006 | ticket_render_bug, duplicate_sale | 5 | 0 | 5 | 0 | 0 |
| u008 | ticket_render_bug, payment_webhook_drop | 297 | 2 | 261 | 0 | 0 |
| u009 | refund_worker_failing, seat_assign_timeout, confirmation_worker_down | 12 | 9 | 3 | 0 | 0 |
| u010 | seat_assign_timeout, ticket_render_bug, payment_webhook_drop | 319 | 3 | 316 | 0 | 0 |
| u011 | hold_ttl_bug, confirmation_worker_down, payment_webhook_drop | 121 | 114 | 7 | 0 | 0 |
| u012 | ticket_render_bug, confirmation_worker_down, queue_overflow_drop | 151 | 2 | 130 | 0 | 0 |
| u013 | payment_webhook_drop, seat_sync_corruption | 27 | 12 | 15 | 0 | 0 |
| u017 | seat_assign_timeout | 24 | 4 | 20 | 0 | 0 |
| u018 | seat_sync_corruption | 15 | 0 | 15 | 0 | 0 |
| u019 | duplicate_sale, seat_sync_corruption, seat_assign_timeout, refund_worker_failing | 21 | 3 | 17 | 0 | 1 |
| u020 | payment_webhook_drop, duplicate_sale, ticket_render_bug | 73 | 5 | 68 | 0 | 0 |
| u023 | seat_assign_timeout, queue_overflow_drop, seat_sync_corruption | 35 | 20 | 15 | 0 | 0 |
| u024 | seat_sync_corruption, confirmation_worker_down | 20 | 5 | 15 | 0 | 0 |
| u026 | refund_worker_failing, ticket_render_bug | 102 | 0 | 102 | 0 | 0 |