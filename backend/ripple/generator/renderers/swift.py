"""Pure renderer for SWIFT correspondent banking payloads adhering to Rule R5.

Produces native payloads conforming to Ripple SWIFT adapter v1 spec.
Zero randomness, zero clock reads.
"""

from typing import Any

from ripple.generator.plan import TxnPlan
from ripple.generator.timeutil import iso_z


def _format_swift_account(acc: Any) -> dict[str, str]:
    """Format account reference into dictionary with either 'iban' or 'number'."""
    clean = str(acc).strip()
    if len(clean) >= 15 and clean[:2].isalpha() and clean[2:4].isdigit():
        return {"iban": clean}
    return {"number": clean}


def render_swift_payload(plan: TxnPlan, stage_idx: int) -> dict[str, Any]:
    """Purely render a SWIFT wire stage into native JSON payload dict."""
    stage = plan.stages[stage_idx]
    odata = stage.outcome_data
    attrs = plan.attrs

    raw_name = stage.name.lower()
    event_time = stage.event_time

    if raw_name in {"instruction", "payment"}:
        event_type = "payment"
    elif raw_name in {"status", "actc", "acsp", "acsc", "accc", "rjct", "pdng"}:
        event_type = "status"
    elif raw_name in {"recall", "recall_request"}:
        event_type = "recall_request"
    elif raw_name == "return":
        event_type = "return"
    else:
        event_type = "status"

    payload: dict[str, Any] = {
        "event_type": event_type,
        "settlement_amount": {
            "amount": str(plan.amount),
            "currency": plan.currency,
        },
        "value_date": attrs.get("value_date", event_time.strftime("%Y-%m-%d")),
        "debtor": {
            "account": _format_swift_account(attrs["debtor_account"]),
            "country": attrs.get("debtor_country", "GB"),
            "name": attrs.get("debtor_name", "Synthetic Debtor"),
        },
        "creditor": {
            "account": _format_swift_account(attrs["creditor_account"]),
            "country": attrs.get("creditor_country", "US"),
            "name": attrs.get("creditor_name", "Synthetic Creditor"),
        },
        "debtor_agent": {
            "bic": attrs.get("debtor_agent_bic", attrs.get("sender_bic", "ZRPAUS33XXX")),
            "received_at": iso_z(event_time),
        },
        "creditor_agent": {
            "bic": attrs.get("creditor_agent_bic", attrs.get("receiver_bic", "ZRPBUS33XXX")),
            "received_at": iso_z(event_time),
        },
    }

    # Reference identification
    if attrs.get("uetr"):
        payload["uetr"] = attrs["uetr"]
    if attrs.get("sender_reference"):
        payload["sender_reference"] = attrs["sender_reference"]

    init_time = attrs.get("instruction_time", plan.stages[0].event_time)
    payload["creation_datetime"] = iso_z(init_time)

    # Cross-currency instructed amount & exchange rate
    if attrs.get("instructed_amount") and attrs.get("instructed_currency"):
        payload["instructed_amount"] = {
            "amount": str(attrs["instructed_amount"]),
            "currency": attrs["instructed_currency"],
        }
    if attrs.get("exchange_rate"):
        payload["exchange_rate"] = str(attrs["exchange_rate"])

    if attrs.get("charge_bearer"):
        payload["charge_bearer"] = attrs["charge_bearer"]

    # Intermediary correspondent hops
    if odata.get("intermediaries"):
        payload["intermediaries"] = odata["intermediaries"]

    # Status coordinates
    if event_type == "status":
        status_code = odata.get("status") or (
            raw_name.upper()
            if raw_name.upper() in {"ACTC", "ACSP", "ACSC", "ACCC", "RJCT", "PDNG"}
            else "ACSP"
        )
        payload["status"] = status_code
        payload["status_timestamp"] = iso_z(event_time)
        if odata.get("status_reason_code"):
            payload["status_reason_code"] = odata["status_reason_code"]

    # Purpose code
    if attrs.get("purpose_code"):
        payload["purpose_code"] = attrs["purpose_code"]

    # Recall request
    if event_type == "recall_request":
        payload["recall_reason"] = odata.get("recall_reason", "FRAD")
        payload["recall_requested_at"] = iso_z(event_time)

    # Return
    if event_type == "return":
        payload["return_reason"] = odata.get("return_reason", "AC04")
        payload["returned_at"] = iso_z(event_time)

    # Malformation mutation quirk (if expected_error is flagged)
    if odata.get("malform_mutation"):
        mut = odata["malform_mutation"]
        if mut == "drop_reference":
            payload.pop("uetr", None)
            payload.pop("sender_reference", None)
        elif mut == "drop_settlement_amount":
            payload.pop("settlement_amount", None)
        elif mut == "bad_bic":
            payload["debtor_agent"]["bic"] = "INVALIDBIC"
        elif mut == "float_amount":
            payload["settlement_amount"]["amount"] = 999.888

    return payload
