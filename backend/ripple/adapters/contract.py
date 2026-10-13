"""Contract specifications, models, and abstract base class for network adapters.

Every payment network adapter must adhere to the contract defined in this module.
An adapter translates proprietary, network-native payloads (e.g. ISO 8583 card messages,
UPI JSON feeds, SWIFT MT103/ISO 20022 XML, crypto RPC transactions) into Ripple's
universal domain model.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar, Self
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ripple.adapters.errors import AdapterRegistrationError, NormalizationError
from ripple.adapters.profile import _NETWORK_ID_REGEX, NetworkProfile
from ripple.domain.party import Party
from ripple.domain.transaction import Transaction

# Fixed Ripple namespace UUID for deterministic UUIDv5 identifier generation
RIPPLE_NAMESPACE: UUID = UUID("7b3f9450-482d-41ae-811c-2ee289d0f1bb")


def deterministic_uuid(network_id: str, *parts: str) -> UUID:
    """Generate a stable, deterministic UUIDv5 using the fixed Ripple namespace.

    All payment adapters must derive party_id and transaction_id deterministically
    from the network_id and rail-native identifiers (e.g. VPA, IBAN, tx_ref).
    Using uuid4 is strictly prohibited because deterministic IDs guarantee idempotent
    graph ingestion, reproducible edge creation, and consistent deduplication across updates.
    """
    composite = ":".join([network_id, *parts])
    return uuid5(RIPPLE_NAMESPACE, composite)


class PayloadFormat(StrEnum):
    """Serialization format of incoming payment network raw payloads."""

    JSON = "JSON"
    XML = "XML"
    ISO8583 = "ISO8583"
    FIXED_WIDTH = "FIXED_WIDTH"
    OTHER = "OTHER"


class RawEvent(BaseModel):
    """Frozen input event wrapping a rail-native message received from a payment network.

    Captures the raw event bytes/dictionary alongside ingestion context. The received_at
    timestamp forms the single source of truth for the transaction's observed_at time.
    """

    model_config = ConfigDict(frozen=True)

    network_id: str = Field(
        ...,
        description="Target network identifier slug (e.g. 'upi', 'visa', 'universal').",
    )
    payload: dict[str, Any] | bytes | str = Field(
        ...,
        description="Raw native message content prior to normalization.",
    )
    payload_format: PayloadFormat = Field(
        ...,
        description="Encoding format of the payload (JSON, XML, ISO8583, etc.).",
    )
    received_at: datetime = Field(
        ...,
        description="Timezone-aware UTC timestamp when Ripple ingested this raw event.",
    )
    source: str | None = Field(
        default=None,
        description="Originating institution, ingestion queue, or messaging feed name.",
    )
    event_id: str | None = Field(
        default=None,
        description="Unique event or message identifier supplied by the ingestor.",
    )

    @field_validator("received_at")
    @classmethod
    def _validate_received_at(cls, v: datetime) -> datetime:
        """Enforce timezone-awareness and normalize to UTC."""
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("RawEvent received_at must be a timezone-aware UTC datetime.")
        return v.astimezone(UTC)


class NormalizationResult(BaseModel):
    """Frozen model representing the canonical output of an adapter normalization pass.

    Contains the normalized Transaction edge, all referenced Party nodes (deduplicated),
    the correlation key spanning multi-event lifecycles, and any non-fatal warnings.
    """

    model_config = ConfigDict(frozen=True)

    transaction: Transaction = Field(
        ...,
        description="The universal Transaction aggregate representing the payment edge.",
    )
    parties: list[Party] = Field(
        ...,
        description=(
            "Deduplicated list of Party aggregates involved, including sender and receiver."
        ),
    )
    correlation_key: str = Field(
        ...,
        description=(
            "A stable identifier shared by every raw event belonging to the same real-world "
            "transaction across its lifecycle stages (e.g. 'visa:auth_123' and 'visa:auth_123')."
        ),
    )
    lifecycle_stage: str = Field(
        ...,
        description=(
            "Adapter-defined label indicating the lifecycle stage of this event "
            "(e.g. 'authorization', 'capture', 'settlement', 'reversal')."
        ),
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal diagnostic notes or non-critical field conversion warnings.",
    )
    adapter_version: str = Field(
        ...,
        description="SemVer release string of the adapter that produced this result.",
    )

    @model_validator(mode="after")
    def _validate_parties_contain_endpoints(self) -> Self:
        """Validate that parties are deduplicated and contain sender and receiver entities."""
        party_ids = {p.party_id for p in self.parties}
        if len(self.parties) != len(party_ids):
            raise NormalizationError(
                "Parties list contains duplicate Party entities with identical party_id.",
                field="parties",
                reason="Duplicate party_id detected in parties list",
            )
        if self.transaction.sender_id not in party_ids:
            raise NormalizationError(
                f"Transaction sender_id '{self.transaction.sender_id}' not found in parties list.",
                field="parties",
                reason="Sender party missing from parties list",
            )
        if self.transaction.receiver_id not in party_ids:
            raise NormalizationError(
                f"Transaction receiver_id '{self.transaction.receiver_id}' missing in parties.",
                field="parties",
                reason="Receiver party missing from parties list",
            )
        return self


class BatchResult(BaseModel):
    """Frozen result model summarizing the outcome of a batch normalization operation.

    Isolates per-event failures to guarantee that a corrupt or malformed payload
    never aborts the normalization of surrounding valid transactions in a stream.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    successes: list[NormalizationResult] = Field(
        default_factory=list,
        description="Successfully normalized events.",
    )
    failures: list[tuple[RawEvent, Exception]] = Field(
        default_factory=list,
        description="Tuples of (failed_event, raised_exception) for dead-letter processing.",
    )

    @property
    def total_count(self) -> int:
        """Total number of events processed in this batch."""
        return len(self.successes) + len(self.failures)

    @property
    def success_count(self) -> int:
        """Count of successfully normalized events."""
        return len(self.successes)

    @property
    def failure_count(self) -> int:
        """Count of failed events in this batch."""
        return len(self.failures)

    @property
    def has_failures(self) -> bool:
        """Return True if any event in the batch failed normalization."""
        return len(self.failures) > 0


class NetworkAdapter(ABC):
    """Abstract base class that all payment network adapters must implement.

    Four Invariant Rules Every Adapter MUST Follow:
    ================================================
    1. Pure: No I/O operations (no network calls, no disk reads), and no system clock reads.
       Ingestion timestamps must be sourced exclusively from event.received_at.
    2. Deterministic: Identical inputs must generate identical outputs, including entity
       and transaction IDs. Derive party_id and transaction_id using deterministic_uuid()
       with uuid5 based on network_id + native identifiers. Never use uuid4.
    3. Never Silently Drop Data: Any native payload field that cannot be mapped directly
       into Ripple's universal domain schema must be preserved inside transaction.network_metadata
       or party.attributes.
    4. Raise Typed Errors: Raise NormalizationError or UnsupportedPayloadError when payloads
       are malformed. Never return partial garbage or default empty entities.
    """

    profile: ClassVar[NetworkProfile]
    adapter_version: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Enforce static class attributes on every NetworkAdapter subclass at definition time."""
        super().__init_subclass__(**kwargs)
        if kwargs.get("is_abstract", False):
            return

        profile = getattr(cls, "profile", None)
        if profile is None or not isinstance(profile, NetworkProfile):
            raise AdapterRegistrationError(
                f"Adapter subclass '{cls.__name__}' must define a class attribute 'profile' "
                "of type NetworkProfile."
            )

        if not profile.network_id or not _NETWORK_ID_REGEX.match(profile.network_id):
            raise AdapterRegistrationError(
                f"Adapter subclass '{cls.__name__}' has invalid profile.network_id "
                f"'{profile.network_id}'."
            )

        adapter_version = getattr(cls, "adapter_version", None)
        if not adapter_version or not isinstance(adapter_version, str):
            raise AdapterRegistrationError(
                f"Adapter subclass '{cls.__name__}' must define "
                "class attribute 'adapter_version' as a non-empty string."
            )

    @classmethod
    def from_settings(cls, settings: Any = None) -> Self:
        """Construct an adapter instance configured from application settings.

        Default implementation instantiates the adapter with no arguments.
        Subclasses may override this method to extract network-specific configuration
        (such as HMAC keys or routing endpoints) from settings.
        """
        return cls()

    @abstractmethod
    def can_handle(self, event: RawEvent) -> bool:
        """Perform a fast, cheap structural check to verify if this adapter can process the event.

        Must have no side effects, perform no I/O, and return False immediately if the event
        payload is incompatible.
        """
        pass

    @abstractmethod
    def normalize(self, event: RawEvent) -> NormalizationResult:
        """Transform a raw network-native event into Ripple's universal domain model.

        Must follow the four rules: pure, deterministic, no data loss, and typed errors.
        """
        pass

    def normalize_batch(self, events: Sequence[RawEvent]) -> BatchResult:
        """Normalize a sequence of raw events with isolated fault containment.

        Never allows a single bad event to abort the batch; captures failures individually
        and returns all successes alongside (event, error) failure tuples.
        """
        successes: list[NormalizationResult] = []
        failures: list[tuple[RawEvent, Exception]] = []

        for event in events:
            try:
                res = self.normalize(event)
                successes.append(res)
            except Exception as exc:
                failures.append((event, exc))

        return BatchResult(successes=successes, failures=failures)

    def describe(self) -> dict[str, Any]:
        """Return plain serializable dictionary describing profile and adapter version."""
        return {
            "profile": self.profile.model_dump(mode="json"),
            "adapter_version": self.adapter_version,
        }
