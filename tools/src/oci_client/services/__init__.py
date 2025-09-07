"""OCI services package."""

from .compute import ComputeService
from .identity import IdentityService
from .bastion import BastionService

__all__ = [
    "ComputeService",
    "IdentityService",
    "BastionService"
]