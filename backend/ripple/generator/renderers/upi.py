"""Pure renderer for UPI instant payment payloads adhering to Rule R5.

Produces native payloads conforming to Ripple UPI adapter v1 spec.
Zero randomness, zero clock reads.
"""

from typing import Any

from ripple.generator.plan import TxnPlan
from ripple.generator.timeutil import iso_z, render_naive_zone


def render_upi_payload(plan: TxnPlan, stage_idx: int) -> dict[str, Any]:
    """Purely render a UPI transaction stage into native JSON payload dict."""
    stage = plan.stages[stage_idx]
    odata = stage.outcome_data
    attrs = plan.attrs

    # Timing: check naive timestamp quirk
    event_time = stage.event_time
    if odata.get("use_naive_timestamp"):
        tz_name = attrs.get("payer_tz", "Asia/Kolkata")
        time_str = render_naive_zone(event_time, tz_name)
    else:
        time_str = iso_z(event_time)

    raw_status = odata.get("status", "SUCCESS")
    txn_type = attrs.get("txn_type", "PAY")

    # Base payload
    payload: dict[str, Any] = {
        "txn_id": plan.plan_id,
        "txn_type": txn_type,
        "status": raw_status,
        "amount": str(plan.amount),
        "currency": "INR",
        "initiated_at": time_str,
    }

    if raw_status == "SUCCESS":
        payload["completed_at"] = time_str
    elif raw_status == "PENDING" and odata.get("completed_at"):
        payload["completed_at"] = iso_z(odata["completed_at"])

    # Collect request timestamps
    if txn_type == "COLLECT" and attrs.get("collect_requested_at"):
        payload["collect_requested_at"] = iso_z(attrs["collect_requested_at"])

    # Payer
    payer: dict[str, Any] = {"name": attrs.get("payer_name", "Synthetic Actor")}
    if attrs.get("payer_vpa"):
        payer["vpa"] = attrs["payer_vpa"]
    if attrs.get("payer_account"):
        payer["account"] = {
            "ifsc": attrs.get("payer_ifsc", "RPLX0000001"),
            "number": attrs["payer_account"],
        }
    if attrs.get("payer_phone"):
        payer["phone"] = attrs["payer_phone"]
    payload["payer"] = payer

    # Payee
    payee: dict[str, Any] = {"name": attrs.get("payee_name", "Synthetic Actor")}
    if attrs.get("payee_vpa"):
        payee["vpa"] = attrs["payee_vpa"]
    if attrs.get("payee_account"):
        payee["account"] = {
            "ifsc": attrs.get("payee_ifsc", "RPLX0000002"),
            "number": attrs["payee_account"],
        }
    if attrs.get("payee_phone"):
        payee["phone"] = attrs["payee_phone"]
    if attrs.get("payee_mcc"):
        payee["merchant"] = {"mcc": attrs["payee_mcc"]}
    payload["payee"] = payee

    # Optional metadata
    if attrs.get("rrn"):
        payload["rrn"] = attrs["rrn"]
    if odata.get("response_code"):
        payload["response_code"] = odata["response_code"]
    if attrs.get("channel"):
        payload["channel"] = attrs["channel"]
    if attrs.get("remarks"):
        payload["remarks"] = attrs["remarks"]
    if attrs.get("device_id"):
        payload["device_id"] = attrs["device_id"]
    if attrs.get("ip_address"):
        payload["ip_address"] = attrs["ip_address"]
    if attrs.get("purpose_code"):
        payload["purpose_code"] = attrs["purpose_code"]

    # Malformation mutation quirk (if expected_error is flagged)
    if odata.get("malform_mutation"):
        mut = odata["malform_mutation"]
        if mut == "drop_txn_id":
            payload.pop("txn_id", None)
        elif mut == "drop_amount":
            payload.pop("amount", None)
        elif mut == "float_amount":
            payload["amount"] = 12.345  # Float is strictly prohibited by UPI adapter
        elif mut == "bad_currency":
            payload["currency"] = "USD"  # UPI adapter rejects non-INR
        elif mut == "bad_status":
            payload["status"] = "INVALID_STATUS"

    return payload
