"""Pure renderer for Closed-Loop Wallet payloads adhering to Rule R5.

Produces native payloads conforming to Ripple Wallet adapter v1 spec.
Zero randomness, zero clock reads.
"""

from typing import Any

from ripple.generator.plan import TxnPlan
from ripple.generator.timeutil import iso_z


def render_wallet_payload(plan: TxnPlan, stage_idx: int) -> dict[str, Any]:
    """Purely render a Wallet transaction stage into native JSON payload dict."""
    stage = plan.stages[stage_idx]
    odata = stage.outcome_data
    attrs = plan.attrs

    raw_event_type = stage.name.lower()
    event_time = stage.event_time
    created_dt = attrs.get("created_at", plan.stages[0].event_time)

    payload: dict[str, Any] = {
        "event_type": raw_event_type,
        "txn_id": plan.plan_id,
        "amount": str(plan.amount),
        "currency": plan.currency,
        "created_at": iso_z(created_dt),
    }

    # Non-dispute/reversal events require status
    if raw_event_type not in {"dispute", "reversal"}:
        status = odata.get("status", "COMPLETED")
        payload["status"] = status
        if status == "COMPLETED":
            payload["completed_at"] = iso_z(event_time)

    # Rail interactions: funding and withdrawal
    if raw_event_type in {"funding", "withdrawal"}:
        payload["account"] = {"account_id": attrs["wallet_account_id"]}
        payload["instrument"] = {
            "instrument_id": attrs["instrument_id"],
            "instrument_type": attrs.get("instrument_type", "bank"),
        }
        if raw_event_type == "funding" and attrs.get("provisional_credit") is not None:
            payload["provisional_credit"] = attrs["provisional_credit"]

    # P2P Transfers
    elif raw_event_type == "p2p":
        payload["flow"] = attrs.get("flow", "send")
        payload["sender"] = {
            "account_id": attrs["sender_wallet_id"],
            "email": attrs.get("sender_email"),
            "phone": attrs.get("sender_phone"),
        }
        payload["receiver"] = {
            "account_id": attrs["receiver_wallet_id"],
            "email": attrs.get("receiver_email"),
            "phone": attrs.get("receiver_phone"),
        }
        payload["payment_type"] = attrs.get("payment_type", "friends_family")
        if payload["flow"] == "request":
            payload["requested_at"] = iso_z(created_dt)
            if odata.get("approved_at"):
                payload["approved_at"] = iso_z(odata["approved_at"])

    # Merchant Payments
    elif raw_event_type == "merchant_payment":
        payload["sender"] = {"account_id": attrs["sender_wallet_id"]}
        payload["receiver"] = {
            "account_id": attrs["merchant_wallet_id"],
            "merchant_id": attrs.get("merchant_id", "mid_wallet_01"),
            "mcc": attrs.get("mcc", "5411"),
        }
        payload["buyer_protection"] = True

    # Refunds
    elif raw_event_type == "refund":
        payload["parent_txn_id"] = attrs.get("parent_txn_id", f"orig_{plan.plan_id}")
        payload["sender"] = {"account_id": attrs["merchant_wallet_id"]}
        payload["receiver"] = {"account_id": attrs["sender_wallet_id"]}

    # Disputes
    elif raw_event_type == "dispute":
        payload["case_id"] = odata.get("case_id", f"case_{plan.plan_id}")
        orig_type = attrs.get("original_event_type", "merchant_payment")
        payload["original_event_type"] = orig_type
        payload["original_completed_at"] = iso_z(created_dt)
        payload["dispute_status"] = odata.get("dispute_status", "opened")
        payload["dispute_reason"] = odata.get("dispute_reason", "item_not_received")
        payload["opened_at"] = iso_z(event_time)
        if odata.get("resolved_at"):
            payload["resolved_at"] = iso_z(odata["resolved_at"])

        if attrs.get("merchant_wallet_id"):
            payload["sender"] = {"account_id": attrs["sender_wallet_id"]}
            payload["receiver"] = {
                "account_id": attrs["merchant_wallet_id"],
                "merchant_id": attrs.get("merchant_id", "mid_wallet_01"),
                "mcc": attrs.get("mcc", "5411"),
            }
        else:
            payload["sender"] = {"account_id": attrs.get("sender_wallet_id", "wal_s01")}
            payload["receiver"] = {"account_id": attrs.get("receiver_wallet_id", "wal_r01")}

    # Reversals
    elif raw_event_type == "reversal":
        orig_type = attrs.get("original_event_type", "funding")
        payload["original_event_type"] = orig_type
        payload["original_completed_at"] = iso_z(created_dt)
        payload["reversal_reason"] = odata.get("reversal_reason", "funding_failed")
        payload["reversed_at"] = iso_z(event_time)

        if orig_type == "funding":
            payload["account"] = {
                "account_id": attrs.get(
                    "wallet_account_id", attrs.get("sender_wallet_id", "wal_01")
                )
            }
            payload["instrument"] = {
                "instrument_id": attrs.get("instrument_id", "ins_01"),
                "instrument_type": attrs.get("instrument_type", "bank"),
            }
        else:
            payload["sender"] = {"account_id": attrs.get("sender_wallet_id", "wal_s01")}
            payload["receiver"] = {"account_id": attrs.get("receiver_wallet_id", "wal_r01")}

    # Optional protection & reversal windows
    if attrs.get("buyer_protection") is not None:
        payload["buyer_protection"] = attrs["buyer_protection"]
    if attrs.get("reversal_window_days") is not None:
        payload["reversal_window_days"] = attrs["reversal_window_days"]

    # Telemetry
    if attrs.get("device_id"):
        payload["device_id"] = attrs["device_id"]
    if attrs.get("ip_address"):
        payload["ip_address"] = attrs["ip_address"]
    if attrs.get("channel"):
        payload["channel"] = attrs["channel"]

    # Malformation mutation quirk (if expected_error is flagged)
    if odata.get("malform_mutation"):
        mut = odata["malform_mutation"]
        if mut == "drop_txn_id":
            payload.pop("txn_id", None)
        elif mut == "float_amount":
            payload["amount"] = 88.7766  # Floats prohibited
        elif mut == "drop_event_type":
            payload.pop("event_type", None)
        elif mut == "bad_currency":
            payload["currency"] = "BTC"  # Fiat currencies only

    return payload
