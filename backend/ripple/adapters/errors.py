"""Exception hierarchy for the Ripple network adapter plug-in layer.

All adapter-level exceptions inherit from AdapterError, which in turn subclasses
RippleDomainError, allowing unified error handling across ingestion pipelines,
worker queues, and API endpoints.
"""

from typing import Any

from ripple.domain.errors import RippleDomainError


class AdapterError(RippleDomainError):
    """Base exception for all errors arising within the Ripple adapter layer."""

    pass


class UnsupportedPayloadError(AdapterError):
    """Raised when an adapter cannot parse or handle an incoming raw event at all.

    Typically occurs when the payload format (e.g. XML vs JSON), structure, or message
    type does not correspond to any known event specification on that rail.
    """

    pass


class NormalizationError(AdapterError):
    """Raised when an incoming event payload is recognized but fails validation.

    Indicates structural flaws in payload contents such as missing mandatory fields,
    malformed identifier tokens, invalid monetary numbers, or lifecycle state violations.
    Carries explicit field and reason attributes for diagnostics and dead-letter queue routing.
    """

    def __init__(
        self,
        message: str,
        field: str | None = None,
        reason: str = "",
        *args: Any,
    ) -> None:
        super().__init__(message, *args)
        self.field = field
        self.reason = reason or message


class UnknownNetworkError(AdapterError):
    """Raised when an operation references an unregistered payment network ID.

    Carries the requested network_id along with the list of currently registered
    available network IDs.
    """

    def __init__(
        self,
        message: str,
        network_id: str | None = None,
        available_ids: list[str] | None = None,
        *args: Any,
    ) -> None:
        super().__init__(message, *args)
        self.network_id = network_id
        self.available_ids = available_ids or []


class DuplicateAdapterError(AdapterError):
    """Raised when attempting to register an adapter whose network_id is already registered.

    Guarantees that adapters cannot be inadvertently clobbered at runtime without
    explicit replace=True consent.
    """

    def __init__(
        self,
        message: str,
        network_id: str | None = None,
        *args: Any,
    ) -> None:
        super().__init__(message, *args)
        self.network_id = network_id


class AdapterRegistrationError(AdapterError):
    """Raised when an adapter class fails contract validation during definition or registration.

    Enforced at class definition time by __init_subclass__ to prevent broken adapter
    classes from ever being instantiated or loaded.
    """

    pass
