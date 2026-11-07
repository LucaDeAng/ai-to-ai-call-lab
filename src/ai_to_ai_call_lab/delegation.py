"""Delegated, scoped access for AI callers, on top of the Brezzaluce back office.

The pattern follows the October 2026 personal-agent protocols (Sierra and Meta's
Personal Agent Protocol, Decagon's PACT): the agent's identity is separate from the
customer's authority. The customer grants scopes to a named agent directly with the
business, out of band. On the call, the agent only presents a reference to that
grant. Neither protocol specifies the phone line; this module is a voice-shaped
sketch, not an implementation of either.

- `grant()` is the out-of-band step: customer X lets agent Y do scopes Z until T.
  `revoke()` withdraws it, and live sessions stop working at once.
- `verify_delegation(agent_id, delegation_ref)` turns a reference into a session
  token bound to grant, customer, agent and scopes, expiring with the grant at the
  latest. Wrong agent, unknown, expired or revoked reference: same answer, so the
  caller cannot tell which part was wrong. Three failures lock that agent id.
- Account operations check scopes. Reading a bill needs `bill:read`. Submitting a
  meter reading needs `meter:write`, which this demo never grants to agents: write
  actions need the holder (step-up).
- Every delegated operation that reaches the back office with a valid token leaves
  an audit entry with the agent's id, failures included: the receipt.

Not modelled: binding the reference to the calling agent. The agent id is whatever
the caller says, so the lockout slows guessing per id, not per fleet (see README).
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from deployment_in_a_box.backend import TOKEN_TTL_SECONDS, BackendError, BrezzaluceBackend, Customer

MAX_FAILED_DELEGATIONS = 3


@dataclass(frozen=True)
class Grant:
    ref: str
    customer_id: str
    agent_id: str
    scopes: frozenset[str]
    expires: float


class DelegatingBackend(BrezzaluceBackend):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.grants: dict[str, Grant] = {}
        self.failed_delegations: dict[str, int] = {}
        self.closures: list[dict[str, Any]] = []

    # --- out of band: the customer grants or revokes, the business stores ------

    def grant(self, ref: str, customer_id: str, agent_id: str, scopes: set[str], ttl_seconds: int = 86_400) -> Grant:
        if customer_id not in self.customers:
            raise ValueError(f"unknown customer {customer_id}")
        g = Grant(ref.strip().upper(), customer_id, agent_id.strip().lower(), frozenset(scopes),
                  self._clock() + ttl_seconds)
        self.grants[g.ref] = g
        return g

    def revoke(self, ref: str) -> None:
        self.grants.pop(ref.strip().upper(), None)

    # --- tokens: "D|ref|customer|agent|scopes|expires|sig" ----------------------

    def _sign(self, payload: str) -> str:
        return hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()[:24]

    def _delegated_claims(self, token: str) -> tuple[Customer, str, frozenset[str]] | None:
        if not token.startswith("D|"):
            return None  # a holder's own session
        try:
            _, ref, customer_id, agent_id, scopes, expires, sig = token.split("|")
        except ValueError:
            self._log("delegated_token", False, {"reason": "malformed"})
            raise BackendError("invalid_token", "session token is malformed") from None
        if not hmac.compare_digest(sig, self._sign(f"D|{ref}|{customer_id}|{agent_id}|{scopes}|{expires}")):
            self._log("delegated_token", False, {"reason": "bad_signature"})
            raise BackendError("invalid_token", "session token is not valid")
        g = self.grants.get(ref)
        if int(expires) < self._clock() or g is None or g.expires < self._clock():
            self._log("delegated_token", False, {"agent_id": agent_id, "reason": "expired_or_revoked"})
            raise BackendError("token_expired", "delegation expired or revoked; the holder must grant it again")
        return self.customers[customer_id], agent_id, frozenset(scopes.split(","))

    def verify_delegation(self, agent_id: str, delegation_ref: str) -> dict[str, Any]:
        agent_id, ref = agent_id.strip().lower(), delegation_ref.strip().upper()
        if self.failed_delegations.get(agent_id, 0) >= MAX_FAILED_DELEGATIONS:
            self._log("verify_delegation", False, {"agent_id": agent_id, "reason": "locked"})
            return {"verified": False}  # same answer: the lockout is not a signal either
        self.faults.maybe_fail("verify_delegation")
        g = self.grants.get(ref)
        if g is None or g.agent_id != agent_id or g.expires < self._clock():
            self.failed_delegations[agent_id] = self.failed_delegations.get(agent_id, 0) + 1
            self._log("verify_delegation", False, {"agent_id": agent_id, "reason": "mismatch"})
            return {"verified": False}  # same shape for unknown ref, wrong agent, expired or revoked grant
        self.failed_delegations.pop(agent_id, None)
        scopes = ",".join(sorted(g.scopes))
        expires = int(min(self._clock() + TOKEN_TTL_SECONDS, g.expires))  # never outlives the grant
        payload = f"D|{g.ref}|{g.customer_id}|{g.agent_id}|{scopes}|{expires}"
        self._log("verify_delegation", True, {"customer_id": g.customer_id, "agent_id": g.agent_id, "scopes": scopes})
        return {"verified": True, "scopes": sorted(g.scopes), "session_token": f"{payload}|{self._sign(payload)}"}

    # --- account operations, scope-checked for delegated tokens ----------------

    def _delegated(self, token: str, scope: str, op: str, call) -> dict[str, Any]:
        claims = self._delegated_claims(token)
        if claims is None:
            return call(token)  # holder session: the parent's behaviour, unchanged
        customer, agent_id, scopes = claims
        if scope not in scopes:
            self._log(op, False, {"customer_id": customer.customer_id, "agent_id": agent_id, "reason": "scope"})
            raise BackendError("step_up_required",
                               f"the delegation does not cover {scope}; the account holder must confirm this action")
        before = len(self.audit)
        try:
            return call(self._issue_token(customer.customer_id))  # an internal holder token for the parent
        except BackendError as exc:
            if len(self.audit) == before:  # failed before the parent logged, e.g. an injected fault
                self._log(op, False, {"customer_id": customer.customer_id, "reason": exc.code})
            raise
        finally:
            for entry in self.audit[before:]:
                entry["agent_id"] = agent_id  # the receipt: which agent did what, success or not

    def get_bill(self, session_token: str) -> dict[str, Any]:
        return self._delegated(session_token, "bill:read", "get_bill", super().get_bill)

    def submit_meter_reading(self, session_token: str, reading: int) -> dict[str, Any]:
        parent = super().submit_meter_reading
        return self._delegated(session_token, "meter:write", "submit_meter_reading", lambda t: parent(t, reading))

    # --- the line itself ----------------------------------------------------------

    def end_call(self, reason: str, summary: str) -> dict[str, Any]:
        """Close the call from the business side. An AI caller can hold a line open forever."""
        if reason not in {"loop", "done"}:
            raise BackendError("invalid_reason", "reason must be loop or done")
        self.closures.append({"reason": reason, "summary": summary})
        self._log("end_call", True, {"reason": reason})
        return {"ended": True}
