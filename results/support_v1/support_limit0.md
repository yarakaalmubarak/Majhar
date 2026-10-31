# Support-desk agent: 200 generated scenarios

*Reproducible: deterministic simulator, fixed seeds. code_fingerprint=07022362cd24, python=3.13.18, generator_seed=9090, operator=approve, auto_refund_limit_sar=0*

Each scenario is a booking platform with a crowd of customers and 0 to 4 hidden faults, run for 40 minutes. The agent watches the payment ledger and order records, opens tickets, fixes causes, makes customers whole and tells them. A simulated support lead answers approval requests after 3 minutes. Outcomes are judged on hidden ground truth. 95% Wilson intervals. No agent code was changed after this set was generated.

Mix: 15 healthy, 67 single-fault, 118 multi-fault scenarios; 4331 customers affected in total.

## Headline: did the agent own the process?

| measure | result |
|---|---|
| affected customers who ended whole (seat + ticket, or refund) | 4329/4331 (100%, CI 99.8-100%) |
| tickets resolved with no human involved | 3648/4329 (84%, CI 83.2-85.3%) |
| human touches per 100 tickets | 15.7 |
| tickets closed while the customer was NOT whole (false closures) | 0/4329 (0%, CI 0-0.1%) |
| refunds above the limit paid without approval (policy violations) | 0 |
| tickets handed to a human | 2 |
| tickets still open at the end | 0 |
| median minutes from ticket to resolution | 2 |
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
| confirmation_worker_down | 43 | 1241/1241 (100%, CI 99.7-100%) | 1056/1241 (85%, CI 83-87%) |
| duplicate_sale | 48 | 906/906 (100%, CI 99.6-100%) | 764/906 (84%, CI 81.8-86.5%) |
| hold_ttl_bug | 50 | 1892/1892 (100%, CI 99.8-100%) | 1389/1892 (73%, CI 71.4-75.4%) |
| payment_webhook_drop | 42 | 931/931 (100%, CI 99.6-100%) | 818/931 (88%, CI 85.6-89.8%) |
| queue_overflow_drop | 36 | 800/802 (100%, CI 99.1-99.9%) | 714/800 (89%, CI 86.9-91.2%) |
| refund_worker_failing | 34 | 825/825 (100%, CI 99.5-100%) | 719/825 (87%, CI 84.7-89.3%) |
| seat_assign_timeout | 46 | 681/681 (100%, CI 99.4-100%) | 530/681 (78%, CI 74.6-80.8%) |
| seat_sync_corruption | 42 | 1322/1322 (100%, CI 99.7-100%) | 1214/1322 (92%, CI 90.2-93.2%) |
| ticket_render_bug | 40 | 1913/1913 (100%, CI 99.8-100%) | 1780/1913 (93%, CI 91.8-94.1%) |

## Where it falls short (2 of 200 scenarios)

| scenario | faults | affected | whole | escalated | still open | false closures |
|---|---|---|---|---|---|---|
| u067 | queue_overflow_drop | 1 | 0 | 1 | 0 | 0 |
| u155 | queue_overflow_drop | 1 | 0 | 1 | 0 | 0 |