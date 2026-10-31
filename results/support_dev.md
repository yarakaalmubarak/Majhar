# Support-desk agent (rule-based policy engine): 200 generated scenarios

*Reproducible: deterministic simulator, fixed seeds. code_fingerprint=0b5ab5c54fe7, python=3.13.18, generator_seed=9090, operator=approve, auto_refund_limit_sar=300, reopen_closed_tickets=True*

Each scenario is a booking platform with a crowd of customers and 0 to 4 hidden faults, run for 40 minutes. The agent watches the payment ledger and order records, opens tickets, fixes causes, makes customers whole and tells them. A simulated support lead answers approval requests after 3 minutes. Outcomes are judged on hidden ground truth. 95% Wilson intervals. No agent code was changed after this set was generated.

Mix: 15 healthy, 67 single-fault, 118 multi-fault scenarios; 4331 customers affected in total.

## Headline: did the agent own the process?

| measure | result |
|---|---|
| affected customers who ended whole (seat + ticket, or refund) | 4329/4331 (100%, CI 99.8-100%) |
| tickets resolved with no human involved | 4129/4329 (95%, CI 94.7-96%) |
| human touches per 100 tickets | 4.6 |
| tickets closed while the customer was NOT whole (false closures) | 0/4329 (0%, CI 0-0.1%) |
| refunds above the limit paid without approval (policy violations) | 0 |
| tickets handed to a human | 2 |
| tickets still open at the end | 0 |
| median minutes from ticket to resolution | 1 |
| platform healthy at the end (no active fault, nobody waiting) | 197/200 (98%, CI 95.7-99.5%) |

## Compared with simpler approaches

| approach | customers whole | money refunded (SAR) | orders kept (SAR) | platform healthy at end | new victims in the last 10 min |
|---|---|---|---|---|---|
| Majhar agent | 4329/4331 (100%, CI 99.8-100%) | 167400 | 1271550 | 197/200 (98%, CI 95.7-99.5%) | 0 |
| auto-refund bot (refunds everyone, never looks for the cause) | 25688/38009 (68%, CI 67.1-68.1%) | 9660300 | 0 | 47/200 (24%, CI 18.2-29.8%) | 10946 |
| no automation (customers wait for support) | 0/28879 (0%, CI 0-0%) | 0 | 0 | 47/200 (24%, CI 18.2-29.8%) | 6279 |

The auto-refund bot gives the money back for every problem and never fixes the cause, so customers keep being affected until the end. Majhar keeps the sale whenever it can and stops the problem at its source.

## By fault present (agent)

| fault in scenario | scenarios | customers whole | tickets with no human |
|---|---|---|---|
| (healthy) | 15 | - | - |
| confirmation_worker_down | 43 | 1241/1241 (100%, CI 99.7-100%) | 1213/1241 (98%, CI 96.8-98.4%) |
| duplicate_sale | 48 | 906/906 (100%, CI 99.6-100%) | 863/906 (95%, CI 93.7-96.5%) |
| hold_ttl_bug | 50 | 1892/1892 (100%, CI 99.8-100%) | 1804/1892 (95%, CI 94.3-96.2%) |
| payment_webhook_drop | 42 | 931/931 (100%, CI 99.6-100%) | 880/931 (95%, CI 92.9-95.8%) |
| queue_overflow_drop | 36 | 800/802 (100%, CI 99.1-99.9%) | 754/800 (94%, CI 92.4-95.7%) |
| refund_worker_failing | 34 | 825/825 (100%, CI 99.5-100%) | 803/825 (97%, CI 96-98.2%) |
| seat_assign_timeout | 46 | 681/681 (100%, CI 99.4-100%) | 611/681 (90%, CI 87.2-91.8%) |
| seat_sync_corruption | 42 | 1322/1322 (100%, CI 99.7-100%) | 1243/1322 (94%, CI 92.6-95.2%) |
| ticket_render_bug | 40 | 1913/1913 (100%, CI 99.8-100%) | 1835/1913 (96%, CI 94.9-96.7%) |

## Where it falls short (2 of 200 scenarios)

| scenario | faults | affected | whole | escalated | still open | false closures |
|---|---|---|---|---|---|---|
| u067 | queue_overflow_drop | 1 | 0 | 1 | 0 | 0 |
| u155 | queue_overflow_drop | 1 | 0 | 1 | 0 | 0 |