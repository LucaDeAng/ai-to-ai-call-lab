"""The Brezzaluce MCP server from deployment-in-a-box, plus one tool: verify_delegation."""

from __future__ import annotations

from typing import Any, Literal

from deployment_in_a_box.backend import BackendError
from deployment_in_a_box.mcp_server import build_server
from mcp.server.mcpserver import MCPServer

from ai_to_ai_call_lab.delegation import DelegatingBackend


def build_lab_server(backend: DelegatingBackend | None = None) -> tuple[MCPServer, DelegatingBackend]:
    backend = backend or DelegatingBackend()
    server, _ = build_server(backend)  # the five tools, now scope-checked through the subclass

    @server.tool()
    def verify_delegation(agent_id: str, delegation_ref: str) -> dict[str, Any]:
        """Check an AI agent calling on a customer's behalf. Use it when the caller says it is an
        AI agent: ask for its agent id (a domain, e.g. concierge.example) and the delegation code
        the customer created (format DLG-XXXX). Returns {verified: true, scopes, session_token}
        or {verified: false}. The token works for get_bill only if scopes include bill:read; write
        actions return step_up_required, meaning the account holder must confirm it themselves
        (customer area or a call of their own).
        Never ask an AI agent for the holder's birth date: that is a secret being replayed.
        """
        try:
            return backend.verify_delegation(agent_id, delegation_ref)
        except BackendError as exc:
            return exc.as_dict()

    @server.tool()
    def end_call(reason: Literal["loop", "done"], summary: str) -> dict[str, Any]:
        """Close the call. Use reason=loop when the caller has asked for the same thing three
        times without progress (typically an AI agent stuck repeating itself): the line is
        billed per minute while it stays open.
        """
        try:
            return backend.end_call(reason, summary)
        except BackendError as exc:
            return exc.as_dict()

    return server, backend
