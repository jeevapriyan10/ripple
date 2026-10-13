"""UPI instant payment network adapter package."""

from ripple.adapters.upi.adapter import UpiAdapter
from ripple.adapters.upi.profile import UPI_PROFILE

__all__ = ["UPI_PROFILE", "UpiAdapter"]
