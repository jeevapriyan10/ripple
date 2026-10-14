"""NetworkProfile definition for the SWIFT Correspondent Banking adapter."""

from ripple.adapters.profile import (
    LedgerVisibility,
    NetworkCategory,
    NetworkProfile,
    OperatingCalendar,
)
from ripple.domain.identity import IdentifierType
from ripple.domain.money import AssetClass
from ripple.domain.transaction import SettlementModel

SWIFT_PROFILE = NetworkProfile(
    network_id="swift",
    display_name="SWIFT Correspondent Banking",
    description=(
        "Cross-border correspondent banking wire transfers modeled on pacs.008 / MT103 "
        "customer credit transfer concepts and gpi tracking."
    ),
    category=NetworkCategory.WIRE,
    settlement_model=SettlementModel.CORRESPONDENT_CHAIN,
    supported_identifier_types=[
        IdentifierType.IBAN,
        IdentifierType.BIC,
        IdentifierType.BANK_ACCOUNT,
    ],
    supported_asset_classes=[AssetClass.FIAT],
    default_scale=2,
    supported_currencies=[],  # Empty list: any fiat ISO-4217 currency is supported
    # Indicative latency band: 300s (fast gpi corridor) to 259200s (3-day multi-hop)
    typical_settlement_seconds_min=300.0,
    typical_settlement_seconds_max=259200.0,
    is_reversible=False,  # Recall requires beneficiary cooperation, not an entitlement
    typical_reversal_window_seconds=None,
    intermediaries_visible=True,
    ledger_visibility=LedgerVisibility.PRIVATE,
    is_pseudonymous=False,
    operating_calendar=OperatingCalendar.BUSINESS_DAYS,
    operating_timezone="UTC",
    typical_fee_bps_range=None,  # Correspondent banking fees are flat deduction charges per hop
    regions=[],
    schema_version=1,
)
