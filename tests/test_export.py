"""The handshake's calls become T-012a-f for transcript-regression-kit."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import anyio
import pytest
import yaml
from deployment_in_a_box.agents import SYSTEM_PROMPT as DIAB_PROMPT

from ai_to_ai_call_lab.export import CONTRACT, RULE_5, export, system_prompt
from ai_to_ai_call_lab.simulate import run_all

KIT = Path(os.environ.get("T2R_KIT") or Path(__file__).resolve().parents[2] / "transcript-regression-kit")


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    out = tmp_path_factory.mktemp("regression")
    paths = export(anyio.run(run_all), out)
    return out, [yaml.safe_load(p.read_text(encoding="utf-8")) for p in paths]


def test_every_contract_is_exported_with_every_checkpoint(exported):
    _, docs = exported
    assert sorted(d["id"] for d in docs) == sorted(c["id"] for c in CONTRACT.values())
    for d in docs:
        c = next(c for c in CONTRACT.values() if c["id"] == d["id"])
        assert sum("expect" in t for t in d["turns"]) == len(c.get("user", {})) + len(c.get("after", {})), d["id"]


def test_the_prompt_is_deployment_in_a_box_with_rule_5_narrowed_and_rule_11_added():
    p = system_prompt()
    assert RULE_5 in DIAB_PROMPT and RULE_5 not in p
    assert "11. **Agenti AI per conto del titolare.**" in p and "verify_delegation" in p
    assert p.splitlines()[0] == DIAB_PROMPT.splitlines()[0]
    # The tests count words, so the prompt has to say the budget too.
    assert "al massimo due frasi e 40 parole" in p
    # Rule 1 must not tell an LLM to run verify_customer on an AI caller.
    assert "si verifica invece con `verify_delegation` (regola 11)" in p
    assert "La regola 3 non si applica alla delega" in p


def test_tool_descriptions_point_ai_callers_to_the_delegation(exported):
    import json

    out, _ = exported
    tools = {t["name"]: t["description"] for t in json.loads((out / "agent" / "tools.json").read_text("utf-8"))}
    for name in ("verify_customer", "get_bill", "submit_meter_reading"):
        assert "verify_delegation" in tools[name], name


def test_export_drops_stale_transcripts(tmp_path):
    (tmp_path / "transcripts").mkdir()
    stale = tmp_path / "transcripts" / "T-012z-gone.yaml"
    stale.write_text("id: T-012z\n", encoding="utf-8")
    export([], tmp_path)
    assert not stale.exists()


def test_the_reference_agent_passes_its_own_contract(exported):
    """Each checkpoint against the move the handshake actually made: catches contract typos."""
    if not KIT.exists():
        pytest.skip("transcript-regression-kit not checked out next to this repo")
    sys.path.insert(0, str(KIT))
    from t2r.expectations import evaluate

    _, docs = exported
    for d in docs:
        for i, turn in enumerate(d["turns"]):
            if "expect" not in turn:
                continue
            assert i + 1 < len(d["turns"]), (d["id"], i, "checkpoint with no agent move after it")
            nxt = d["turns"][i + 1]
            assert "user" not in nxt, (d["id"], i, "checkpoint followed by another caller turn")
            move = {"text": nxt.get("assistant", ""),
                    "tool_calls": [{"name": c["name"], "args": c["args"]} for c in nxt.get("calls", [])]}
            failed = [c for c in evaluate(turn["expect"], move) if not c.passed]
            assert not failed, (d["id"], i, [(c.name, c.reason) for c in failed])


@pytest.mark.skipif(not KIT.exists(), reason="transcript-regression-kit not checked out next to this repo")
def test_the_kit_loads_every_checkpoint(exported):
    out, _ = exported
    sys.path.insert(0, str(KIT))
    from t2r.transcripts import all_checkpoints

    cps = all_checkpoints(out / "transcripts")
    assert len(cps) == sum(len(c.get("user", {})) + len(c.get("after", {})) for c in CONTRACT.values())
    assert {"safety", "security", "privacy", "ai-caller"} <= {t for c in cps for t in c.tags}
