"""NetworkProfile definition for the Cryptocurrency payment adapter."""

from ripple.adapters.profile import (
    LedgerVisibility,
    NetworkCategory,
    NetworkProfile,
    OperatingCalendar,
)
from ripple.domain.identity import IdentifierType
from ripple.domain.money import AssetClass
from ripple.domain.transaction import SettlementModel

CRYPTO_PROFILE = NetworkProfile(
    network_id="crypto",
    display_name="Cryptocurrency Networks",
    description=(
        "Decentralized blockchain value transfers across EVM, Bitcoin, Solana, Tron, "
        "and compatible layer-1/layer-2 distributed ledgers."
    ),
    category=NetworkCategory.CRYPTO,
    settlement_model=SettlementModel.BLOCK_CONFIRMATION,
    supported_identifier_types=[IdentifierType.CRYPTO_ADDRESS],
    supported_asset_classes=[AssetClass.CRYPTO],
    default_scale=18,
    supported_currencies=[],  # Empty list indicates any cryptographic asset symbol is accepted
    # Settlement band: 10s (fast chains like Solana) to 3600s (Bitcoin 6-confirmation depth).
    # Blends fast consensus chains and slow proof-of-work finality; see chain table for details.
    typical_settlement_seconds_min=10.0,
    typical_settlement_seconds_max=3600.0,
    is_reversible=False,
    typical_reversal_window_seconds=None,
    intermediaries_visible=False,
    ledger_visibility=LedgerVisibility.PUBLIC,
    is_pseudonymous=True,
    operating_calendar=OperatingCalendar.ALWAYS_ON,
    operating_timezone=None,
    typical_fee_bps_range=None,  # Gas and miner fees are not proportional to transferred value
    regions=[],
    schema_version=1,
)
