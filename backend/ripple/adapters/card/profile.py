"""Static NetworkProfile specification for the payment card network rail."""

from ripple.adapters.profile import (
    LedgerVisibility,
    NetworkCategory,
    NetworkProfile,
    OperatingCalendar,
)
from ripple.domain.identity import IdentifierType
from ripple.domain.money import AssetClass
from ripple.domain.transaction import SettlementModel

CARD_PROFILE = NetworkProfile(
    network_id="card",
    display_name="Payment Card Network",
    description=(
        "Four-party payment card network rails (e.g. Visa, Mastercard, RuPay, Amex). "
        "Operates on a dual-message authorization and clearing/settlement model with visible "
        "intermediary institutional hops (acquirer, scheme, issuer) and a 120-day chargeback "
        "dispute liability window. Clearing operates on BUSINESS_DAYS while authorization is 24/7."
    ),
    category=NetworkCategory.CARD,
    settlement_model=SettlementModel.AUTH_CAPTURE,
    supported_identifier_types=[
        IdentifierType.CARD_PAN_TOKEN,
        IdentifierType.MERCHANT_ID,
    ],
    supported_asset_classes=[AssetClass.FIAT],
    default_scale=2,
    supported_currencies=[],  # Permissive: global card schemes support any currency
    typical_settlement_seconds_min=86400.0,  # 24 hours
    typical_settlement_seconds_max=259200.0,  # 72 hours (3 business days)
    is_reversible=True,
    typical_reversal_window_seconds=120 * 86400,  # 120 days
    intermediaries_visible=True,
    ledger_visibility=LedgerVisibility.PRIVATE,
    is_pseudonymous=True,
    operating_calendar=OperatingCalendar.BUSINESS_DAYS,
    operating_timezone="UTC",
    typical_fee_bps_range=(50.0, 350.0),  # 50 to 350 bps interchange / scheme fee
    regions=[],  # Global network
    schema_version=1,
)
