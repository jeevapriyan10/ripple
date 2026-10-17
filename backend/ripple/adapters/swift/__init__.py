"""SWIFT Correspondent Banking network adapter package."""

from ripple.adapters.swift.adapter import SwiftAdapter
from ripple.adapters.swift.profile import SWIFT_PROFILE

__all__ = [
    "SwiftAdapter",
    "SWIFT_PROFILE",
]
