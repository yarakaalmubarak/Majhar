# Majhar (مجهر)

An AI agent that handles what happens after a customer pays and something goes wrong: it finds the affected orders, fixes the
cause, and makes sure every customer ends up with their ticket or a refund.

Built for the HUMAIN Future AI Builders Hackathon, Challenge 3: autonomous enterprise workflows.

## Why

I once bought concert tickets on an official ticketing website during a busy sale. The payment went through, the tickets never
arrived, and the refund took about three months. Failures
like this are often silent: the payment goes through, the order is never recorded, and support only finds out when customers
complain. With events like Expo 2030 and the FIFA World Cup 2034 ahead, one bug during a ticket rush can affect thousands of
people at once.

## How it works

1. **Detect.** Majhar compares payments taken with orders recorded and opens a ticket for each affected order, before the
   customer complains.
2. **Diagnose.** Tickets are grouped by problem. For each problem, a language model reads the logs and records and names the
   root cause with evidence.
3. **Fix.** The model applies the platform fix once (for example, restarting the payment webhook and replaying lost payments),
   chooses a plan for the affected customers, and writes the customer message in Arabic and English.
4. **Apply.** Code applies the plan to every ticket: restore the booking, move the customer to an equal seat, or refund.
5. **Approve.** Refunds over 300 SAR and platform-wide changes wait for a person to approve them.
6. **Verify.** A ticket closes only when the order records show the customer is whole. It reopens if they are affected again.

The model plans once per problem, not once per customer, so a rush of 1,069 affected customers took the same 3 model calls as
10 (scale test with a scripted stand-in model, `results/scale.md`). If the model provider is unavailable, a rule-based playbook
takes over and is reported separately.

**Stack:** Python 3.10+ (standard library only), a small web UI in HTML/JavaScript, pytest. The model runs on Groq
(`openai/gpt-oss-120b` by default); Gemini, Ollama, OpenAI and Claude are also supported.

## Run it

You need Python 3 and a free Groq API key from https://console.groq.com.

```bash
bash start.sh
```

The script asks for the key (it is not saved), checks the connection and opens http://127.0.0.1:8000.
Step-by-step instructions are in [START_HERE.txt](START_HERE.txt).

To run without the script:

```bash
export GROQ_API_KEY="your-key"
python3 app.py
```

The page has two tabs:

- **Live demo:** a customer's phone and the support desk. Pick a failure, start a rush of 60 customers, buy tickets as a
  customer, and watch the agent find and fix the problem. The system log shows every step.
- **Testing and results:** run new test scenarios against the live agent and see the results.

## Testing

The data comes from a simulated ticket shop with nine failure types (lost payments, stopped confirmations, broken Arabic
e-tickets, double-sold seats, and others). The simulator knows what really happened to each customer. The agent never sees
this; it is only used for grading. Details: [docs/TESTING.md](docs/TESTING.md).

Results on 200 held-out scenarios (generated after the code was frozen), using the rule-based version of the workflow so they
can be reproduced without an API key:

| Measure | Result |
|---|---|
| Affected customers who ended with their ticket or a refund | 5,725 / 5,725 (100%) |
| Tickets resolved with no person involved | 96% |
| Tickets closed while the customer still had a problem | 0 |
| Refunds over the limit paid without approval | 0 |
| Every refund requires approval (stricter rules) | 100% resolved, 90% with no person |

**Benchmark against simpler approaches** (same 200 scenarios):

| Approach | Customers fixed | Cause stopped by the end | Money refunded | Sales kept |
|---|---|---|---|---|
| **Majhar** | **100%** (5,725 / 5,725) | **197 of 200** | 149,100 SAR | **1,773,750 SAR** |
| Auto-refund bot (refunds everyone, never looks for the cause) | 76% (39,611 / 52,113) | 42 of 200 | 14,036,250 SAR | 0 |
| No automation (customers wait for support) | 0% | 42 of 200 | 0 | 0 |

The auto-refund bot refunds about 94 times more money and never stops the cause, so far more customers are affected.
Majhar keeps the sale whenever it can and fixes the problem at its source.

Runs with the real model are recorded from the Testing tab in `results/live_runs.md`.

Testing changed the design several times; the full history is in `results/support_iteration_log.md`. The main example: the
first test with the real model resolved only 8% of customers because the free tier's rate limit ran out. Model calls are now
about three times smaller, rate limits are waited out, and a labelled fallback covers outages.

## Limitations

- The shop and its failures are simulated, so the results describe the simulation, not a production system.
- Held-out scenarios are new combinations of known failure types, not new kinds of failure.
- The person approving refunds is simulated in the automated tests.
- Free model tiers are slow and rate limited; a full test run with the real model takes several minutes.

## Project structure

```
app.py                  web server for the demo and the testing page
start.sh                one-step start
majhar/ai_support.py    the AI agent: instructions, tools, planning, fallback
majhar/support.py       tickets, refund policy, approvals, rule-based workflow
majhar/llm_backends.py  connections to Groq, Gemini, Ollama, OpenAI and Anthropic
majhar/sim.py           simulated ticket platform and its nine failure types
majhar/shop.py          storefront and demo crowd
majhar/tools.py         actions available to the agent
web/                    user interface
eval_support.py         held-out evaluation and benchmark
scale_test.py           AI calls as the number of affected customers grows
check.py                regression check against the first frozen version
docs/TESTING.md         test data, method and results
tests/                  automated tests
```

## Reproduce

```bash
python3 -m pytest -q
./reproduce.sh          # regenerates every result above
```
