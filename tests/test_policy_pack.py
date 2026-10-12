"""The policy pack must never drift from the documents it claims to encode."""
from app.actions import MANAGER_APPROVAL_ABOVE_INR
from app.policy.pack import agreement, policy, product, sop, verify_pack


def test_every_quote_is_present_in_its_source_pdf():
    problems = verify_pack()
    assert problems == [], "Policy pack no longer matches the source PDFs:\n" + "\n".join(problems)


def test_precedence_order_matches_the_support_policy():
    assert policy()["source_precedence"]["order"] == [
        "agreement", "policy", "product_doc", "historical",
    ]


def test_sop_numbers():
    booked = sop()["cancellation"]["by_status"]["BOOKED"]
    assert booked["free_window_minutes_from_booking"] == 20
    assert booked["fee_after_window_inr"] == 350
    credit = sop()["service_credit"]
    assert credit["default_threshold_hours_past_window_end"] == 1.5
    assert credit["amount"]["flat_inr"] == 600
    assert credit["amount"]["percent_of_shipment_fee"] == 15
    assert sop()["approval"]["manager_approval_above_inr"] == 1500


def test_action_gate_reads_the_same_approval_limit_as_the_engine():
    assert MANAGER_APPROVAL_ABOVE_INR == sop()["approval"]["manager_approval_above_inr"]


def test_agreement_overrides_are_loaded():
    halcyon = agreement("CUS-101")
    assert halcyon["cancellation"]["waives_booked_cancellation_fee"] is True
    assert halcyon["service_credit"]["monthly_aggregate_cap_inr"] == 8000
    assert halcyon["first_response_targets"]["matrix"]["P1"]["amount"] == 10

    brightmoor = agreement("CUS-102")
    assert brightmoor["cancellation"]["waives_booked_cancellation_fee"] is False
    assert brightmoor["service_credit"]["threshold_hours_past_window_end"] == 3
    assert brightmoor["service_credit"]["fixed_amount_inr"] == 2000

    # Accounts with no signed agreement must fall through cleanly.
    assert agreement("CUS-103") is None
    assert agreement("CUS-104") is None


def test_known_issues_present_and_typed():
    kinds = {ki["id"]: ki["kind"] for ki in product()["known_issues"]}
    assert kinds == {"KI-314": "bulk_upload_degradation", "KI-322": "status_lag"}
