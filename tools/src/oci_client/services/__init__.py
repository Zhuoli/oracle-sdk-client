"""OCI services package."""

from .bastion import BastionService
from .compute import ComputeService
from .identity import IdentityService

__all__ = ["ComputeService", "IdentityService", "BastionService"]
