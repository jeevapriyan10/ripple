"""Static NetworkProfile specification for the UPI instant-payment rail."""

from ripple.adapters.profile import (
    LedgerVisibility,
    NetworkCategory,
    NetworkProfile,
    OperatingCalendar,
)
from ripple.domain.identity import IdentifierType
from ripple.domain.money import AssetClass
from ripple.domain.transaction import SettlementModel

UPI_PROFILE = NetworkProfile(
    network_id="upi",
    display_name="Unified Payments Interface (UPI)",
    description=(
        "India's instant real-time payment system developed by NPCI. Features instant "
        "gross settlement from the consumer perspective (though interbank settlement behind "
        "the scenes is periodically netted), multi-identifier addressing via VPAs, phone numbers, "
        "and account/IFSC pairs."
    ),
    category=NetworkCategory.INSTANT_PAYMENT,
    settlement_model=SettlementModel.INSTANT,
    supported_identifier_types=[
        IdentifierType.VPA,
        IdentifierType.BANK_ACCOUNT,
        IdentifierType.PHONE,
    ],
    supported_asset_classes=[AssetClass.FIAT],
    default_scale=2,
    supported_currencies=["INR"],
    typical_settlement_seconds_min=1.0,
    typical_settlement_seconds_max=30.0,
    is_reversible=False,
    typical_reversal_window_seconds=None,
    intermediaries_visible=False,
    ledger_visibility=LedgerVisibility.PRIVATE,
    is_pseudonymous=False,
    operating_calendar=OperatingCalendar.ALWAYS_ON,
    operating_timezone="Asia/Kolkata",
    typical_fee_bps_range=(0.0, 0.0),
    regions=["IN"],
    schema_version=1,
)
