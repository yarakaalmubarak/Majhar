# Support-desk agent: 200 generated scenarios

*Reproducible: deterministic simulator, fixed seeds. code_fingerprint=07022362cd24, python=3.13.18, generator_seed=9090, operator=approve, auto_refund_limit_sar=1000*

Each scenario is a booking platform with a crowd of customers and 0 to 4 hidden faults, run for 40 minutes. The agent watches the payment ledger and order records, opens tickets, fixes causes, makes customers whole and tells them. A simulated support lead answers approval requests after 3 minutes. Outcomes are judged on hidden ground truth. 95% Wilson intervals. No agent code was changed after this set was generated.

Mix: 15 healthy, 67 single-fault, 118 multi-fault scenarios; 4331 customers affected in total.

## Headline: did the agent own the process?

| measure | result |
|---|---|
| affected customers who ended whole (seat + ticket, or refund) | 4329/4331 (100%, CI 99.8-100%) |
| tickets resolved with no human involved | 4249/4329 (98%, CI 97.7-98.5%) |
| human touches per 100 tickets | 1.8 |
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
| refund bot (refund everyone, never investigate) | 25688/38009 (68%, CI 67.1-68.1%) | 9660300 | 0 | 47/200 (24%, CI 18.2-29.8%) | 10946 |
| no agent | 0/28879 (0%, CI 0-0%) | 0 | 0 | 47/200 (24%, CI 18.2-29.8%) | 6279 |

The refund bot also makes people whole, but by giving the money back for every problem, and because it never fixes the cause, customers keep getting hurt until the end. The agent keeps the sale wherever it can and stops the cause.

## By fault present (agent)

| fault in scenario | scenarios | customers whole | tickets with no human |
|---|---|---|---|
| (healthy) | 15 | - | - |
| confirmation_worker_down | 43 | 1241/1241 (100%, CI 99.7-100%) | 1230/1241 (99%, CI 98.4-99.5%) |
| duplicate_sale | 48 | 906/906 (100%, CI 99.6-100%) | 884/906 (98%, CI 96.4-98.4%) |
| hold_ttl_bug | 50 | 1892/1892 (100%, CI 99.8-100%) | 1871/1892 (99%, CI 98.3-99.3%) |
| payment_webhook_drop | 42 | 931/931 (100%, CI 99.6-100%) | 918/931 (99%, CI 97.6-99.2%) |
| queue_overflow_drop | 36 | 800/802 (100%, CI 99.1-99.9%) | 789/800 (99%, CI 97.6-99.2%) |
| refund_worker_failing | 34 | 825/825 (100%, CI 99.5-100%) | 814/825 (99%, CI 97.6-99.3%) |
| seat_assign_timeout | 46 | 681/681 (100%, CI 99.4-100%) | 664/681 (98%, CI 96-98.4%) |
| seat_sync_corruption | 42 | 1322/1322 (100%, CI 99.7-100%) | 1270/1322 (96%, CI 94.9-97%) |
| ticket_render_bug | 40 | 1913/1913 (100%, CI 99.8-100%) | 1863/1913 (97%, CI 96.6-98%) |

## Where it falls short (2 of 200 scenarios)

| scenario | faults | affected | whole | escalated | still open | false closures |
|---|---|---|---|---|---|---|
| u067 | queue_overflow_drop | 1 | 0 | 1 | 0 | 0 |
| u155 | queue_overflow_drop | 1 | 0 | 1 | 0 | 0 |