"""Ripple Payment Network Adapter Layer.

This package provides the plug-in architecture for translating rail-native payment
events into Ripple's universal domain model.
"""

from ripple.adapters.card import CARD_PROFILE, CardAdapter
from ripple.adapters.contract import (
    BatchResult,
    NetworkAdapter,
    NormalizationResult,
    PayloadFormat,
    RawEvent,
    deterministic_uuid,
)
from ripple.adapters.crypto import CRYPTO_PROFILE, CryptoAdapter
from ripple.adapters.errors import (
    AdapterError,
    AdapterRegistrationError,
    DuplicateAdapterError,
    NormalizationError,
    UnknownNetworkError,
    UnsupportedPayloadError,
)
from ripple.adapters.profile import (
    LedgerVisibility,
    NetworkCategory,
    NetworkProfile,
    OperatingCalendar,
)
from ripple.adapters.registry import (
    AdapterRegistry,
    PluginDiscoveryReport,
    discover_plugins,
    get_registry,
    load_enabled_adapters,
    register_adapter,
)
from ripple.adapters.swift import SWIFT_PROFILE, SwiftAdapter
from ripple.adapters.universal import UniversalAdapter
from ripple.adapters.upi import UPI_PROFILE, UpiAdapter

__all__ = [
    # Profile
    "NetworkCategory",
    "LedgerVisibility",
    "OperatingCalendar",
    "NetworkProfile",
    # Contract
    "PayloadFormat",
    "RawEvent",
    "NormalizationResult",
    "BatchResult",
    "NetworkAdapter",
    "deterministic_uuid",
    # Errors
    "AdapterError",
    "UnsupportedPayloadError",
    "NormalizationError",
    "UnknownNetworkError",
    "DuplicateAdapterError",
    "AdapterRegistrationError",
    # Registry
    "AdapterRegistry",
    "PluginDiscoveryReport",
    "get_registry",
    "register_adapter",
    "discover_plugins",
    "load_enabled_adapters",
    # Built-in Adapters
    "UniversalAdapter",
    "UpiAdapter",
    "UPI_PROFILE",
    "CardAdapter",
    "CARD_PROFILE",
    "CryptoAdapter",
    "CRYPTO_PROFILE",
    "SwiftAdapter",
    "SWIFT_PROFILE",
]
