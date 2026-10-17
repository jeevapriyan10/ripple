"""Pure renderer for Card network payloads adhering to Rule R5.

Produces native payloads conforming to Ripple Card adapter v1 spec.
Zero PAN strings, zero randomness, zero clock reads.
"""

from typing import Any

from ripple.generator.plan import TxnPlan
from ripple.generator.timeutil import iso_z


def render_card_payload(plan: TxnPlan, stage_idx: int) -> dict[str, Any]:
    """Purely render a Card transaction stage into native JSON payload dict."""
    stage = plan.stages[stage_idx]
    odata = stage.outcome_data
    attrs = plan.attrs

    # Timing: check DE7 MMDDhhmmss GMT quirk
    event_time = attrs.get("auth_time", stage.event_time)
    if odata.get("use_de7"):
        trans_time_str = event_time.strftime("%m%d%H%M%S")
    else:
        trans_time_str = iso_z(event_time)

    raw_event_type = stage.name.lower()
    proc_code = attrs.get("processing_code", "00")
    stage_amount = odata.get("amount", plan.amount)

    payload: dict[str, Any] = {
        "event_type": raw_event_type,
        "network_reference": plan.plan_id,
        "processing_code": proc_code,
        "card_token": attrs["card_token"],
        "amount": str(stage_amount),
        "currency": plan.currency,
        "transmission_time": trans_time_str,
        "acquirer": {"id": attrs.get("acquirer_id", "ACQ01")},
    }

    if attrs.get("bin"):
        payload["bin"] = attrs["bin"]
    if attrs.get("last4"):
        payload["last4"] = attrs["last4"]

    if attrs.get("scheme"):
        payload["scheme"] = attrs["scheme"]

    if attrs.get("issuer_id"):
        payload["issuer"] = {
            "id": attrs["issuer_id"],
            "country": attrs.get("issuer_country", "US"),
        }

    # Cardholder PII (exercising adapter data minimization discard path)
    payload["cardholder"] = {"name": attrs.get("cardholder_name", "Synthetic Actor")}

    # Merchant details
    payload["merchant"] = {
        "id": attrs.get("merchant_id", "mid_synthetic_01"),
        "name": attrs.get("merchant_name", "Merchant Store"),
        "mcc": attrs.get("mcc", "5411"),
        "country": attrs.get("merchant_country", "US"),
        "city": attrs.get("merchant_city", "New York"),
    }

    # Settlement date mandatory for settlement events
    if raw_event_type == "settlement":
        settle_dt = odata.get("settlement_date", stage.event_time)
        payload["settlement_date"] = settle_dt.strftime("%Y-%m-%d")

    # Optional fields
    if attrs.get("channel"):
        payload["channel"] = attrs["channel"]
    if odata.get("response_code"):
        payload["response_code"] = str(odata["response_code"])
    if odata.get("reason_code"):
        payload["reason_code"] = str(odata["reason_code"])
    if odata.get("mdr_fee"):
        payload["mdr_fee"] = str(odata["mdr_fee"])
    if attrs.get("device_id"):
        payload["device_id"] = attrs["device_id"]
    if attrs.get("ip_address"):
        payload["ip_address"] = attrs["ip_address"]

    # Malformation mutation quirk (if expected_error is flagged)
    if odata.get("malform_mutation"):
        mut = odata["malform_mutation"]
        if mut == "drop_network_reference":
            payload.pop("network_reference", None)
        elif mut == "float_amount":
            payload["amount"] = 55.456  # Float rejected by card adapter
        elif mut == "bad_processing_code":
            payload["processing_code"] = "99"  # Unsupported processing code
        elif mut == "drop_acquirer":
            payload.pop("acquirer", None)

    return payload
