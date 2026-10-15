"""Thread-safe adapter registry, plugin discovery, and lifecycle management.

Maintains the runtime catalog of loaded payment network adapters, routes incoming
raw events to their designated adapters, and handles external plugin discovery via
setuptools / pyproject entry points.
"""

import importlib.metadata
import logging
import threading
from collections.abc import Callable
from typing import Any, TypeVar, overload

from pydantic import BaseModel, ConfigDict, Field

from ripple.adapters.contract import NetworkAdapter, RawEvent
from ripple.adapters.errors import (
    AdapterRegistrationError,
    DuplicateAdapterError,
    UnknownNetworkError,
)
from ripple.adapters.profile import NetworkProfile
from ripple.config import Settings, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=NetworkAdapter)


class PluginDiscoveryReport(BaseModel):
    """Diagnostic report detailing the outcome of external plugin discovery."""

    model_config = ConfigDict(frozen=True)

    loaded: list[str] = Field(
        default_factory=list,
        description="List of entry point names / network IDs successfully loaded and registered.",
    )
    failed: list[tuple[str, str]] = Field(
        default_factory=list,
        description="Tuples of (entry_point_name, error_reason) for plugins that failed to load.",
    )


class AdapterRegistry:
    """Thread-safe catalog of loaded payment network adapters.

    Provides registration, lookup, profile listing, and event routing.
    Guarantees thread-safe state mutations across multi-threaded ingestion workers.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._adapters: dict[str, NetworkAdapter] = {}
        self._failed_registrations: dict[str, str] = {}

    def register(
        self,
        adapter_cls_or_instance: type[NetworkAdapter] | NetworkAdapter,
        *,
        replace: bool = False,
        settings: Any = None,
    ) -> NetworkAdapter:
        """Register a NetworkAdapter instance or subclass into the registry.

        If a class is provided, it is instantiated via cls.from_settings(settings).
        Raises DuplicateAdapterError if an adapter with the same network_id exists
        unless replace=True is passed.
        """
        if isinstance(adapter_cls_or_instance, type) and issubclass(
            adapter_cls_or_instance, NetworkAdapter
        ):
            try:
                cfg = settings if settings is not None else get_settings()
                instance = adapter_cls_or_instance.from_settings(cfg)
            except Exception as exc:
                if isinstance(exc, AdapterRegistrationError):
                    raise
                raise AdapterRegistrationError(
                    f"Failed to instantiate adapter class "
                    f"'{adapter_cls_or_instance.__name__}': {exc}"
                ) from exc
        elif isinstance(adapter_cls_or_instance, NetworkAdapter):
            instance = adapter_cls_or_instance
        else:
            raise AdapterRegistrationError(
                f"Expected NetworkAdapter instance or subclass, got {type(adapter_cls_or_instance)}"
            )

        network_id = instance.profile.network_id.lower()

        with self._lock:
            if network_id in self._adapters and not replace:
                raise DuplicateAdapterError(
                    f"Adapter for network '{network_id}' is already registered. "
                    "Pass replace=True to overwrite an existing adapter.",
                    network_id=network_id,
                )
            self._adapters[network_id] = instance
            self._failed_registrations.pop(network_id, None)
            logger.info(
                "Registered network adapter for '%s' (v%s)",
                network_id,
                instance.adapter_version,
            )
            return instance

    def failed_registrations(self) -> dict[str, str]:
        """Return a mapping of network IDs that failed registration to error reasons."""
        with self._lock:
            return dict(self._failed_registrations)

    def unregister(self, network_id: str) -> NetworkAdapter | None:
        """Unregister and return the adapter for the given network_id, or None if not found."""
        clean_id = network_id.lower()
        with self._lock:
            removed = self._adapters.pop(clean_id, None)
            if removed:
                logger.info("Unregistered network adapter for '%s'", clean_id)
            return removed

    def get(self, network_id: str) -> NetworkAdapter:
        """Retrieve the adapter for network_id.

        Raises UnknownNetworkError listing available network IDs if not found.
        """
        clean_id = network_id.lower()
        with self._lock:
            if clean_id not in self._adapters:
                available = self.network_ids()
                raise UnknownNetworkError(
                    f"Network '{network_id}' is not registered. Available networks: {available}",
                    network_id=network_id,
                    available_ids=available,
                )
            return self._adapters[clean_id]

    def has(self, network_id: str) -> bool:
        """Check whether an adapter is registered for network_id."""
        clean_id = network_id.lower()
        with self._lock:
            return clean_id in self._adapters

    def list_profiles(self) -> list[NetworkProfile]:
        """Return all registered NetworkProfiles, sorted alphabetically by network_id."""
        with self._lock:
            return [self._adapters[nid].profile for nid in sorted(self._adapters.keys())]

    def network_ids(self) -> list[str]:
        """Return a sorted list of registered network_id slugs."""
        with self._lock:
            return sorted(self._adapters.keys())

    def route(self, event: RawEvent) -> NetworkAdapter:
        """Route a raw event to its designated adapter based strictly on event.network_id.

        Never attempts to guess or heuristically match payloads across adapters.
        """
        return self.get(event.network_id)

    def clear(self) -> None:
        """Remove all registered adapters. Intended primarily for isolated testing."""
        with self._lock:
            self._adapters.clear()
            self._failed_registrations.clear()


# Global default registry singleton
_default_registry = AdapterRegistry()


def get_registry() -> AdapterRegistry:
    """Return the global default AdapterRegistry instance."""
    return _default_registry


@overload
def register_adapter(
    adapter_cls_or_none: type[T],
    *,
    replace: bool = False,
    registry: AdapterRegistry | None = None,
) -> type[T]: ...


@overload
def register_adapter(
    adapter_cls_or_none: None = None,
    *,
    replace: bool = False,
    registry: AdapterRegistry | None = None,
) -> Callable[[type[T]], type[T]]: ...


def register_adapter(
    adapter_cls_or_none: type[T] | None = None,
    *,
    replace: bool = False,
    registry: AdapterRegistry | None = None,
) -> Any:
    """Decorator to register a NetworkAdapter subclass into the registry at import time.

    Supports both bare `@register_adapter` and `@register_adapter(replace=True)` syntax.
    """
    target_registry = registry if registry is not None else get_registry()

    def decorator(cls: type[T]) -> type[T]:
        target_registry.register(cls, replace=replace)
        return cls

    if adapter_cls_or_none is None:
        return decorator
    return decorator(adapter_cls_or_none)


def discover_plugins(registry: AdapterRegistry | None = None) -> PluginDiscoveryReport:
    """Discover and register external network adapters via the entry point group 'ripple.adapters'.

    Each entry point must resolve to a NetworkAdapter subclass. A plugin that fails
    to load or register is logged with a clear warning and skipped, never crashing the platform.
    """
    target_registry = registry if registry is not None else get_registry()
    loaded: list[str] = []
    failed: list[tuple[str, str]] = []

    try:
        eps = importlib.metadata.entry_points(group="ripple.adapters")
    except Exception as exc:
        logger.warning("Failed to query entry points for 'ripple.adapters': %s", exc)
        return PluginDiscoveryReport(loaded=[], failed=[("group_query", str(exc))])

    for ep in eps:
        name = ep.name
        try:
            plugin_cls = ep.load()
            if not (isinstance(plugin_cls, type) and issubclass(plugin_cls, NetworkAdapter)):
                msg = (
                    f"Entry point '{name}' resolved to {plugin_cls}, "
                    "which is not a NetworkAdapter subclass."
                )
                logger.warning(msg)
                failed.append((name, msg))
                continue

            target_registry.register(plugin_cls, replace=True)
            loaded.append(name)
            logger.info("Successfully discovered and loaded plugin adapter: %s", name)
        except Exception as exc:
            msg = f"Failed to load entry point '{name}': {exc}"
            logger.warning(msg, exc_info=True)
            failed.append((name, msg))

    return PluginDiscoveryReport(loaded=loaded, failed=failed)


def load_enabled_adapters(
    settings: Settings | None = None,
    registry: AdapterRegistry | None = None,
) -> None:
    """Import built-in adapters, discover plugins, and filter active networks per configuration.

    Applies the settings.ENABLED_NETWORKS configuration (comma-separated list of network slugs,
    or 'all' for all registered adapters). Unregistered IDs in ENABLED_NETWORKS log a warning.
    """
    cfg = settings if settings is not None else get_settings()
    reg = registry if registry is not None else get_registry()

    # 1. Import and register built-in adapters on target registry
    try:
        from ripple.adapters.universal import UniversalAdapter

        reg.register(UniversalAdapter, replace=True, settings=cfg)
    except Exception as exc:
        logger.error("Failed to register built-in universal adapter: %s", exc)
        reg._failed_registrations["universal"] = str(exc)

    try:
        from ripple.adapters.upi import UpiAdapter

        reg.register(UpiAdapter, replace=True, settings=cfg)
    except Exception as exc:
        logger.error("Failed to register built-in UPI adapter: %s", exc)
        reg._failed_registrations["upi"] = str(exc)

    try:
        from ripple.adapters.card import CardAdapter

        reg.register(CardAdapter, replace=True, settings=cfg)
    except Exception as exc:
        logger.error("Failed to register built-in card adapter: %s", exc)
        reg._failed_registrations["card"] = str(exc)

    try:
        from ripple.adapters.crypto import CryptoAdapter

        reg.register(CryptoAdapter, replace=True, settings=cfg)
    except Exception as exc:
        logger.error("Failed to register built-in crypto adapter: %s", exc)
        reg._failed_registrations["crypto"] = str(exc)

    try:
        from ripple.adapters.swift import SwiftAdapter

        reg.register(SwiftAdapter, replace=True, settings=cfg)
    except Exception as exc:
        logger.error("Failed to register built-in swift adapter: %s", exc)
        reg._failed_registrations["swift"] = str(exc)

    try:
        from ripple.adapters.wallet import WalletAdapter

        reg.register(WalletAdapter, replace=True, settings=cfg)
    except Exception as exc:
        logger.error("Failed to register built-in wallet adapter: %s", exc)
        reg._failed_registrations["wallet"] = str(exc)

    # 2. Run external plugin discovery
    discover_plugins(registry=reg)

    # 3. Filter adapters based on ENABLED_NETWORKS
    enabled_spec = cfg.ENABLED_NETWORKS.strip()
    if enabled_spec.lower() == "all":
        logger.info(
            "All payment network adapters enabled. Loaded: %s",
            reg.network_ids(),
        )
        return

    enabled_ids = {item.strip().lower() for item in enabled_spec.split(",") if item.strip()}

    current_ids = set(reg.network_ids())

    # Log warnings for specified IDs that were not registered
    for missing_id in enabled_ids - current_ids:
        logger.warning(
            "Network ID '%s' in ENABLED_NETWORKS is not registered in the adapter registry.",
            missing_id,
        )

    # Unregister any loaded adapter not in the enabled set
    for active_id in current_ids:
        if active_id not in enabled_ids:
            reg.unregister(active_id)

    logger.info(
        "Active payment network adapters after filtering: %s",
        reg.network_ids(),
    )
