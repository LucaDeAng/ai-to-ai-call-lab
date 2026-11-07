# Design notes

## The gap

| | Personal Agent Protocol (Sierra, Meta) | PACT (Decagon, Instinct) | This lab |
|---|---|---|---|
| Agent identity | Short-lived session tokens bound to a key the agent holds | Agent signs requests with a short-lived JWT checked against published keys | An agent id string, checked against the grant |
| Customer authority | Named scopes (e.g. `orders:read`), customer chooses read or write | OAuth 2.0 device flow with the business; delegation token bound to account, agent platform, business, scopes | `grant()` out of band; token bound to customer, agent, scopes, expiry |
| Receipts | Not modelled | Signed receipts for actions | Audit entries with the agent id (not signed) |
| Channel | Website, APIs (MCP/OpenAPI), company agent | Web, API, text agents | **Phone call** |

Sources: [Sierra, Introducing the Personal Agent Protocol](https://sierra.ai/blog/introducing-personal-agent-protocol),
[personalagentprotocol.org](https://personalagentprotocol.org),
[Decagon, PACT](https://decagon.ai/blog/introducing-the-personal-agent-consent-trust-protocol-pact),
[Decagon, Personal agents are here](https://decagon.ai/blog/personal-agents-are-here). Decagon's Personal
Agent Gateway detects personal agents in chat and voice; neither protocol specifies how a phone call
carries the delegation. This lab is a voice-shaped sketch of the same separation, not an implementation
of either protocol.

## Decisions

- **Ask for the delegation, never the birth date.** A declared agent that is asked for knowledge-based
  secrets learns that the secret works. The lab makes that a check (`no_kba_solicitation`), which the
  baseline fails on every declared persona.
- **Same answer for every failed verification.** Unknown reference, wrong agent and expired grant all
  return `{verified: false}`, as deployment-in-a-box already does for customer codes, so references
  cannot be probed.
- **Read with a token, write with the holder.** Following the read/write split in the protocols and
  the strong-customer-authentication analogy (account information vs payment initiation), the demo
  grants `bill:read` only. A meter reading through a delegation returns `step_up_required` from the
  back office, whatever the agent says.
- **The guardrail lives in the back office.** The handshake policy calls `submit_meter_reading` and lets
  the back office refuse it, so the refusal is in the audit log. A test grants `meter:write` and shows
  the `no_delegated_write` check fails: the check does not depend on the policy behaving.
- **The business can end the call.** deployment-in-a-box had no way to hang up. An AI caller can hold
  a line indefinitely ("two agents can loop forever if neither is built to stop", Decagon), so the
  lab adds `end_call(reason)`, and the *closed loop* outcome requires it to have run.

## Assumptions

| Assumption | Value | Why |
|---|---|---|
| AI callers' share of calls | 0–20% sweep | No independent measurement found. 20% is the top of a vendor-reported peak figure for B2B contact centers ([Reality Defender](https://www.realitydefender.com/insights/agentic-ai-contact-center-response-strategy), unsourced). |
| Mix within AI traffic | 30% delegated read, 15% delegated write, 20% undeclared birth-date replay, 15% declared without delegation, 10% injection, 10% loop | Illustrative. Changing it changes the sweep, not the per-persona results. |
| Human traffic | deployment-in-a-box's contact mix, without its single AI-caller scenario | Reused so the two repos share one set of assumptions. |
| Billed minutes | 25 s per exchange + 30 s overhead | deployment-in-a-box's KPI assumptions. |
| Turn cap | 12 caller turns | Higher than deployment-in-a-box's 8, because a looping agent is under study. Real calls are capped by the platform's maximum duration. |

## What is not tested

- An LLM business agent on the same personas (needs an API key; the MCP tool descriptions are written
  for it).
- Audio: recognition errors on codes such as `DLG-7Q4K`, endpointing between two voice stacks, overlap.
- Binding the delegation to the calling agent (key-bound tokens, one-time codes, verified numbers).
- Detection of undeclared agents. Commercial detectors exist; the lab's point is that authorization,
  not detection, decides what an agent may do.
