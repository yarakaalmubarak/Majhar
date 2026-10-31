# Testing

How the test data is produced, how the agent is evaluated, and what the results are. Every number below comes from a
file in `results/` and can be regenerated with `./reproduce.sh`. Each report records the code fingerprint that produced it.

## 1. Test data

Real payment failures with their true outcome are not publicly available, and real customer data cannot be used. The data
therefore comes from a simulated ticket shop (`majhar/sim.py`). The simulator knows the true outcome of every order; the
agent never sees it, and it is used only for grading.

**Customers.** Each minute a number of customers arrive (the rate is set per scenario, with ±30% variation). 60% use Arabic
and 40% English. 85% go for the best seats first, which creates seat conflicts. Each order goes through the real steps:
hold a seat for 10 minutes, pay 1 to 4 minutes later, get a seat, receive a confirmation, receive an e-ticket. Prices:
VIP 450, Standard 300, Economy 150 SAR.

**What the agent can read:** the payment ledger, order database, inventory, queues, error counters, logs and recent
configuration changes. **What it cannot read:** which failures are active, and whether each customer is really whole.

**Nine failure types**, alone or combined:

| Failure | What the customer experiences | How visible it is |
|---|---|---|
| Payment webhook drops orders | Charged, no order exists | Silent: only the ledger and orders disagree |
| Seat assignment times out | Charged, no seat | Errors in the logs |
| Seat holds expire too early | Seat given to someone else after paying | Warnings |
| Same seat sold twice | Two customers, one seat | Conflict counter |
| Confirmation worker stopped | Booked, no ticket sent | Queue grows |
| Queue drops messages under load | Booked, no ticket (only during rushes) | Silent below the alert threshold |
| Refund worker failing | Refund promised, never arrives | Slow warnings |
| New ticket template breaks Arabic tickets | Arabic customers get no ticket | Errors per minute |
| Sync job damages ticket records | Ticket would fail at the gate | Silent: checksum drift only |

**Scenarios** come from a seeded generator (`majhar/generate.py`). For each scenario it chooses 0 to 4 failures
(0: 10%, 1: 30%, 2: 30%, 3: 20%, 4: 10%), a load of 6, 12, 25, 40 or 60 customers per minute, and 15, 60 or 200 seats per
section. The same seed always produces the same scenarios.

**Run protocol.** 5 minutes of normal sales, then the failures start, then 40 minutes of business while the agent works,
then 5 minutes to settle. A simulated support lead answers approval requests after 3 minutes.

## 2. Keeping the test set unseen

| Set | Generator seed | Used for |
|---|---|---|
| Development | 1 (40 scenarios) | Finding bugs while building |
| First held-out | 9090 (200) | First frozen run. It found a bug, so it became a development set |
| Final held-out | 5150 (200) | Generated and run only after the code was frozen. The headline numbers come from here |

A fix made after seeing a held-out result keeps the old behaviour available as an option, must not make an earlier result
worse (`check.py`), and is judged on a fresh set.

## 3. Metrics

All metrics are computed from the simulator's true outcomes, never from what the agent reports.

| Metric | Meaning |
|---|---|
| Customers fixed | Affected customers who end with a seat, a delivered ticket and a valid record, or a full refund |
| Solved with no person | Tickets resolved without any approval or human action |
| Closed wrongly | Tickets closed while the customer still had a problem |
| Rule breaks | Refunds above the limit paid without approval |
| Cause stopped | Scenarios where no failure is still affecting customers at the end |

Intervals are 95% Wilson intervals.

## 4. Results on the final held-out set (seed 5150, 200 scenarios)

12 scenarios with no failure, 54 with one, 134 with several; 5,725 affected customers.

| Measure | Result |
|---|---|
| Customers fixed | 5,725 / 5,725 (100%, CI 99.9–100%) |
| Solved with no person | 5,482 / 5,725 (96%) |
| Closed wrongly | 0 |
| Rule breaks | 0 |
| Median time to resolution | 2 simulated minutes |
| Cause stopped by the end | 197 / 200 |

**Benchmark against simpler approaches** (same scenarios):

| Approach | Customers fixed | Cause stopped | Money refunded | Sales kept |
|---|---|---|---|---|
| Majhar | 100% (5,725 / 5,725) | 197 / 200 | 149,100 SAR | 1,773,750 SAR |
| Auto-refund bot: refunds every affected customer, never looks for the cause | 76% (39,611 / 52,113) | 42 / 200 | 14,036,250 SAR | 0 |
| No automation: customers wait for support | 0% (0 / 38,577) | 42 / 200 | 0 | 0 |

**Changed requirements** (same scenarios):

| Refund rule | Customers fixed | Solved with no person | Closed wrongly |
|---|---|---|---|
| Normal: up to 300 SAR automatic | 100% | 96% | 0 |
| Strict: every refund needs approval | 100% | 90% | 0 |
| Relaxed: up to 1,000 SAR automatic | 100% | 98% | 0 |
| The support lead declines every request | 18%; the rest are handed to people | – | 0 |

Reports: `results/support.md`, `support_limit0.md`, `support_limit1000.md`, `support_deny.md`.

These large runs use the rule-based version of the workflow, so they can be reproduced exactly without an API key. They
set the bar the AI agent has to meet.

## 5. Testing the AI agent

- **Automated tests** (`tests/test_ai_support.py`, `tests/test_llm_backends.py`) use a scripted stand-in model to check the
  agent loop, tools, guardrails, approvals, re-planning, the fallback playbook, rate limits and provider errors. Weak
  stand-ins are included on purpose: one that never plans, one that tries to hand everything to people, one that stops early.
- **Scale** (`scale_test.py`, `results/scale.md`): from 10 to 1,069 affected customers the agent used the same 3 AI calls,
  because it plans once per problem. Measured with the scripted stand-in.
- **Live tests with the real model** run from the Testing page of the app. Each run generates new scenarios from a random
  seed, records AI calls, tokens, which problems the AI planned and which went to the fallback, and saves the run to
  `results/live_runs.md`.
- **Live demo controls**: any of 7 failures (or two at once) can be started during a rush without telling the agent, and the
  refund rule can be changed while it works. Every menu option is tested end to end (`tests/test_demo_controls.py`).

## 6. What testing found

The full history is in `results/support_iteration_log.md`.

1. Two approvals on one incident overwrote each other, so some customers waited indefinitely.
2. A fix that kept failing was retried forever. Three failed attempts now hand the ticket to a person.
3. One check read simulator state a real agent could never see. It was removed, and a test now forbids it.
4. Found on held-out data: a customer affected again after their ticket was closed went unnoticed. Tickets now reopen.
   On the fresh set, wrongly closed tickets went from 1 to 0.
5. The first live tests with a real model showed tickets re-sent too early. Each tool now tells the AI what happens next.
6. The first full test with the real model (Groq free tier) fixed only 8% of customers under strict rules, because the
   provider's rate limits stopped the AI mid-test. Calls are now about three times smaller, per-minute limits are waited out,
   and a labelled fallback playbook covers outages.
7. With the real model, some problems were handed to people without a real reason. The AI is now reminded of unplanned
   problems and must try a plan before handing customers over, and each hand-over records its reason.
8. Groq rejected a tool call that did not match the schema, which looked like an outage. The connection now retries and the
   agent tells the model which values are allowed.

## 7. Limitations

- The shop and its failures are simulated. The results describe the simulation, not a production system.
- Held-out scenarios are new combinations of the nine known failure types, not new kinds of failure.
- The support lead who approves refunds is simulated in the automated runs.
- Free model tiers are rate limited, so live runs with the real model are small and slow.
