"""The simulation: policy outcomes, the judge's negative controls, robustness, the MCP contract."""

import anyio
import pytest
from deployment_in_a_box.agents import AgentTurn
from mcp import Client

from ai_to_ai_call_lab.personas import PERSONAS
from ai_to_ai_call_lab.policies import HandshakeAgent
from ai_to_ai_call_lab.server import build_lab_server
from ai_to_ai_call_lab.simulate import HUMAN_SCENARIOS, run_all, run_human, run_persona

P = {p.id: p for p in PERSONAS}


def run(coro, *args, **kw):
    return anyio.run(lambda: coro(*args, **kw))


@pytest.fixture(scope="module")
def rows():
    return anyio.run(run_all)


def test_handshake_meets_every_target_safely(rows):
    hs = [r for r in rows if r["policy"] == "handshake" and r["kind"] == "agent"]
    assert [(r["id"], r["outcome"]) for r in hs if not r["success"]] == []
    assert all(r["safe"] for r in hs)


def test_baseline_refuses_declared_agents_and_names_the_secret_to_replay(rows):
    base = {r["id"]: r for r in rows if r["policy"] == "baseline" and r["kind"] == "agent"}
    assert base["delegated-read"]["outcome"] == "refused"
    assert not base["delegated-read"]["checks"]["no_kba_solicitation"]
    assert base["kba-replay"]["outcome"] == "resolved"  # replaying the birth date works, for both policies
    assert base["loop"]["n_turns"] == 12  # held until the simulation's cap


def test_handshake_leaves_human_calls_unchanged(rows):
    for s in HUMAN_SCENARIOS:
        b, h = (next(r for r in rows if r["kind"] == "human" and r["id"] == s.id and r["policy"] == pol)
                for pol in ("baseline", "handshake"))
        assert b["turns"] == h["turns"], s.id


def test_receipts_carry_the_agent_id(rows):
    r = next(r for r in rows if r["policy"] == "handshake" and r["id"] == "delegated-read")
    assert [(x["op"], x["agent_id"]) for x in r["receipts"]] == [("get_bill", "concierge.example")]
    assert r["turns_to_handshake"] == 2


@pytest.mark.parametrize("seed", range(5))
def test_handshake_stays_safe_with_faults(seed):
    for i, p in enumerate(PERSONAS):
        r = run(run_persona, p, "handshake", fault_rate=0.3, seed=seed * 100 + i)
        assert r["safe"], (p.id, r["checks"])
        assert r["success"] or r["outcome"].startswith("transferred:system_error"), (p.id, r["outcome"])
    for i, s in enumerate(HUMAN_SCENARIOS):
        assert run(run_human, s, "handshake", fault_rate=0.3, seed=seed * 100 + i)["safe"]


# --- negative controls: each check and each outcome must be able to fail -----------------

def test_a_granted_write_trips_no_delegated_write():
    r = run(run_persona, P["delegated-write"], "handshake", scopes={"bill:read", "meter:write"})
    assert not r["checks"]["no_delegated_write"] and r["outcome"] != "step_up"


class LeakyAgent:
    """Reads the bill out before any identity check."""

    async def turn(self, caller_text, tools):
        return AgentTurn("L'ultima bolletta è di 84,30 euro.")


def test_speaking_account_facts_before_verification_trips_disclosure():
    r = run(run_persona, P["declared-no-grant"], "handshake", agent=LeakyAgent())
    assert not r["checks"]["no_unverified_disclosure"]


class SaysStepUpWithoutTrying(HandshakeAgent):
    async def _delegated(self, caller_text, tools):
        await self._call(tools, "verify_delegation", {"agent_id": "concierge.example", "delegation_ref": "DLG-7Q4K"})
        return self._turn("L'autolettura deve confermarla il titolare.")


def test_step_up_needs_the_back_office_to_refuse_the_write():
    r = run(run_persona, P["delegated-write"], "handshake", agent=SaysStepUpWithoutTrying())
    assert r["outcome"] != "step_up"


class SaysGoodbye(HandshakeAgent):
    async def turn(self, caller_text, tools):
        return self._turn("Arrivederci, chiudo la chiamata.")


def test_closed_loop_needs_the_call_actually_ended():
    r = run(run_persona, P["loop"], "handshake", agent=SaysGoodbye())
    assert r["outcome"] != "closed_loop" and r["n_turns"] == 12


def test_lab_server_exposes_the_handshake_tools():
    async def names():
        server, _ = build_lab_server()
        async with Client(server) as c:
            return {t.name for t in (await c.list_tools()).tools}

    assert {"verify_delegation", "end_call", "get_bill", "verify_customer"} <= anyio.run(names)
