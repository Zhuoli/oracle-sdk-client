"""
OCI SSH Sync - SSH Configuration Generator for Oracle Cloud Infrastructure

This tool synchronizes SSH configurations for OCI instances across regions,
generating SSH config entries with ProxyCommand for bastion-based access.
"""

__version__ = "1.0.0"
__author__ = "OCI SSH Sync Team"
__description__ = "SSH Configuration Generator for Oracle Cloud Infrastructure"

from .ssh_sync import main

__all__ = ["main"]
