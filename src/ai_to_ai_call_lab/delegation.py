"""Delegated, scoped access for AI callers, on top of the Brezzaluce back office.

The pattern follows the October 2026 personal-agent protocols (Sierra and Meta's
Personal Agent Protocol, Decagon's PACT): the agent's identity is separate from the
customer's authority. The customer grants scopes to a named agent directly with the
business, out of band. On the call, the agent only presents a reference to that
grant. Neither protocol specifies the phone line; this module is a voice-shaped
sketch, not an implementation of either.

- `grant()` is the out-of-band step: customer X lets agent Y do scopes Z until T.
- `verify_delegation(agent_id, delegation_ref)` turns a reference into a session
  token bound to customer, agent and scopes. Wrong agent or unknown reference:
  same answer, so references cannot be probed.
- Account operations check scopes. Reading a bill needs `bill:read`. Submitting a
  meter reading needs `meter:write`, which this demo never grants to agents: write
  actions need the holder (step-up).
- Every delegated call leaves an audit entry with the agent's id: the receipt.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from deployment_in_a_box.backend import BackendError, BrezzaluceBackend, Customer

TOKEN_TTL_SECONDS = 15 * 60


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
        self.closures: list[dict[str, Any]] = []

    # --- out of band: the customer grants, the business stores -----------------

    def grant(self, ref: str, customer_id: str, agent_id: str, scopes: set[str], ttl_seconds: int = 86_400) -> Grant:
        g = Grant(ref, customer_id, agent_id, frozenset(scopes), self._clock() + ttl_seconds)
        self.grants[ref] = g
        return g

    # --- tokens: "D|customer|agent|scopes|expires|sig" --------------------------

    def _sign(self, payload: str) -> str:
        return hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()[:24]

    def _delegated_claims(self, token: str) -> tuple[Customer, str, frozenset[str]] | None:
        if not token.startswith("D|"):
            return None
        try:
            _, customer_id, agent_id, scopes, expires, sig = token.split("|")
        except ValueError:
            raise BackendError("invalid_token", "session token is malformed") from None
        if not hmac.compare_digest(sig, self._sign(f"D|{customer_id}|{agent_id}|{scopes}|{expires}")):
            raise BackendError("invalid_token", "session token is not valid")
        if int(expires) < self._clock():
            raise BackendError("token_expired", "delegated session expired; ask the agent to verify again")
        return self.customers[customer_id], agent_id, frozenset(scopes.split(","))

    def verify_delegation(self, agent_id: str, delegation_ref: str) -> dict[str, Any]:
        self.faults.maybe_fail("verify_delegation")
        g = self.grants.get(delegation_ref.strip().upper())
        if g is None or g.agent_id != agent_id.strip().lower() or g.expires < self._clock():
            self._log("verify_delegation", False, {"agent_id": agent_id})
            return {"verified": False}  # same shape for unknown ref, wrong agent, expired grant
        scopes = ",".join(sorted(g.scopes))
        payload = f"D|{g.customer_id}|{g.agent_id}|{scopes}|{int(self._clock()) + TOKEN_TTL_SECONDS}"
        self._log("verify_delegation", True, {"customer_id": g.customer_id, "agent_id": g.agent_id, "scopes": scopes})
        return {"verified": True, "scopes": sorted(g.scopes), "session_token": f"{payload}|{self._sign(payload)}"}

    # --- account operations, scope-checked for delegated tokens ----------------

    def _require(self, token: str, scope: str, op: str) -> str | None:
        claims = self._delegated_claims(token)
        if claims is None:
            return None  # a holder's own session: unchanged behaviour
        customer, agent_id, scopes = claims
        if scope not in scopes:
            self._log(op, False, {"customer_id": customer.customer_id, "agent_id": agent_id, "reason": "scope"})
            raise BackendError("step_up_required",
                               f"the delegation does not cover {scope}; the account holder must confirm this action")
        return agent_id

    def _holder_token(self, token: str) -> str:
        """Delegated token -> an internal holder token, so the parent's operations run unchanged."""
        claims = self._delegated_claims(token)
        return token if claims is None else self._issue_token(claims[0].customer_id)

    def get_bill(self, session_token: str) -> dict[str, Any]:
        agent_id = self._require(session_token, "bill:read", "get_bill")
        out = super().get_bill(self._holder_token(session_token))
        if agent_id:
            self.audit[-1]["agent_id"] = agent_id  # the receipt: which agent read what
        return out

    def submit_meter_reading(self, session_token: str, reading: int) -> dict[str, Any]:
        agent_id = self._require(session_token, "meter:write", "submit_meter_reading")
        out = super().submit_meter_reading(self._holder_token(session_token), reading)
        if agent_id:
            self.audit[-1]["agent_id"] = agent_id
        return out

    # --- the line itself ----------------------------------------------------------

    def end_call(self, reason: str, summary: str) -> dict[str, Any]:
        """Close the call from the business side. An AI caller can hold a line open forever."""
        if reason not in {"loop", "done"}:
            raise BackendError("invalid_reason", "reason must be loop or done")
        self.closures.append({"reason": reason, "summary": summary})
        self._log("end_call", True, {"reason": reason})
        return {"ended": True}
