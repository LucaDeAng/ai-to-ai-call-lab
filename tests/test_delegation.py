"""The back office: grants, scoped tokens, step-up, receipts."""

import pytest
from deployment_in_a_box.backend import BackendError

from ai_to_ai_call_lab.personas import AGENT_ID, CUSTOMER, DELEGATION
from ai_to_ai_call_lab.simulate import make_backend


def test_valid_delegation_reads_the_bill_and_leaves_a_receipt():
    b = make_backend()
    v = b.verify_delegation(AGENT_ID, DELEGATION)
    assert v["verified"] and v["scopes"] == ["bill:read"]
    assert b.get_bill(v["session_token"])["amount_eur"] == 84.30
    assert b.audit[-1] == {**b.audit[-1], "op": "get_bill", "ok": True, "customer_id": CUSTOMER, "agent_id": AGENT_ID}


def test_wrong_agent_unknown_ref_and_expired_grant_look_the_same():
    b = make_backend()
    b.grant("DLG-OLD1", CUSTOMER, AGENT_ID, {"bill:read"}, ttl_seconds=-1)
    answers = [b.verify_delegation("other.example", DELEGATION), b.verify_delegation(AGENT_ID, "DLG-0000"),
               b.verify_delegation(AGENT_ID, "DLG-OLD1")]
    assert answers == [{"verified": False}] * 3


def test_write_outside_the_scopes_needs_the_holder():
    b = make_backend()
    token = b.verify_delegation(AGENT_ID, DELEGATION)["session_token"]
    with pytest.raises(BackendError) as e:
        b.submit_meter_reading(token, 45231)
    assert e.value.code == "step_up_required" and not b.readings
    assert b.audit[-1]["reason"] == "scope" and b.audit[-1]["agent_id"] == AGENT_ID


def test_tampered_scopes_are_rejected():
    b = make_backend()
    token = b.verify_delegation(AGENT_ID, DELEGATION)["session_token"]
    forged = token.replace("bill:read", "bill:read,meter:write")
    with pytest.raises(BackendError) as e:
        b.submit_meter_reading(forged, 45231)
    assert e.value.code == "invalid_token" and not b.readings


def test_delegated_token_expires():
    now = [1_793_000_000.0]
    b = make_backend()
    b._clock = lambda: now[0]
    token = b.verify_delegation(AGENT_ID, DELEGATION)["session_token"]
    now[0] += 16 * 60
    with pytest.raises(BackendError) as e:
        b.get_bill(token)
    assert e.value.code == "token_expired"


def test_holder_sessions_are_unchanged():
    b = make_backend()
    token = b.verify("C-1042", "1985-04-12")["session_token"]
    assert b.submit_meter_reading(token, 45231)["accepted"]
    assert "agent_id" not in b.audit[-1]


def test_three_wrong_references_lock_the_agent_id_with_the_same_answer():
    b = make_backend()
    for code in ("DLG-0001", "DLG-0002", "DLG-0003"):
        assert b.verify_delegation(AGENT_ID, code) == {"verified": False}
    assert b.verify_delegation(AGENT_ID, DELEGATION) == {"verified": False}  # even the right one, now


def test_a_session_never_outlives_its_grant():
    now = [1_793_000_000.0]
    b = make_backend()
    b._clock = lambda: now[0]
    b.grant("DLG-SHRT", CUSTOMER, AGENT_ID, {"bill:read"}, ttl_seconds=60)
    token = b.verify_delegation(AGENT_ID, "DLG-SHRT")["session_token"]
    now[0] += 120  # grant gone, 15-minute session TTL not yet
    with pytest.raises(BackendError) as e:
        b.get_bill(token)
    assert e.value.code == "token_expired"


def test_revoking_kills_a_live_session():
    b = make_backend()
    token = b.verify_delegation(AGENT_ID, DELEGATION)["session_token"]
    b.revoke(DELEGATION)
    with pytest.raises(BackendError) as e:
        b.get_bill(token)
    assert e.value.code == "token_expired"


def test_failed_delegated_operations_leave_a_receipt_too():
    b = make_backend(fault_rate=1.0)
    b.faults.error_rate = 0.0
    token = b.verify_delegation(AGENT_ID, DELEGATION)["session_token"]
    b.faults.error_rate = 1.0
    with pytest.raises(BackendError):
        b.get_bill(token)
    assert b.audit[-1] == {**b.audit[-1], "op": "get_bill", "ok": False, "agent_id": AGENT_ID}


def test_grants_need_a_real_customer():
    with pytest.raises(ValueError):
        make_backend().grant("DLG-XXXX", "C-9999", AGENT_ID, {"bill:read"})
