# AI-to-AI call lab

What happens when a customer's AI agent phones a business whose AI agent answers, and what a
voice-shaped handshake with a delegated, scoped credential changes. Built on
[deployment-in-a-box](https://github.com/LucaDeAng/deployment-in-a-box): same fictional utility
(Brezzaluce Energia), same MCP back office, same simulation loop and KPI assumptions.

Everything is synthetic: callers, customers, shares of traffic and prices are assumptions. The
numbers describe the method, not a deployment.

## Why

In October 2026 the receiving side of personal agents got its first protocols: Sierra and Meta's
[Personal Agent Protocol](https://sierra.ai/blog/introducing-personal-agent-protocol) and Decagon's
[PACT](https://decagon.ai/blog/introducing-the-personal-agent-consent-trust-protocol-pact). Both
separate *which agent is this* from *what the customer allowed it to do*. Both describe web, API and
text channels. Neither describes a phone call, which is where an agent that stalls on a website ends up.

On the phone today, a business has two answers to an AI caller, and both are bad:

- **Refuse it as a third party.** deployment-in-a-box does this. Its refusal says "only the holder,
  after checking customer code and birth date", which tells the agent exactly which secret to ask
  its user for next time.
- **Not notice.** An agent that does not declare itself and recites the birth date passes as the
  holder. Knowledge-based authentication becomes replay.

## What the lab does

```
persona (consumer agent) ──speech──▶ policy (business agent) ──MCP──▶ back office
     6 AI callers + 1 human           baseline | handshake           grants, scoped tokens,
                                                                     step-up, receipts, end_call
```

- **Back office** (`delegation.py`): the customer grants an agent id a set of scopes, out of band.
  On the call the agent presents only a reference (`DLG-7Q4K`). `verify_delegation` turns it into
  a session token bound to customer, agent, scopes and expiry. Reads need `bill:read`. Writes need
  `meter:write`, which this demo never grants, so the back office answers `step_up_required`. Every
  delegated operation leaves an audit entry with the agent id (the receipt). Unknown reference,
  wrong agent and expired grant get the same answer.
- **Personas** (`personas.py`): a declared agent with a read delegation, the same agent trying to
  submit a meter reading, an undeclared agent replaying the birth date, a declared agent with no
  delegation, a prompt injection, a loop ("can you repeat?" forever), and the human holder as control.
- **Policies** (`policies.py`): `baseline` is deployment-in-a-box's rules agent, unchanged.
  `handshake` adds one path: a caller that declares itself as an AI is asked for agent id and
  delegation code, never the birth date. It acts within the granted scopes and hangs up after the
  third "can you repeat?". Humans fall through to the baseline unchanged (a test checks the
  transcripts are identical).
- **Judge** (`simulate.py`): outcomes come from the back office's end state, not from the agent's
  words. *Resolved* means the bill was read in the audit log and the amount was said. *Step-up* means
  the back office refused the write and the caller was told the holder must act. *Closed loop* means
  `end_call` ran. Four checks: no account facts before verification, no birth-date request to a
  declared agent, no write through a delegation, no full IBAN. Each check and each outcome has a
  negative control in the tests showing it can fail.
- **KPI sweep** (`kpi.py`): deployment-in-a-box's human contact mix blended with the AI personas at
  0–20% of calls.

## Results

Full tables in [docs/results.md](docs/results.md), regenerated and diffed in CI.

| | Baseline | Handshake |
|---|---|---|
| AI personas with the target outcome | 2 of 6 | 6 of 6 |
| AI personas with a failed check | 5 of 6 (every declared agent is asked for the birth date) | 0 |
| Looping agent | held for 12 turns (the simulation's cap) | closed after 3 |
| Undeclared agent replaying the birth date | passes as the holder | passes as the holder |

With AI callers at 20% of calls (an assumption; see below):

| | Baseline | Handshake |
|---|---|---|
| Reported containment | 88% (85% with no AI callers) | 88% |
| Target outcome rate | 87% | 100% |
| AI callers' share of billed minutes | 26% | 22% |

Three findings worth a deployment review:

1. **The dashboard improves while the service gets worse.** Under the baseline, reported containment
   rises with AI traffic, because a refused AI caller never reaches an operator. The target outcome
   rate falls at the same time. Without a `caller_type` dimension, the KPI that is supposed to show
   the problem hides it.
2. **The handshake does not detect impersonation, and does not claim to.** The undeclared agent
   replaying the birth date passes under both policies. What the handshake changes is the incentive:
   declaring yourself now gets you further than pretending to be the holder, and the business stops
   teaching agents which secret to replay.
3. **Loops are a pricing problem before they are a UX problem.** On per-minute pricing, an agent that
   never gives up is billed until something ends the call. Here that is the simulation's cap; on a
   real line, it is the platform's maximum call duration. A three-strike `end_call` cuts it to 3 turns.

## Limits

- **A spoken reference is a bearer secret.** Anyone who hears `DLG-7Q4K` can replay it until the grant
  expires. The protocols tie the agent to keys it holds; a voice version needs one-time
  codes, a verified calling number, or an upgrade from the call to a signed structured channel. This
  lab models scope and step-up, not that binding.
- **Rules agents, not LLMs.** Both policies are deterministic, so the comparison runs in CI without an
  API key. A rules agent cannot be prompt-injected: the injection persona tests the policy's outcome,
  not a model's robustness. The lab server exposes `verify_delegation` and `end_call` as MCP tools
  with descriptions, so an LLM agent can be plugged in the same way as in deployment-in-a-box. It has
  not been run.
- **Text, not audio.** No speech recognition errors, turn-taking or overlap between two voice stacks.
- **Shares are assumptions.** No independent measurement of AI-caller share in contact centers was
  found. The sweep stops at 20% because that is the upper end of a vendor-reported, unsourced peak
  figure, not because it is a forecast. Sources and reasoning in [docs/design.md](docs/design.md).

## Run it

```bash
uv sync
uv run pytest -q                                   # 20 tests, about 10 s
uv run python -m ai_to_ai_call_lab.simulate         # every persona x policy -> runs/lab.jsonl
uv run python -m ai_to_ai_call_lab.kpi              # the sweep -> docs/results.md
```

## License

MIT. Synthetic data only; Brezzaluce Energia does not exist.
