"""Pure renderer for Cryptocurrency blockchain payloads adhering to Rule R5.

Produces native payloads conforming to Ripple Crypto adapter v1 spec.
Zero randomness, zero clock reads.
"""

from typing import Any

from ripple.generator.plan import TxnPlan
from ripple.generator.timeutil import iso_z


def render_crypto_payload(plan: TxnPlan, stage_idx: int) -> dict[str, Any]:
    """Purely render a Crypto transaction stage into native JSON payload dict."""
    stage = plan.stages[stage_idx]
    odata = stage.outcome_data
    attrs = plan.attrs

    stage_name = stage.name.lower()
    chain = attrs.get("chain", "ethereum").lower()

    # Map stage name / outcome status to adapter enum
    if stage_name == "pending":
        raw_status = "pending"
    elif stage_name in {"included", "finalized"}:
        raw_status = "included"
    elif stage_name == "reverted":
        raw_status = "failed"
    elif stage_name in {"dropped", "replaced", "reorged"}:
        raw_status = stage_name
    else:
        raw_status = odata.get("status", "included")

    payload: dict[str, Any] = {
        "chain": chain,
        "tx_hash": attrs.get("tx_hash", f"0x{plan.plan_id.replace('-', '')}"),
        "status": raw_status,
        "from_address": attrs["from_address"],
        "to_address": attrs["to_address"],
    }

    token_meta = attrs.get("token_metadata")
    nft_meta = attrs.get("nft_metadata")

    if nft_meta is not None:
        payload["asset"] = {
            "type": "nft",
            "contract_address": nft_meta["contract_address"],
            "token_id": str(nft_meta["token_id"]),
            "symbol": "NFT",
        }
        payload["quantity"] = "1"
        if nft_meta.get("consideration_amount"):
            payload["consideration"] = {
                "amount": str(nft_meta["consideration_amount"]),
                "symbol": str(nft_meta.get("consideration_asset", "ETH")),
            }
    elif token_meta is not None:
        payload["asset"] = {
            "type": "token",
            "symbol": token_meta["symbol"],
            "decimals": int(token_meta.get("decimals", 6)),
            "contract_address": token_meta.get(
                "contract_address", "0x0000000000000000000000000000000000000001"
            ),
        }
        payload["amount"] = str(plan.amount)
    else:
        # Native asset (omitted or explicit native descriptor)
        payload["amount"] = str(plan.amount)

    # Status-specific timestamps and coordinates
    if raw_status == "pending":
        payload["first_seen_at"] = iso_z(stage.event_time)
    elif raw_status == "included":
        confs = int(odata.get("confirmations", odata.get("confirmation_count", 1)))
        payload["confirmations"] = confs
        payload["block_number"] = int(odata.get("block_number", 15000000))
        payload["block_time"] = iso_z(stage.event_time)
        if stage_name == "finalized" or odata.get("finalized", False):
            payload["finalized"] = True
            payload["finalized_at"] = iso_z(stage.event_time)

    elif raw_status == "failed":
        payload["execution_status"] = "reverted"
        payload["block_number"] = int(odata.get("block_number", 15000000))
        payload["block_time"] = iso_z(stage.event_time)

    elif raw_status == "replaced":
        if odata.get("replaced_by"):
            payload["replaced_by"] = odata["replaced_by"]

    # Fee (native asset)
    if odata.get("fee_amount"):
        payload["fee"] = {
            "amount": str(odata["fee_amount"]),
        }

    # USD normalized value
    if odata.get("usd_value"):
        payload["usd_value"] = str(odata["usd_value"])

    # Threat intel labels
    if attrs.get("from_label"):
        cat = attrs["from_label"]
        payload["from_label"] = {"entity": f"{cat}_provider", "category": cat}
    if attrs.get("to_label"):
        cat = attrs["to_label"]
        payload["to_label"] = {"entity": f"{cat}_provider", "category": cat}

    return payload
