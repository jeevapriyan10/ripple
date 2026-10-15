"""Unit tests for Cryptocurrency network adapter (backend/ripple/adapters/crypto/)."""

import copy
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from ripple.adapters.contract import PayloadFormat, RawEvent
from ripple.adapters.crypto import CRYPTO_PROFILE, CryptoAdapter
from ripple.adapters.errors import NormalizationError, UnsupportedPayloadError
from ripple.domain.party import PartyType
from ripple.domain.transaction import (
    SettlementModel,
    TransactionStatus,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "crypto"


def load_fixture(name: str) -> dict[str, Any]:
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        # pyrefly: ignore [no-any-return-explicit]
        return json.load(f)


def make_raw_event(
    payload: Any,
    received_at: datetime = datetime(2026, 10, 14, 8, 30, 0, tzinfo=UTC),
) -> RawEvent:
    return RawEvent(
        network_id="crypto",
        payload=payload,
        payload_format=PayloadFormat.JSON,
        received_at=received_at,
    )


def test_crypto_adapter_profile_and_describe() -> None:
    """Assert CryptoAdapter profile matches specifications and describe() exposes chain table."""
    adapter = CryptoAdapter()
    assert adapter.profile == CRYPTO_PROFILE
    assert adapter.profile.network_id == "crypto"
    assert adapter.profile.settlement_model == SettlementModel.BLOCK_CONFIRMATION
    assert adapter.profile.is_reversible is False

    desc = adapter.describe()
    assert "chains" in desc
    assert "ethereum" in desc["chains"]
    assert "bitcoin" in desc["chains"]
    assert "solana" in desc["chains"]
    assert "tron" in desc["chains"]


def test_crypto_lifecycle_pending_confirming_finalized() -> None:
    """Assert lifecycle pending -> confirming -> finalized preserves correlation_key and id."""
    adapter = CryptoAdapter()

    # 1. Pending (mempool)
    ev_pending = make_raw_event(load_fixture("transfer_pending.json"))
    res_pending = adapter.normalize(ev_pending)

    assert res_pending.transaction.status == TransactionStatus.PENDING_SETTLEMENT
    assert res_pending.lifecycle_stage == "mempool"
    assert res_pending.transaction.settled_at is None
    assert res_pending.transaction.initiated_at == datetime(2026, 10, 14, 8, 0, 0, tzinfo=UTC)

    # 2. Included / Confirming (confirmation_3)
    ev_confirming = make_raw_event(load_fixture("transfer_confirming.json"))
    res_confirming = adapter.normalize(ev_confirming)

    assert res_confirming.transaction.status == TransactionStatus.PENDING_SETTLEMENT
    assert res_confirming.lifecycle_stage == "confirmation_3"
    assert res_confirming.transaction.settled_at is None

    # 3. Finalized (CONFIRMED)
    ev_finalized = make_raw_event(load_fixture("transfer_finalized.json"))
    res_finalized = adapter.normalize(ev_finalized)

    assert res_finalized.transaction.status == TransactionStatus.CONFIRMED
    assert res_finalized.lifecycle_stage == "finalized"
    assert res_finalized.transaction.settled_at == datetime(2026, 10, 14, 8, 13, 0, tzinfo=UTC)

    # All three lifecycle events share the identical correlation_key and transaction_id
    assert (
        res_pending.correlation_key
        == res_confirming.correlation_key
        == res_finalized.correlation_key
    )
    assert (
        res_pending.transaction.transaction_id
        == res_confirming.transaction.transaction_id
        == res_finalized.transaction.transaction_id
    )
    assert res_pending.correlation_key.startswith("crypto:ethereum:0xaaaaaaaa")


def test_crypto_finalized_below_confirmation_threshold_via_flag() -> None:
    """Assert finalized: true yields CONFIRMED even if confirmations < required_confirmations."""
    adapter = CryptoAdapter()
    payload = load_fixture("transfer_confirming.json")
    payload["confirmations"] = 5  # below ethereum 64 threshold
    payload["finalized"] = True
    payload["finalized_at"] = "2026-10-14T08:02:00Z"

    res = adapter.normalize(make_raw_event(payload))
    assert res.transaction.status == TransactionStatus.CONFIRMED
    assert res.lifecycle_stage == "finalized"
    assert res.transaction.settled_at == datetime(2026, 10, 14, 8, 2, 0, tzinfo=UTC)


def test_crypto_token_transfer_with_decimals_and_contract() -> None:
    """Assert token transfer normalizes with correct asset contract, decimals, and native fee."""
    adapter = CryptoAdapter()
    ev = make_raw_event(load_fixture("token_transfer.json"))
    res = adapter.normalize(ev)

    tx = res.transaction
    assert tx.amount.currency == "USDC"
    assert tx.amount.scale == 6
    assert tx.amount.amount == Decimal("250.750000")
    assert tx.fee is not None
    assert tx.fee.currency == "ETH"  # Fee in native asset, differing from transferred USDC
    assert tx.fee.scale == 18
    assert tx.fee.amount == Decimal("0.001500000000000000")

    meta = tx.network_metadata
    assert meta["asset_contract"] == "0x3333333333333333333333333333333333333333"
    assert meta["asset_id"] == "ethereum:0x3333333333333333333333333333333333333333"
    assert meta["token_standard"] == "ERC-20"


def test_crypto_18_decimal_amount_raw_exact_round_trip() -> None:
    """Assert 18-decimal amount_raw parses with exact Decimal arithmetic and zero precision loss."""
    adapter = CryptoAdapter()
    payload = load_fixture("transfer_pending.json")
    # 1.234567890123456789 ETH in Wei (18 decimal places)
    raw_wei = "1234567890123456789"
    payload.pop("amount", None)
    payload["amount_raw"] = raw_wei

    res = adapter.normalize(make_raw_event(payload))
    assert res.transaction.amount.scale == 18
    assert res.transaction.amount.amount == Decimal("1.234567890123456789")


def test_crypto_reverted_execution_fails_with_fee_kept() -> None:
    """Assert reverted execution maps to FAILED, settled_at is None, and gas fee is recorded."""
    adapter = CryptoAdapter()
    res = adapter.normalize(make_raw_event(load_fixture("transfer_reverted.json")))

    assert res.transaction.status == TransactionStatus.FAILED
    assert res.lifecycle_stage == "failed"
    assert res.transaction.settled_at is None
    assert res.transaction.fee is not None
    assert res.transaction.fee.amount == Decimal("0.000850000000000000")


def test_crypto_dropped_and_replaced_statuses() -> None:
    """Assert dropped and replaced statuses map to CANCELLED with replaced_by metadata."""
    adapter = CryptoAdapter()
    base_payload = load_fixture("transfer_pending.json")

    # Dropped
    payload_dropped = dict(base_payload, status="dropped")
    res_dropped = adapter.normalize(make_raw_event(payload_dropped))
    assert res_dropped.transaction.status == TransactionStatus.CANCELLED
    assert res_dropped.lifecycle_stage == "dropped"

    # Replaced
    new_hash = "0xdddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
    payload_replaced = dict(base_payload, status="replaced", replaced_by=new_hash)
    res_replaced = adapter.normalize(make_raw_event(payload_replaced))
    assert res_replaced.transaction.status == TransactionStatus.CANCELLED
    assert res_replaced.lifecycle_stage == "replaced"
    assert res_replaced.transaction.network_metadata["replaced_by"] == new_hash


def test_crypto_reorg_status_and_warning() -> None:
    """Assert reorged status maps back to PENDING_SETTLEMENT with settled_at None and warning."""
    adapter = CryptoAdapter()
    payload = dict(load_fixture("transfer_finalized.json"), status="reorged")
    res = adapter.normalize(make_raw_event(payload))

    assert res.transaction.status == TransactionStatus.PENDING_SETTLEMENT
    assert res.lifecycle_stage == "reorged"
    assert res.transaction.settled_at is None
    assert res.transaction.network_metadata["reorged"] is True
    assert any("reorged" in w.lower() for w in res.warnings)


def test_crypto_two_transfers_same_tx_hash_different_transfer_index() -> None:
    """Assert two transfers inside same tx_hash get distinct transaction_id and correlation_key."""
    adapter = CryptoAdapter()
    base_payload = load_fixture("token_transfer.json")

    payload0 = dict(base_payload, transfer_index=0)
    payload1 = dict(base_payload, transfer_index=1)

    res0 = adapter.normalize(make_raw_event(payload0))
    res1 = adapter.normalize(make_raw_event(payload1))

    assert res0.transaction.network_transaction_ref == res1.transaction.network_transaction_ref
    assert res0.transaction.transaction_id != res1.transaction.transaction_id
    assert res0.correlation_key != res1.correlation_key
    assert res0.correlation_key.endswith(":0")
    assert res1.correlation_key.endswith(":1")


def test_crypto_address_normalization_evm_and_base58() -> None:
    """Assert EVM addresses are case-insensitive lowercased while base58 addresses preserve case."""
    adapter = CryptoAdapter()

    # EVM mixed-case vs lower-case: identical party_id
    evm_mixed = "0x11111111111111111111111111111111111111aB"
    evm_lower = "0x11111111111111111111111111111111111111ab"
    p_evm_1 = dict(load_fixture("transfer_pending.json"), from_address=evm_mixed)
    p_evm_2 = dict(load_fixture("transfer_pending.json"), from_address=evm_lower)

    res_evm_1 = adapter.normalize(make_raw_event(p_evm_1))
    res_evm_2 = adapter.normalize(make_raw_event(p_evm_2))
    assert res_evm_1.transaction.sender_id == res_evm_2.transaction.sender_id

    # Solana base58: case preserved; differing case produces different party_id
    sol_from_1 = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    sol_from_2 = "tokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    sol_to = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"

    p_sol_1 = {
        "chain": "solana",
        "tx_hash": "5Verx8r9nYBPnWbkLzdpba2TA47e4k1A97cR5zPAG4bYh4s7AwhmEwP5YqC2z5u2y1",
        "status": "pending",
        "from_address": sol_from_1,
        "to_address": sol_to,
        "amount": "10.000000000",
        "first_seen_at": 1728864000,
    }
    p_sol_2 = dict(p_sol_1, from_address=sol_from_2)

    res_sol_1 = adapter.normalize(make_raw_event(p_sol_1))
    res_sol_2 = adapter.normalize(make_raw_event(p_sol_2))
    assert res_sol_1.transaction.sender_id != res_sol_2.transaction.sender_id


def test_crypto_custom_chain_resolution() -> None:
    """Assert unlisted chain is rejected unless family and confirmations are provided."""
    adapter = CryptoAdapter()
    base_payload = load_fixture("transfer_pending.json")

    # Unlisted chain without parameters raises NormalizationError listing known chains
    payload_bad_chain = dict(base_payload, chain="arbitrum")
    with pytest.raises(NormalizationError) as exc_chain:
        adapter.normalize(make_raw_event(payload_bad_chain))
    assert "known chains" in str(exc_chain.value).lower()
    assert "arbitrum" in str(exc_chain.value)

    # With address_family and required_confirmations supplied, custom chain succeeds
    payload_valid_custom = dict(
        base_payload,
        chain="arbitrum",
        address_family="evm",
        required_confirmations=20,
    )
    res = adapter.normalize(make_raw_event(payload_valid_custom))
    assert res.transaction.network_metadata["chain"] == "arbitrum"
    assert res.transaction.network_metadata["required_confirmations"] == 20


def test_crypto_rejections_utxo_zero_self_contract_creation_and_floats() -> None:
    """Assert clear typed rejections for UTXO, zero-value, self-transfer, and floats."""
    adapter = CryptoAdapter()
    base_payload = load_fixture("transfer_pending.json")

    # 1. UTXO inputs/outputs rejected with clear message
    with pytest.raises(UnsupportedPayloadError) as exc_utxo:
        adapter.normalize(make_raw_event(dict(base_payload, inputs=[{"txid": "abc"}])))
    assert "UTXO decomposition must happen upstream" in str(exc_utxo.value)

    # 2. Zero-value transfer rejected
    with pytest.raises(UnsupportedPayloadError) as exc_zero:
        adapter.normalize(make_raw_event(dict(base_payload, amount="0.000000000000000000")))
    assert "strictly positive amounts" in str(exc_zero.value)

    # 3. Self-transfer (from == to) rejected
    with pytest.raises(UnsupportedPayloadError) as exc_self:
        adapter.normalize(
            make_raw_event(dict(base_payload, to_address=base_payload["from_address"]))
        )
    assert "sender and receiver to be distinct" in str(exc_self.value)

    # 4. Missing to_address (contract creation) rejected
    with pytest.raises(UnsupportedPayloadError) as exc_create:
        p_no_to = dict(base_payload)
        p_no_to.pop("to_address", None)
        adapter.normalize(make_raw_event(p_no_to))
    assert "Contract creation" in str(exc_create.value)

    # 5. Decimals above 18 rejected
    with pytest.raises(NormalizationError) as exc_dec:
        p_high_dec = dict(
            base_payload,
            asset={
                "type": "token",
                "symbol": "HIGH",
                "decimals": 19,
                "contract_address": "0x3333333333333333333333333333333333333333",
            },
        )
        adapter.normalize(make_raw_event(p_high_dec))
    assert "exceeds Ripple universal scale limit of 18" in str(exc_dec.value)

    # 6. Float amount rejected
    with pytest.raises(NormalizationError) as exc_flt:
        adapter.normalize(make_raw_event(dict(base_payload, amount=1.5)))
    assert "must not be a float" in str(exc_flt.value)

    # 7. Non-digit amount_raw rejected
    with pytest.raises(NormalizationError) as exc_raw_nd:
        p_bad_raw = dict(base_payload)
        p_bad_raw.pop("amount", None)
        p_bad_raw["amount_raw"] = "1000abc"
        adapter.normalize(make_raw_event(p_bad_raw))
    assert "must contain only digits" in str(exc_raw_nd.value)


def test_crypto_invalid_address_rejected_without_echoing_address() -> None:
    """Assert invalid address format fails without echoing the submitted address."""
    adapter = CryptoAdapter()
    base_payload = load_fixture("transfer_pending.json")

    invalid_evm = "0xinvalidaddressnothex123456789012345678"
    p_bad_addr = dict(base_payload, from_address=invalid_evm)

    with pytest.raises(NormalizationError) as exc_info:
        adapter.normalize(make_raw_event(p_bad_addr))

    assert "structurally invalid" in str(exc_info.value).lower()
    # The invalid address value must NEVER appear in the error message or reason
    assert invalid_evm not in str(exc_info.value)
    assert invalid_evm not in exc_info.value.reason


def test_crypto_labels_party_types_and_sanctions() -> None:
    """Assert exchange label maps to EXCHANGE, sanctioned sets is_sanctioned, mule stays None."""
    adapter = CryptoAdapter()
    payload = dict(
        load_fixture("transfer_pending.json"),
        from_label={"entity": "Kraken", "category": "exchange"},
        to_label={"entity": "Sanctioned Entity", "category": "sanctioned"},
        from_is_contract=False,
        to_is_contract=True,
    )

    res = adapter.normalize(make_raw_event(payload))
    sender = next(p for p in res.parties if p.party_id == res.transaction.sender_id)
    receiver = next(p for p in res.parties if p.party_id == res.transaction.receiver_id)

    assert sender.party_type == PartyType.EXCHANGE
    assert sender.risk_flags.is_sanctioned is False
    assert sender.is_mule is None
    assert sender.mule_role is None
    assert sender.attributes["is_contract"] is False
    assert sender.attributes["label"]["entity"] == "Kraken"

    assert receiver.party_type == PartyType.UNKNOWN
    assert receiver.risk_flags.is_sanctioned is True
    assert receiver.is_mule is None
    assert receiver.mule_role is None
    assert receiver.attributes["is_contract"] is True


def test_crypto_usd_value_and_timestamps_and_approximations() -> None:
    """Assert usd_value sets Money.normalized_value and integer epochs work."""
    adapter = CryptoAdapter()
    payload = {
        "chain": "bitcoin",
        "tx_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "status": "included",
        "from_address": "1SyntheticBtcAddress1111111111111",
        "to_address": "1SyntheticBtcAddress2222222222222",
        "amount": "0.50000000",
        "block_time": 1728864000,  # integer epoch seconds
        "confirmations": 6,
        "usd_value": "30000.00",
    }

    res = adapter.normalize(make_raw_event(payload))
    tx = res.transaction

    assert tx.amount.currency == "BTC"
    assert tx.amount.scale == 8
    assert tx.amount.normalized_value == Decimal("30000.00")
    assert tx.network_metadata["normalized_currency"] == "USD"

    # Approximations: initiation time and finality time approximated by block_time
    assert any("initiation time was approximated" in w.lower() for w in res.warnings)
    assert any("finality time was approximated" in w.lower() for w in res.warnings)


def test_crypto_determinism_on_repeated_calls() -> None:
    """Assert normalizing the identical raw event twice produces identical results."""
    adapter = CryptoAdapter()
    ev = make_raw_event(load_fixture("transfer_finalized.json"))

    res1 = adapter.normalize(ev)
    res2 = adapter.normalize(ev)

    assert res1.transaction.transaction_id == res2.transaction.transaction_id
    assert res1.correlation_key == res2.correlation_key
    assert res1.transaction.amount == res2.transaction.amount
    assert len(res1.parties) == len(res2.parties)
    for p1, p2 in zip(res1.parties, res2.parties, strict=True):
        assert p1.party_id == p2.party_id


def test_crypto_nft_erc721_transfer() -> None:
    """Assert ERC-721 NFT transfer normalizes with quantity 1, scale 0, and token_id in asset_id."""
    adapter = CryptoAdapter()
    payload = {
        "chain": "ethereum",
        "tx_hash": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "transfer_index": 0,
        "status": "included",
        "confirmations": 20,
        "finalized": True,
        "from_address": "0x1111111111111111111111111111111111111111",
        "to_address": "0x2222222222222222222222222222222222222222",
        "asset": {
            "type": "nft",
            "contract_address": "0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D",
            "token_id": "42",
            "symbol": "BAYC",
        },
        "token_standard": "erc721",
        "quantity": "1",
        "block_time": "2026-10-14T08:00:00Z",
    }
    res = adapter.normalize(make_raw_event(payload))
    tx = res.transaction

    assert tx.amount.amount == Decimal("1")
    assert tx.amount.scale == 0
    assert tx.amount.currency == "BAYC"
    assert (
        tx.network_metadata["asset_id"] == "ethereum:0xbc4ca0eda7647a8ab7c2061c2e118a18a936f13d:42"
    )
    assert tx.network_metadata["token_id"] == "42"
    assert tx.network_metadata["token_standard"] == "erc721"


def test_crypto_nft_erc1155_quantity_5() -> None:
    """Assert ERC-1155 multi-token transfer normalizes with quantity 5."""
    adapter = CryptoAdapter()
    payload = {
        "chain": "ethereum",
        "tx_hash": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "transfer_index": 0,
        "status": "included",
        "confirmations": 20,
        "finalized": True,
        "from_address": "0x1111111111111111111111111111111111111111",
        "to_address": "0x2222222222222222222222222222222222222222",
        "asset": {
            "type": "nft",
            "contract_address": "0x3333333333333333333333333333333333333333",
            "token_id": "999",
            "symbol": "ITEMS",
        },
        "token_standard": "erc1155",
        "quantity": "5",
        "block_time": "2026-10-14T08:00:00Z",
    }
    res = adapter.normalize(make_raw_event(payload))
    tx = res.transaction

    assert tx.amount.amount == Decimal("5")
    assert tx.amount.scale == 0
    assert tx.amount.currency == "ITEMS"
    assert tx.network_metadata["token_id"] == "999"


def test_crypto_nft_different_token_ids_yield_different_asset_ids() -> None:
    """Assert two different token_ids in the same collection yield different asset_ids."""
    adapter = CryptoAdapter()
    base_payload = {
        "chain": "ethereum",
        "tx_hash": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "transfer_index": 0,
        "status": "included",
        "confirmations": 20,
        "finalized": True,
        "from_address": "0x1111111111111111111111111111111111111111",
        "to_address": "0x2222222222222222222222222222222222222222",
        "asset": {
            "type": "nft",
            "contract_address": "0x4444444444444444444444444444444444444444",
            "token_id": "1",
        },
        "block_time": "2026-10-14T08:00:00Z",
    }

    res1 = adapter.normalize(make_raw_event(base_payload))

    payload2 = copy.deepcopy(base_payload)
    payload2["asset"]["token_id"] = "2"
    res2 = adapter.normalize(make_raw_event(payload2))

    assert (
        res1.transaction.network_metadata["asset_id"]
        != res2.transaction.network_metadata["asset_id"]
    )
    assert (
        res1.transaction.network_metadata["asset_id"]
        == "ethereum:0x4444444444444444444444444444444444444444:1"
    )
    assert (
        res2.transaction.network_metadata["asset_id"]
        == "ethereum:0x4444444444444444444444444444444444444444:2"
    )


def test_crypto_nft_amount_supplied_rejected() -> None:
    """Assert supplying amount or amount_raw for an NFT raises NormalizationError."""
    adapter = CryptoAdapter()
    payload = {
        "chain": "ethereum",
        "tx_hash": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "transfer_index": 0,
        "status": "included",
        "confirmations": 20,
        "finalized": True,
        "from_address": "0x1111111111111111111111111111111111111111",
        "to_address": "0x2222222222222222222222222222222222222222",
        "asset": {
            "type": "nft",
            "contract_address": "0x4444444444444444444444444444444444444444",
            "token_id": "1",
        },
        "amount": "1.0",
        "block_time": "2026-10-14T08:00:00Z",
    }
    with pytest.raises(NormalizationError) as exc_info:
        adapter.normalize(make_raw_event(payload))
    assert exc_info.value.field == "amount"

    payload2 = copy.deepcopy(payload)
    del payload2["amount"]
    payload2["amount_raw"] = "1"
    with pytest.raises(NormalizationError) as exc_info2:
        adapter.normalize(make_raw_event(payload2))
    assert exc_info2.value.field == "amount_raw"


def test_crypto_nft_quantity_zero_rejected() -> None:
    """Assert quantity 0 for an NFT transfer is rejected."""
    adapter = CryptoAdapter()
    payload = {
        "chain": "ethereum",
        "tx_hash": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "transfer_index": 0,
        "status": "included",
        "confirmations": 20,
        "finalized": True,
        "from_address": "0x1111111111111111111111111111111111111111",
        "to_address": "0x2222222222222222222222222222222222222222",
        "asset": {
            "type": "nft",
            "contract_address": "0x4444444444444444444444444444444444444444",
            "token_id": "1",
        },
        "quantity": "0",
        "block_time": "2026-10-14T08:00:00Z",
    }
    with pytest.raises(NormalizationError) as exc_info:
        adapter.normalize(make_raw_event(payload))
    assert exc_info.value.field == "quantity"


def test_crypto_nft_erc721_quantity_other_than_1_rejected() -> None:
    """Assert ERC-721 quantity other than 1 is rejected."""
    adapter = CryptoAdapter()
    payload = {
        "chain": "ethereum",
        "tx_hash": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "transfer_index": 0,
        "status": "included",
        "confirmations": 20,
        "finalized": True,
        "from_address": "0x1111111111111111111111111111111111111111",
        "to_address": "0x2222222222222222222222222222222222222222",
        "asset": {
            "type": "nft",
            "contract_address": "0x4444444444444444444444444444444444444444",
            "token_id": "1",
        },
        "token_standard": "erc721",
        "quantity": "2",
        "block_time": "2026-10-14T08:00:00Z",
    }
    with pytest.raises(NormalizationError) as exc_info:
        adapter.normalize(make_raw_event(payload))
    assert exc_info.value.field == "quantity"


def test_crypto_nft_consideration_in_metadata_only() -> None:
    """Assert consideration and sale_tx_hash live in metadata.

    usd_value populates normalized_value.
    """
    adapter = CryptoAdapter()
    payload = {
        "chain": "ethereum",
        "tx_hash": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "transfer_index": 0,
        "status": "included",
        "confirmations": 20,
        "finalized": True,
        "from_address": "0x1111111111111111111111111111111111111111",
        "to_address": "0x2222222222222222222222222222222222222222",
        "asset": {
            "type": "nft",
            "contract_address": "0x4444444444444444444444444444444444444444",
            "token_id": "10",
            "symbol": "ART",
        },
        "consideration": {
            "amount": "12.5",
            "symbol": "ETH",
            "usd_value": "35000.00",
        },
        "sale_tx_hash": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "block_time": "2026-10-14T08:00:00Z",
    }
    res = adapter.normalize(make_raw_event(payload))
    tx = res.transaction

    # Amount is the NFT quantity
    assert tx.amount.amount == Decimal("1")
    assert tx.amount.currency == "ART"
    assert tx.amount.scale == 0
    # Normalized USD value populated from consideration.usd_value
    assert tx.amount.normalized_value == Decimal("35000.00")
    # Consideration and sale_tx_hash recorded in metadata only
    assert tx.network_metadata["consideration"] == {
        "amount": "12.5",
        "symbol": "ETH",
        "usd_value": "35000.00",
    }
    assert (
        tx.network_metadata["sale_tx_hash"]
        == "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    )


def test_crypto_nft_mint_from_null_address_accepted() -> None:
    """Assert minting from EVM null address is accepted as an ordinary transfer."""
    adapter = CryptoAdapter()
    payload = {
        "chain": "ethereum",
        "tx_hash": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "transfer_index": 0,
        "status": "included",
        "confirmations": 20,
        "finalized": True,
        "from_address": "0x0000000000000000000000000000000000000000",
        "to_address": "0x2222222222222222222222222222222222222222",
        "asset": {
            "type": "nft",
            "contract_address": "0x4444444444444444444444444444444444444444",
            "token_id": "1",
        },
        "block_time": "2026-10-14T08:00:00Z",
    }
    res = adapter.normalize(make_raw_event(payload))
    assert res.transaction.status == TransactionStatus.CONFIRMED
    assert res.parties[0].identifiers[0].value == "0x0000000000000000000000000000000000000000"
