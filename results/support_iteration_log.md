# Support Desk: what testing found, what changed, and the proof

Rule I followed: freeze the code, record its fingerprint, run, report what comes out. A fix after freezing must be switchable
(so the old behaviour stays reproducible), must not make any previous result worse, and is judged on a set nobody has looked at.

## Before freezing: throwaway set (generator seed 1, 40 scenarios)
| bug found | what happened | fix | guarded by test |
|---|---|---|---|
| Two approvals overwrote each other | an incident needing a template rollback **and** a record repair put both requests on one ticket; the second replaced the first, and 20 customers waited forever | platform-wide changes are tracked once at desk level and proposed on any free ticket | `test_two_platform_changes_on_the_same_incident_do_not_overwrite_each_other` |
| A repair loop that never escalates | once the ticket-template fault went quiet, its error counter read 0, so the agent kept re-sending failing tickets every minute | a failed resend is itself evidence of the template fault, and **3 failed tries in a row hand the ticket to a human** | `test_a_fix_that_keeps_failing_is_handed_over_not_retried_forever` |
| Reading hidden state | one check looked at the simulator's hidden fault list, which a real agent could never see | replaced with the observable mismatch counter | `test_agent_never_reads_hidden_fault_flags` |

## v1 frozen (code `07022362cd24`), held-out set 9090 (200 scenarios): `results/support_v1/`
Default policy: 4,329 / 4,331 customers whole, 95% of tickets with no human, **0 false closures**.
But with a support lead who **declines every proposal**: **3 tickets closed while the customer was not whole.**

Cause: those customers were fixed correctly and their tickets closed. Minutes later the record-corruption fault damaged their tickets again. Because the
repair had been declined it kept happening, and **a closed ticket was never reopened**. With a normal support lead the approved repair fixes every
record at once, so the bug is invisible. Only the "requirements change" test exposed it.

## v2 (code `4f2ec1bf6306`): reopen a closed ticket when the same customer is hurt again
Switchable: `SupportDesk(reopen=False)` / `eval_support.py --no-reopen` reproduces v1 exactly (verified: identical report on set 9090).

| check | v1 | v2 |
|---|---|---|
| set 9090, lead declines everything (motivated the fix, **not independent**) | 3 false closures | **0** |
| **fresh set 5150** (never inspected before freezing v2), lead declines everything | 1 false closure | **0** |
| fresh set 5150, default policy | n/a | 5,725 / 5,725 whole, 96% no human, 0 false closures |
| all 147 tests | pass | pass |

Set 9090 is now a development set (`results/support_dev*.md`). The headline numbers in the README come from set 5150.

## v3 (code `70bdf1980480`): the AI agent
Added `majhar/ai_support.py`: Claude now works the tickets with tools, inside the same guardrails. The rule-based workflow and every number
above are unchanged (the fingerprint changed only because the new file is now part of it; `reproduce.sh` gives identical reports).
The AI agent is tested with a scripted stand-in model; it has not yet been scored against the real model.

## v4: the AI as incident commander (built after the first live runs with a real model)
First live runs with a real model (Groq, gpt-oss-120b) showed two things: the per-ticket AI was impatient (it re-sent tickets
before they were ready, and the safety rule handed a solvable ticket to a person), and one model conversation per ticket
would not survive a real rush. v4 changes the design: the AI plans once per incident; automation applies the plan to every
ticket; "nothing to send yet" is no longer counted as a failure; a booking of several tickets gets one refund approval.
* Scale (`results/scale.md`): 10 → 1,069 affected tickets, all solved, **3 AI calls in every case** (scripted stand-in model).
* The rule-based held-out results are unchanged (identical reports).


## v5: first full test with the real AI model (Groq free tier, openai/gpt-oss-120b)

**What happened.** A live test of 5 fresh crises under strict rules: 122 of 1,484 affected customers fixed (8%), 0 false
closures, 0 rule breaks. Crisis 1 used 21 AI calls; crises 4 and 5 used 2 and 0. The free tier allows 8,000 tokens per minute
and 200,000 per day per model. Every call re-sent the full conversation (about 1,600 tokens of instructions and tool
definitions plus a growing history), so the provider started refusing calls; the agent then handed every ticket of the
affected problems to people, and the faults kept hurting new customers.

**What changed.**
- Calls are smaller: only the last 3 tool rounds are re-sent, tool results are cut to 1,500 characters, the brief is compact,
  the model is asked to call several tools per reply and (for gpt-oss) to reason briefly. Session budget 14 -> 10 calls.
- Per-minute limits: the connection waits as long as the provider asks (up to 65 s), then retries.
- Daily limit: fails at once with a clear message and how to continue (another model has its own quota).
- AI service unavailable: the company's standard playbook (the rule-based desk) takes over those problems, marked as backup
  (never credited to the AI), and the AI is tried again for new problems after an 8-minute cool-down. When the AI itself
  cannot produce a plan, tickets still go to people (that is the AI's judgment, not an outage).
- Every run reports AI calls, tokens, problems planned by the AI and problems on the backup.

**Tests added.** outage -> playbook fixes the customer; outage without backup -> people; cool-down then AI again; calls stay
small however long the session; daily limit fails at once; per-minute limit waits and tokens are counted; wait-time parsing.

## v6: fewer hand-overs to people when the AI falls short

**What we saw.** In runs with the real model, many tickets were handed to a person. The reasons were not decisions that need a
person: the model ended a session without planning every problem, a plan stalled twice, or the model escalated on its first look.

**What changed.**
- If the model stops before every problem has a plan, it is reminded (up to twice) which problems are left and what the usual
  steps are.
- If it still has no plan, or its plan stops working after two revisions, the standard playbook takes over that problem,
  marked as backup (not credited to the AI). A person is involved only when the playbook itself fails or a person declines.
- The model must try a plan before handing customers to people; if it insists a second time, the hand-over goes ahead.
- Every hand-over and backup now records its reason, shown under each crisis on the Testing page.

**Check.** 5 fresh crises with deliberately weak stand-in models: an AI that never plans now ends with 99/99 customers whole and
0 hand-overs (all covered by the backup); an AI that tries to hand everything over ends with 4 hand-overs, only where it insisted.

## v7: a mistaken tool call no longer looks like an outage

**What we saw.** A live run with the real model (Groq) sent one problem to the backup with: `HTTP 400 Tool call validation
failed: parameters for tool set_plan did not match schema (missing 'steps', unknown 'problem')`. The model had filled a tool
call wrongly, and the provider rejected the whole answer before it reached the agent, so it was treated as the AI being down.

**What changed.** On that error the connection retries with the same tools, but with the allowed values written into the
descriptions instead of enforced by the schema. The call then reaches the agent, whose own checks reply with what is wrong
(the list of valid problems, causes or steps), and the model corrects itself on the next call.

**Tests added.** A fake provider that rejects strict schemas exactly like Groq does: the ticket is resolved by the AI, with no
backup and no error.
