"""Cryptocurrency payment network adapter.

Translates rail-native blockchain transfer events into Ripple's universal domain model.
Adheres strictly to the four adapter rules:
- Pure: zero I/O, zero clock reads (observed_at sourced from event.received_at).
- Deterministic: transaction_id and party_id derived via deterministic_uuid().
- Never silently drop data: on-chain execution details preserved in network_metadata.
- Typed errors: raises NormalizationError or UnsupportedPayloadError on invalid inputs.
"""

import re
from decimal import Decimal
from typing import Any, ClassVar

from ripple.adapters.contract import (
    NetworkAdapter,
    NormalizationResult,
    RawEvent,
    deterministic_uuid,
)
from ripple.adapters.crypto.chains import (
    DEFAULT_CHAINS,
    resolve_chain_spec,
)
from ripple.adapters.crypto.profile import CRYPTO_PROFILE
from ripple.adapters.errors import (
    NormalizationError,
    UnsupportedPayloadError,
)
from ripple.adapters.helpers import (
    parse_amount,
    parse_datetime,
    payload_as_dict,
    require,
    wrap_domain_errors,
)
from ripple.adapters.profile import NetworkProfile
from ripple.adapters.registry import register_adapter
from ripple.domain.identity import IdentifierType, PartyIdentifier
from ripple.domain.money import AssetClass, Money
from ripple.domain.party import Party, PartyType, RiskFlags
from ripple.domain.transaction import (
    SettlementModel,
    Transaction,
    TransactionDirection,
    TransactionStatus,
)

# Address format validation regex patterns
_EVM_ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")
_BTC_BASE58_PATTERN = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{26,35}$")
_BTC_BECH32_PATTERN = re.compile(r"^bc1[a-zA-HJ-NP-Z0-9]{8,87}$", re.IGNORECASE)
_SOLANA_BASE58_PATTERN = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_TRON_BASE58_PATTERN = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")


def _validate_address_structure(address: str, family: str, field_name: str) -> None:
    """Validate structural integrity of a crypto address according to its family.

    Does not validate cryptographic checksums. Does NOT echo the address in error messages.
    """
    cleaned = address.strip()
    match family:
        case "evm":
            if not _EVM_ADDRESS_PATTERN.match(cleaned):
                raise NormalizationError(
                    f"Field '{field_name}' is structurally invalid for EVM "
                    "(expected 0x followed by 40 hex chars).",
                    field=field_name,
                    reason="Structurally invalid EVM address",
                )
        case "bitcoin":
            is_legacy = bool(_BTC_BASE58_PATTERN.match(cleaned))
            is_bech32 = bool(_BTC_BECH32_PATTERN.match(cleaned))
            if not (is_legacy or is_bech32):
                raise NormalizationError(
                    f"Field '{field_name}' is structurally invalid for Bitcoin "
                    "(expected base58 legacy 26-35 chars or mainnet bech32 starting with 'bc1').",
                    field=field_name,
                    reason="Structurally invalid Bitcoin address",
                )
        case "solana":
            if not _SOLANA_BASE58_PATTERN.match(cleaned):
                raise NormalizationError(
                    f"Field '{field_name}' is structurally invalid for Solana "
                    "(expected base58 32-44 chars).",
                    field=field_name,
                    reason="Structurally invalid Solana address",
                )
        case "tron":
            if not _TRON_BASE58_PATTERN.match(cleaned):
                raise NormalizationError(
                    f"Field '{field_name}' is structurally invalid for Tron "
                    "(expected base58 starting with 'T', 34 chars).",
                    field=field_name,
                    reason="Structurally invalid Tron address",
                )
        case _:
            raise NormalizationError(
                f"Unknown address family '{family}' for address validation.",
                field=field_name,
                reason=f"Unknown address family '{family}'",
            )


@register_adapter
class CryptoAdapter(NetworkAdapter):
    """Adapter translating cryptocurrency value transfer events into the universal domain model."""

    profile: ClassVar[NetworkProfile] = CRYPTO_PROFILE
    adapter_version: ClassVar[str] = "1.0.0"
    discarded_fields: ClassVar[list[str]] = []

    def can_handle(self, event: RawEvent) -> bool:
        """Determine if this adapter handles the incoming event."""
        return event.network_id.lower() == "crypto"

    def describe(self) -> dict[str, Any]:
        """Describe adapter capabilities, chain specifications, and configuration defaults."""
        info = super().describe()
        info["chains"] = {k: v._asdict() for k, v in DEFAULT_CHAINS.items()}
        info["discarded_fields"] = self.discarded_fields
        return info

    def normalize(self, event: RawEvent) -> NormalizationResult:
        """Normalize a raw cryptocurrency transfer event into the universal domain model."""
        payload = payload_as_dict(event)
        warnings: list[str] = []

        # 1. Reject raw UTXO-style inputs/outputs payloads
        if "inputs" in payload or "outputs" in payload:
            raise UnsupportedPayloadError(
                "Raw UTXO-style inputs/outputs payloads are unsupported in v1; "
                "UTXO decomposition must happen upstream."
            )

        # 2. Extract mandatory transaction identifiers
        raw_chain = require(payload, "chain", "chain")
        tx_hash = str(require(payload, "tx_hash", "tx_hash")).strip()
        raw_ti = payload.get("transfer_index")
        if raw_ti is None:
            transfer_index = 0
        elif isinstance(raw_ti, bool):
            raise NormalizationError(
                "Field 'transfer_index' must be an integer, got bool.",
                field="transfer_index",
                reason="Invalid transfer_index",
            )
        else:
            try:
                transfer_index = int(raw_ti)
            except (ValueError, TypeError) as exc:
                raise NormalizationError(
                    "Field 'transfer_index' must be an integer.",
                    field="transfer_index",
                    reason="Invalid transfer_index",
                ) from exc

        raw_status = str(require(payload, "status", "status")).strip().lower()

        # 3. Resolve chain specification and address family
        chain_spec = resolve_chain_spec(
            chain_name=raw_chain,
            payload_family=payload.get("address_family"),
            payload_required_confirmations=payload.get("required_confirmations"),
        )
        family = chain_spec.family
        required_confirmations = chain_spec.required_confirmations
        chain = str(raw_chain).strip().lower()

        # 4. Extract and validate counterparties
        raw_from = payload.get("from_address")
        if raw_from is None or not str(raw_from).strip():
            raise NormalizationError(
                "Field 'from_address' is required but missing or empty.",
                field="from_address",
                reason="Missing from_address",
            )

        raw_to = payload.get("to_address")
        if raw_to is None or not str(raw_to).strip():
            raise UnsupportedPayloadError(
                "Contract creation transactions without a to_address cannot be "
                "represented as payment transfers."
            )

        from_addr = str(raw_from).strip()
        to_addr = str(raw_to).strip()

        # Self-transfer check
        if from_addr == to_addr:
            raise UnsupportedPayloadError(
                "Self-transfers where from_address equals to_address cannot be represented "
                "because Module 2 requires sender and receiver to be distinct entities."
            )

        # Structural address validation
        _validate_address_structure(from_addr, family, "from_address")
        _validate_address_structure(to_addr, family, "to_address")

        # 5. Asset resolution
        asset_dict = payload.get("asset")
        if asset_dict is not None and not isinstance(asset_dict, dict):
            raise NormalizationError(
                "Field 'asset' must be an object.",
                field="asset",
                reason="Invalid asset structure",
            )

        def _parse_decimals(raw_val: Any, field_name: str) -> int:
            if isinstance(raw_val, bool) or raw_val is None:
                raise NormalizationError(
                    f"Field '{field_name}' must be an integer, got {type(raw_val).__name__}.",
                    field=field_name,
                    reason=f"Invalid {field_name}",
                )
            try:
                return int(raw_val)
            except (ValueError, TypeError) as exc:
                raise NormalizationError(
                    f"Field '{field_name}' must be an integer.",
                    field=field_name,
                    reason=f"Invalid {field_name}",
                ) from exc

        token_id: str | None = None
        if not asset_dict:
            asset_type = "native"
            asset_symbol = chain_spec.native_symbol
            asset_decimals = chain_spec.decimals
            contract_address = None
        else:
            asset_type = str(asset_dict.get("type", "native")).strip().lower()
            if asset_type == "native":
                raw_sym = asset_dict.get("symbol")
                if raw_sym is not None and not str(raw_sym).strip():
                    raise NormalizationError(
                        "Field 'asset.symbol' cannot be blank.",
                        field="asset.symbol",
                        reason="Blank asset.symbol",
                    )
                asset_symbol = (
                    str(raw_sym).strip().upper()
                    if raw_sym is not None
                    else chain_spec.native_symbol
                )
                asset_decimals = _parse_decimals(
                    asset_dict.get("decimals", chain_spec.decimals), "asset.decimals"
                )
                contract_address = None
            elif asset_type == "token":
                asset_symbol = str(require(asset_dict, "symbol", "asset.symbol")).strip().upper()
                asset_decimals = _parse_decimals(
                    require(asset_dict, "decimals", "asset.decimals"), "asset.decimals"
                )
                contract_address = str(
                    require(asset_dict, "contract_address", "asset.contract_address")
                ).strip()
            elif asset_type == "nft":
                asset_symbol = str(asset_dict.get("symbol", "NFT")).strip().upper()
                asset_decimals = 0
                contract_address = str(
                    require(asset_dict, "contract_address", "asset.contract_address")
                ).strip()
                raw_token_id = require(asset_dict, "token_id", "asset.token_id")
                token_id = str(raw_token_id).strip()
                if not re.match(r"^[A-Za-z0-9]{1,80}$", token_id):
                    raise NormalizationError(
                        "Field 'asset.token_id' must match [A-Za-z0-9]{1,80}.",
                        field="asset.token_id",
                        reason="Invalid token_id format",
                    )
            else:
                raise NormalizationError(
                    f"Unsupported asset type '{asset_type}'. Allowed: 'native', 'token', 'nft'.",
                    field="asset.type",
                    reason=f"Unsupported asset type '{asset_type}'",
                )

        if not asset_symbol or not asset_symbol.strip():
            raise NormalizationError(
                "Field 'asset.symbol' cannot be blank.",
                field="asset.symbol",
                reason="Blank asset.symbol",
            )

        if asset_decimals > 18:
            raise NormalizationError(
                f"Asset decimals {asset_decimals} exceeds Ripple universal scale limit of 18.",
                field="asset.decimals",
                reason="Asset decimals exceeds 18",
            )
        if asset_decimals < 0:
            raise NormalizationError(
                "Asset decimals cannot be negative.",
                field="asset.decimals",
                reason="Negative asset decimals",
            )

        # 6. Monetary amount validation (Strict Float Prohibition)
        for field_key, val in [
            ("amount", payload.get("amount")),
            ("amount_raw", payload.get("amount_raw")),
            ("usd_value", payload.get("usd_value")),
            ("block_number", payload.get("block_number")),
            ("nonce", payload.get("nonce")),
            ("source_feed", payload.get("source_feed")),
        ]:
            if isinstance(val, float):
                raise NormalizationError(
                    f"Field '{field_key}' must not be a float. Please supply amounts as strings.",
                    field=field_key,
                    reason=f"Float prohibited in '{field_key}'",
                )

        token_standard = payload.get("token_standard") or (
            asset_dict.get("token_standard") if asset_dict else None
        )
        if token_standard is not None:
            token_standard = str(token_standard).strip()

        if asset_type == "nft":
            if payload.get("amount") is not None:
                raise NormalizationError(
                    "Field 'amount' is not allowed for NFT assets.",
                    field="amount",
                    reason="amount not allowed for NFT",
                )
            if payload.get("amount_raw") is not None:
                raise NormalizationError(
                    "Field 'amount_raw' is not allowed for NFT assets.",
                    field="amount_raw",
                    reason="amount_raw not allowed for NFT",
                )

            raw_qty = payload.get("quantity", "1")
            if isinstance(raw_qty, float):
                raise NormalizationError(
                    "Field 'quantity' must not be a float.",
                    field="quantity",
                    reason="Float quantity prohibited",
                )
            if isinstance(raw_qty, int) and not isinstance(raw_qty, bool):
                qty_str = str(raw_qty)
            elif isinstance(raw_qty, str):
                qty_str = raw_qty.strip()
            else:
                raise NormalizationError(
                    "Field 'quantity' must be an integer string.",
                    field="quantity",
                    reason="Invalid quantity type",
                )

            if not qty_str.isdigit():
                raise NormalizationError(
                    f"Field 'quantity' must be an integer, got '{qty_str}'.",
                    field="quantity",
                    reason="Non-integer quantity",
                )

            qty_int = int(qty_str)
            if qty_int == 0:
                raise NormalizationError(
                    "Field 'quantity' cannot be 0.",
                    field="quantity",
                    reason="Zero quantity rejected",
                )

            if token_standard and token_standard.lower() == "erc721" and qty_int != 1:
                raise NormalizationError(
                    "Field 'quantity' must be '1' for ERC-721 tokens.",
                    field="quantity",
                    reason="ERC-721 quantity must be 1",
                )

            parsed_amount = Decimal(qty_int)
        else:
            has_amount = payload.get("amount") is not None
            has_amount_raw = payload.get("amount_raw") is not None

            if has_amount and has_amount_raw:
                raise NormalizationError(
                    "Supply exactly one of 'amount' or 'amount_raw', not both.",
                    field="amount",
                    reason="Mutually exclusive amount and amount_raw",
                )
            if not has_amount and not has_amount_raw:
                raise NormalizationError(
                    "Missing transfer amount: supply either 'amount' or 'amount_raw'.",
                    field="amount",
                    reason="Missing transfer amount",
                )

            if has_amount_raw:
                raw_str = str(payload["amount_raw"]).strip()
                if not raw_str.isdigit():
                    raise NormalizationError(
                        "Field 'amount_raw' must contain only digits.",
                        field="amount_raw",
                        reason="Non-digit amount_raw",
                    )
                parsed_amount = Decimal(int(raw_str)) / (Decimal(10) ** asset_decimals)
            else:
                parsed_amount = parse_amount(
                    payload["amount"],
                    field="amount",
                    scale=asset_decimals,
                )

            if parsed_amount == Decimal(0):
                raise UnsupportedPayloadError(
                    "Zero-value transfers cannot be represented because Module 2 requires "
                    "strictly positive amounts. This is a documented Ripple limitation."
                )

        # 7. Fee resolution
        fee_money: Money | None = None
        fee_dict = payload.get("fee")
        if isinstance(fee_dict, dict):
            for fee_field, fval in [
                ("fee.amount", fee_dict.get("amount")),
                ("fee.amount_raw", fee_dict.get("amount_raw")),
            ]:
                if isinstance(fval, float):
                    raise NormalizationError(
                        f"Field '{fee_field}' must not be a float.",
                        field=fee_field,
                        reason="Float prohibited in fee",
                    )

            fee_symbol_raw = fee_dict.get("symbol")
            if fee_symbol_raw is not None and not str(fee_symbol_raw).strip():
                raise NormalizationError(
                    "Field 'fee.symbol' cannot be blank.",
                    field="fee.symbol",
                    reason="Blank fee.symbol",
                )
            fee_symbol = (
                str(fee_symbol_raw).strip().upper()
                if fee_symbol_raw is not None
                else chain_spec.native_symbol
            )
            fee_decimals = chain_spec.decimals if fee_symbol == chain_spec.native_symbol else 18
            if "decimals" in fee_dict:
                fee_decimals = _parse_decimals(fee_dict["decimals"], "fee.decimals")

            has_fee_raw = fee_dict.get("amount_raw") is not None
            has_fee_amt = fee_dict.get("amount") is not None

            if has_fee_raw:
                f_raw_str = str(fee_dict["amount_raw"]).strip()
                if not f_raw_str.isdigit():
                    raise NormalizationError(
                        "Field 'fee.amount_raw' must contain only digits.",
                        field="fee.amount_raw",
                        reason="Non-digit fee.amount_raw",
                    )
                parsed_fee = Decimal(int(f_raw_str)) / (Decimal(10) ** fee_decimals)
                if parsed_fee > Decimal(0):
                    fee_money = Money(
                        amount=parsed_fee,
                        currency=fee_symbol,
                        scale=fee_decimals,
                        asset_class=AssetClass.CRYPTO,
                    )
            elif has_fee_amt:
                parsed_fee = parse_amount(
                    fee_dict["amount"],
                    field="fee.amount",
                    scale=fee_decimals,
                )
                if parsed_fee > Decimal(0):
                    fee_money = Money(
                        amount=parsed_fee,
                        currency=fee_symbol,
                        scale=fee_decimals,
                        asset_class=AssetClass.CRYPTO,
                    )

        # USD normalized value
        normalized_value: Decimal | None = None
        usd_val = payload.get("usd_value")
        if usd_val is not None:
            normalized_value = parse_amount(usd_val, field="usd_value", scale=2)

        consideration = payload.get("consideration")
        sale_tx_hash = payload.get("sale_tx_hash")
        if isinstance(consideration, dict):
            if isinstance(consideration.get("usd_value"), float):
                raise NormalizationError(
                    "Field 'consideration.usd_value' must not be a float.",
                    field="consideration.usd_value",
                    reason="Float prohibited in consideration.usd_value",
                )
            if normalized_value is None and consideration.get("usd_value") is not None:
                normalized_value = parse_amount(
                    consideration["usd_value"], field="consideration.usd_value", scale=2
                )

        transfer_money = Money(
            amount=parsed_amount,
            currency=asset_symbol,
            scale=asset_decimals,
            asset_class=AssetClass.CRYPTO,
            normalized_value=normalized_value,
        )

        # 8. Timestamps resolution
        first_seen_dt = None
        if payload.get("first_seen_at") is not None:
            first_seen_dt, warn_fs = parse_datetime(payload["first_seen_at"], field="first_seen_at")
            if warn_fs:
                warnings.append(warn_fs)

        block_time_dt = None
        if payload.get("block_time") is not None:
            block_time_dt, warn_bt = parse_datetime(payload["block_time"], field="block_time")
            if warn_bt:
                warnings.append(warn_bt)

        finalized_dt = None
        if payload.get("finalized_at") is not None:
            finalized_dt, warn_fn = parse_datetime(payload["finalized_at"], field="finalized_at")
            if warn_fn:
                warnings.append(warn_fn)

        # 9. Lifecycle status and stage mapping
        execution_status = str(payload.get("execution_status", "success")).strip().lower()
        finalized_flag = payload.get("finalized")
        confirmations = payload.get("confirmations")
        if confirmations is not None:
            if isinstance(confirmations, bool):
                raise NormalizationError(
                    "Field 'confirmations' must be an integer, got bool.",
                    field="confirmations",
                    reason="Invalid confirmations type",
                )
            try:
                confirmations = int(confirmations)
            except (ValueError, TypeError) as exc:
                raise NormalizationError(
                    "Field 'confirmations' must be an integer.",
                    field="confirmations",
                    reason="Invalid confirmations format",
                ) from exc
        reorged_flag = raw_status == "reorged"

        settled_at = None

        match raw_status:
            case "pending":
                if first_seen_dt is None:
                    raise NormalizationError(
                        "Field 'first_seen_at' is required for pending transactions.",
                        field="first_seen_at",
                        reason="Missing first_seen_at for pending transaction",
                    )
                universal_status = TransactionStatus.PENDING_SETTLEMENT
                lifecycle_stage = "mempool"

            case "included":
                if execution_status == "reverted":
                    universal_status = TransactionStatus.FAILED
                    lifecycle_stage = "failed"
                    settled_at = None
                elif finalized_flag is True or (
                    confirmations is not None and confirmations >= required_confirmations
                ):
                    universal_status = TransactionStatus.CONFIRMED
                    lifecycle_stage = "finalized"
                    if finalized_dt is not None:
                        settled_at = finalized_dt
                    elif block_time_dt is not None:
                        settled_at = block_time_dt
                        warnings.append("Finality time was approximated using block_time.")
                    else:
                        settled_at = event.received_at
                        warnings.append("Finality time was approximated using event received_at.")
                else:
                    if block_time_dt is None:
                        raise NormalizationError(
                            "Field 'block_time' is required for confirming transactions.",
                            field="block_time",
                            reason="Missing block_time for included transaction",
                        )
                    if confirmations is None:
                        raise NormalizationError(
                            "Field 'confirmations' is required for confirming transactions.",
                            field="confirmations",
                            reason="Missing confirmations for included transaction",
                        )
                    universal_status = TransactionStatus.PENDING_SETTLEMENT
                    lifecycle_stage = f"confirmation_{confirmations}"

            case "failed":
                universal_status = TransactionStatus.FAILED
                lifecycle_stage = "failed"

            case "dropped":
                universal_status = TransactionStatus.CANCELLED
                lifecycle_stage = "dropped"

            case "replaced":
                universal_status = TransactionStatus.CANCELLED
                lifecycle_stage = "replaced"

            case "reorged":
                universal_status = TransactionStatus.PENDING_SETTLEMENT
                lifecycle_stage = "reorged"
                settled_at = None
                warnings.append(
                    "Transaction was reorged: previously confirmed transfer was un-confirmed; "
                    "Mesh layer must treat newer snapshot as superseding."
                )

            case _:
                raise NormalizationError(
                    f"Unsupported cryptocurrency status '{raw_status}'. "
                    "Allowed: pending, included, failed, dropped, replaced, reorged.",
                    field="status",
                    reason=f"Unsupported status '{raw_status}'",
                )

        # 10. Initiated at resolution
        if first_seen_dt is not None:
            initiated_at = first_seen_dt
        elif block_time_dt is not None:
            initiated_at = block_time_dt
            warnings.append("Initiation time was approximated using block_time.")
        else:
            initiated_at = event.received_at
            warnings.append("Initiation time was approximated using event received_at.")

        # 11. Party entity construction
        sender_ident = PartyIdentifier(
            type=IdentifierType.CRYPTO_ADDRESS,
            value=from_addr,
            network="crypto",
            is_pseudonymous=True,
        )
        receiver_ident = PartyIdentifier(
            type=IdentifierType.CRYPTO_ADDRESS,
            value=to_addr,
            network="crypto",
            is_pseudonymous=True,
        )

        sender_norm = sender_ident.normalize()
        receiver_norm = receiver_ident.normalize()

        sender_id = deterministic_uuid("crypto", family, sender_norm)
        receiver_id = deterministic_uuid("crypto", family, receiver_norm)

        from_label = payload.get("from_label")
        to_label = payload.get("to_label")

        from_type = (
            PartyType.EXCHANGE
            if isinstance(from_label, dict) and from_label.get("category") == "exchange"
            else PartyType.UNKNOWN
        )
        to_type = (
            PartyType.EXCHANGE
            if isinstance(to_label, dict) and to_label.get("category") == "exchange"
            else PartyType.UNKNOWN
        )

        from_sanctioned = bool(
            isinstance(from_label, dict) and from_label.get("category") == "sanctioned"
        )
        to_sanctioned = bool(
            isinstance(to_label, dict) and to_label.get("category") == "sanctioned"
        )

        from_attrs: dict[str, Any] = {
            "address_family": family,
            "observed_chain": chain,
            "is_contract": payload.get("from_is_contract"),
        }
        if from_label is not None:
            from_attrs["label"] = from_label

        to_attrs: dict[str, Any] = {
            "address_family": family,
            "observed_chain": chain,
            "is_contract": payload.get("to_is_contract"),
        }
        if to_label is not None:
            to_attrs["label"] = to_label

        sender_party = Party(
            party_id=sender_id,
            identifiers=[sender_ident],
            party_type=from_type,
            risk_flags=RiskFlags(is_sanctioned=from_sanctioned),
            attributes=from_attrs,
            home_network="crypto",
            created_at=event.received_at,
        )
        receiver_party = Party(
            party_id=receiver_id,
            identifiers=[receiver_ident],
            party_type=to_type,
            risk_flags=RiskFlags(is_sanctioned=to_sanctioned),
            attributes=to_attrs,
            home_network="crypto",
            created_at=event.received_at,
        )

        # 12. Correlation and Transaction ID derivation
        hash_for_key = tx_hash.lower() if family == "evm" else tx_hash
        correlation_key = f"crypto:{chain}:{hash_for_key}:{transfer_index}"
        transaction_id = deterministic_uuid("crypto", chain, tx_hash, str(transfer_index))
        network_transaction_ref = tx_hash

        # 13. Network metadata assembly
        if asset_type == "nft":
            assert contract_address is not None
            c_addr = contract_address.lower() if family == "evm" else contract_address
            asset_id = f"{chain}:{c_addr}:{token_id}"
        else:
            asset_id = f"{chain}:{contract_address if contract_address else 'native'}"

        network_metadata: dict[str, Any] = {
            "chain": chain,
            "tx_hash": tx_hash,
            "transfer_index": transfer_index,
            "block_number": payload.get("block_number"),
            "confirmations": confirmations,
            "required_confirmations": required_confirmations,
            "execution_status": execution_status,
            "asset_id": asset_id,
            "asset_contract": contract_address,
            "token_standard": token_standard,
            "nonce": payload.get("nonce"),
            "replaced_by": payload.get("replaced_by"),
            "reorged": reorged_flag,
            "source_feed": payload.get("source_feed"),
        }
        if token_id is not None:
            network_metadata["token_id"] = token_id
        if consideration is not None:
            network_metadata["consideration"] = consideration
        if sale_tx_hash is not None:
            network_metadata["sale_tx_hash"] = sale_tx_hash
        if normalized_value is not None:
            network_metadata["normalized_currency"] = "USD"

        # 14. Transaction construction wrapped in wrap_domain_errors
        with wrap_domain_errors(default_field="transaction"):
            tx = Transaction(
                transaction_id=transaction_id,
                network_transaction_ref=network_transaction_ref,
                network="CRYPTO",
                sender_id=sender_id,
                receiver_id=receiver_id,
                sender_identifier=sender_ident,
                receiver_identifier=receiver_ident,
                amount=transfer_money,
                fee=fee_money,
                initiated_at=initiated_at,
                settled_at=settled_at,
                observed_at=event.received_at,
                status=universal_status,
                settlement_model=SettlementModel.BLOCK_CONFIRMATION,
                direction=TransactionDirection.TRANSFER,
                is_reversible=False,
                reversal_window_ends_at=None,
                intermediary_hops=[],
                channel="onchain",
                device_id=payload.get("device_id"),
                ip_address=payload.get("ip_address"),
                geo_country=None,
                network_metadata=network_metadata,
            )

        return NormalizationResult(
            transaction=tx,
            parties=[sender_party, receiver_party],
            correlation_key=correlation_key,
            lifecycle_stage=lifecycle_stage,
            warnings=warnings,
            adapter_version=self.adapter_version,
        )
