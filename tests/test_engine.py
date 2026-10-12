"""The entitlement maths. These are the answers the product is judged on, so they
are pinned here and run without an API key."""
import pytest

from app.policy.engine import (
    evaluate_cancellation,
    evaluate_plan_capability,
    evaluate_service_credit,
    resolve_sla,
)
from app.store import snapshot_now


def test_snapshot_is_read_from_the_workbook_readme():
    now = snapshot_now()
    assert now.strftime("%Y-%m-%d %H:%M") == "2026-09-19 14:00"
    assert now.strftime("%A") == "Saturday"  # business-hours targets depend on this


# --------------------------------------------------------------- cancellation
def test_halcyon_cancels_booked_order_free_despite_being_outside_the_window():
    """Contract beats SOP."""
    d = evaluate_cancellation("SHP-5001")
    assert d.outcome == "cancellable_no_fee"
    assert d.facts["fee_inr"] == 0
    assert d.facts["default_fee_inr"] == 350          # what the SOP alone would say
    assert d.facts["minutes_since_booking"] == 150     # well outside the 20-min window
    assert d.facts["fee_waived_by_agreement"] is True
    assert any(c.tier == 1 for c in d.citations)       # the agreement is cited


def test_halcyon_case_flags_the_contradicting_past_ticket():
    d = evaluate_cancellation("SHP-5001")
    claims = " ".join(c["claim"] for c in d.conflicts)
    assert "350" in claims, "CASE-240's incorrect past guidance should be surfaced"


def test_halcyon_case_warns_that_booked_may_be_stale():
    """KI-322 makes TransVelo's BOOKED status unreliable inside the pickup window."""
    d = evaluate_cancellation("SHP-5001")
    assert d.requires_human is True
    assert "KI-322" in " ".join(d.caveats)


def test_brightmoor_pays_the_sop_fee_because_its_contract_waives_nothing():
    d = evaluate_cancellation("SHP-6001")
    assert d.outcome == "cancellable_with_fee"
    assert d.facts["fee_inr"] == 350


def test_inside_the_free_window_is_free_for_an_account_with_no_agreement():
    d = evaluate_cancellation("SHP-7001")
    assert d.outcome == "cancellable_no_fee"
    assert d.facts["minutes_since_booking"] == 12


def test_picked_up_order_is_not_cancellable():
    d = evaluate_cancellation("SHP-5002")
    assert d.outcome == "not_cancellable_use_rto"
    assert "fee_inr" not in d.facts  # no fee is computed; this is not a cancellation
    assert d.suggested_action["action_type"] == "create_followup_task"


def test_delivered_order_is_not_cancellable():
    d = evaluate_cancellation("SHP-8001")
    assert d.outcome == "not_cancellable"


# ------------------------------------------------------------ service credits
def test_brightmoor_overdue_pickup_pays_the_contract_amount_and_needs_a_manager():
    d = evaluate_service_credit(order_id="SHP-6002")
    assert d.outcome == "eligible"
    assert d.facts["credit_amount_inr"] == 2000     # contract fixed, not 15% of 3,400
    assert d.facts["threshold_hours"] == 3
    assert d.facts["delay_hours_past_window_end"] == 6.0
    assert d.requires_human is True                  # 2,000 is over the SOP's 1,500 limit


def test_same_delay_gives_opposite_answers_under_different_contracts():
    """Two hours late, carrier at fault — the answer is contract-specific."""
    halcyon = evaluate_service_credit(
        account_id="CUS-101", delay_hours=2, carrier_fault=True,
        customer_fault=False, shipment_fee_inr=3600,
    )
    brightmoor = evaluate_service_credit(
        account_id="CUS-102", delay_hours=2, carrier_fault=True, customer_fault=False,
    )
    assert halcyon.outcome == "eligible"
    assert halcyon.facts["credit_amount_inr"] == 540     # min(600, 15% of 3,600)
    assert brightmoor.outcome == "not_eligible"          # 3h contract threshold not met


def test_unknown_fault_never_promises_a_credit():
    d = evaluate_service_credit(account_id="CUS-103", delay_hours=5)
    assert d.outcome == "undetermined"
    assert d.confidence == "low"
    assert d.requires_human is True


def test_a_real_order_with_unknown_fault_is_undetermined_too():
    d = evaluate_service_credit(order_id="SHP-8002")
    assert d.outcome == "undetermined"
    assert d.facts["delay_hours_past_window_end"] == 4.0


def test_customer_fault_blocks_a_credit():
    d = evaluate_service_credit(
        account_id="CUS-101", delay_hours=9, carrier_fault=True,
        customer_fault=True, shipment_fee_inr=3600,
    )
    assert d.outcome == "not_eligible"


def test_sop_credit_is_capped_by_the_flat_amount():
    d = evaluate_service_credit(
        account_id="CUS-104", delay_hours=6, carrier_fault=True,
        customer_fault=False, shipment_fee_inr=200_000,   # 15% would be 30,000
    )
    assert d.facts["credit_amount_inr"] == 600           # capped by the flat amount
    assert d.requires_human is False                      # 600 is under the 1,500 limit


def test_missing_shipment_fee_yields_a_range_not_a_number():
    d = evaluate_service_credit(
        account_id="CUS-101", delay_hours=2, carrier_fault=True, customer_fault=False,
    )
    assert d.outcome == "eligible_amount_unknown"
    assert d.confidence == "medium"


# ------------------------------------------------------------------------ SLA
def test_agreement_sla_beats_plan_default_and_is_breached():
    d = resolve_sla("CUS-101", "P1", ticket_id="CASE-301")
    assert d.outcome == "breached"
    assert d.facts["target"] == "10 minutes, 24x7"        # not the Enterprise 20 min
    assert "agreement" in d.facts["target_source"].lower()
    assert d.facts["overdue_by"] == "10 min"


def test_plan_default_applies_without_an_agreement():
    d = resolve_sla("CUS-104", "P1", ticket_id="CASE-305")
    assert d.facts["target"] == "20 minutes, 24x7"
    assert d.facts["target_source"] == "plan default"
    assert d.outcome == "breached"


def test_business_hours_targets_stop_running_over_the_weekend():
    """Opened Friday 16:30; the snapshot is Saturday. Only Friday's 90 minutes count."""
    d = resolve_sla("CUS-102", "P2", ticket_id="CASE-302")
    assert d.outcome == "at_risk"                         # not breached, despite ~21h wall clock
    assert d.facts["elapsed_minutes"] == 90
    assert d.facts["due_at"].startswith("2026-09-21 09:30")   # rolls to Monday


def test_p1_always_requires_human_escalation():
    for account, ticket in (("CUS-101", "CASE-301"), ("CUS-104", "CASE-305")):
        assert resolve_sla(account, "P1", ticket_id=ticket).requires_human is True


def test_unknown_severity_is_rejected():
    with pytest.raises(ValueError):
        resolve_sla("CUS-101", "P0")


# ------------------------------------------------------------- plan capability
def test_bulk_upload_documented_limit_and_known_issue_are_both_reported():
    d = evaluate_plan_capability("CUS-102")
    assert d.outcome == "available"
    assert d.facts["documented_max_rows"] == 8000
    assert d.facts["known_issue"] == "KI-314"
    assert "2,500" in " ".join(d.caveats)
    # And the earlier wrong answer to this customer is surfaced.
    assert any("2,500 rows" in c["claim"] for c in d.conflicts)


def test_starter_plan_has_no_bulk_upload():
    d = evaluate_plan_capability("CUS-103")
    assert d.outcome == "not_available"
