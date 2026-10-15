"""NetworkProfile definition for the Closed-Loop Payment Wallet adapter."""

from ripple.adapters.profile import (
    LedgerVisibility,
    NetworkCategory,
    NetworkProfile,
    OperatingCalendar,
)
from ripple.domain.identity import IdentifierType
from ripple.domain.money import AssetClass
from ripple.domain.transaction import SettlementModel

WALLET_PROFILE = NetworkProfile(
    network_id="wallet",
    display_name="Closed-Loop Payment Wallet",
    description=("Proprietary closed-loop digital wallet network with internal ledger settlement."),
    category=NetworkCategory.WALLET,
    settlement_model=SettlementModel.INTERNAL_LEDGER,
    supported_identifier_types=[
        IdentifierType.WALLET_USER_ID,
        IdentifierType.EMAIL,
        IdentifierType.PHONE,
        IdentifierType.MERCHANT_ID,
        IdentifierType.OTHER,
    ],
    supported_asset_classes=[AssetClass.FIAT],
    default_scale=2,
    supported_currencies=[],  # Any fiat currency supported via shared currency table
    # Settlement band: 1s to 259200s (3 days).
    # Internal wallet transfers settle in seconds, while funding and withdrawal
    # follow the external rail's timing, so the band is deliberately coarse.
    typical_settlement_seconds_min=1.0,
    typical_settlement_seconds_max=259200.0,
    is_reversible=True,
    # Conventional simulation default (180 days), NOT authoritative; per-event override supported.
    typical_reversal_window_seconds=15552000,
    intermediaries_visible=False,
    ledger_visibility=LedgerVisibility.PRIVATE,
    is_pseudonymous=False,
    operating_calendar=OperatingCalendar.ALWAYS_ON,
    operating_timezone=None,
    typical_fee_bps_range=(0, 500),
    regions=[],
    schema_version=1,
)
