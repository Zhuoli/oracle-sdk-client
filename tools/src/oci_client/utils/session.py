"""
Session management utilities for OCI authentication.
"""

import os
import time
from pathlib import Path
from typing import Optional

from rich.console import Console

try:
    import oci
except ImportError:
    oci = None

from ..client import OCIClient, create_oci_session_token
from .display import display_error, display_session_token_header, display_success, display_warning

console = Console()


def create_profile_for_region(project_name: str, stage: str, region: str) -> str:
    """Generate profile name for a specific project, stage, and region."""
    return f"ssh_sync_{project_name}_{stage}_{region.replace('-', '_')}"


def check_session_token_validity(profile_name: str, config_file_path: Optional[str] = None) -> bool:
    """
    Check if a session token for the given profile is still valid.

    Args:
        profile_name: Name of the OCI profile to check
        config_file_path: Optional path to OCI config file

    Returns:
        bool: True if session token exists and is still valid, False otherwise
    """
    if not oci:
        return False

    try:
        # Try to load the config for this profile
        config_path = config_file_path or str(Path.home() / ".oci" / "config")

        if not Path(config_path).exists():
            return False

        # Try to load the profile configuration
        config = oci.config.from_file(file_location=config_path, profile_name=profile_name)

        # Check if this is a session token profile (has security_token_file)
        if "security_token_file" not in config:
            return False

        token_file_path = Path(config["security_token_file"])

        # Check if the token file exists
        if not token_file_path.exists():
            return False

        # Check if the token file is not too old (session tokens typically expire after 1 hour)
        # We'll consider it valid if it's less than 50 minutes old to provide a buffer
        token_age_seconds = time.time() - token_file_path.stat().st_mtime
        max_age_seconds = 50 * 60  # 50 minutes

        if token_age_seconds > max_age_seconds:
            return False

        # Try to use the config to make a simple API call to verify it works
        try:
            identity_client = oci.identity.IdentityClient(config)
            # Make a simple API call to verify the token works
            identity_client.get_tenancy(config["tenancy"])
            return True
        except Exception:
            # If the API call fails, the token is probably expired or invalid
            return False

    except Exception:
        # If any step fails, assume the session token is not valid
        return False


def get_session_token_info(
    profile_name: str, config_file_path: Optional[str] = None
) -> Optional[dict]:
    """
    Get information about an existing session token.

    Returns:
        dict with token info or None if not found/invalid
    """
    if not oci:
        return None

    try:
        config_path = config_file_path or str(Path.home() / ".oci" / "config")
        if not Path(config_path).exists():
            return None

        config = oci.config.from_file(file_location=config_path, profile_name=profile_name)

        if "security_token_file" not in config:
            return None

        token_file_path = Path(config["security_token_file"])
        if not token_file_path.exists():
            return None

        token_age_seconds = time.time() - token_file_path.stat().st_mtime
        token_age_minutes = token_age_seconds / 60

        return {
            "profile_name": profile_name,
            "token_file": str(token_file_path),
            "age_minutes": token_age_minutes,
            "region": config.get("region", "unknown"),
        }

    except Exception:
        return None


def setup_session_token(project_name: str, stage: str, region: str) -> str:
    """
    Create or reuse session token for a region and return the profile name to use.
    Optimized to check for existing valid sessions before creating new ones.

    Returns:
        str: Profile name to use (either the existing/created profile or fallback to DEFAULT)
    """
    target_profile = create_profile_for_region(project_name, stage, region)

    # Check if we already have a valid session token for this profile
    if check_session_token_validity(target_profile):
        token_info = get_session_token_info(target_profile)
        if token_info:
            age_minutes = token_info["age_minutes"]
            display_success(
                f"✓ Using existing valid session token for profile '{target_profile}' (age: {age_minutes:.1f} minutes)"
            )
            return target_profile

    # If no valid session exists, create a new one
    display_session_token_header(target_profile)

    try:
        # Create session token using standalone function (no client needed)
        token_success = create_oci_session_token(
            profile_name=target_profile, region_name=region, tenancy_name="bmc_operator_access"
        )

        if not token_success:
            display_error("Failed to create session token. Using DEFAULT profile...")
            return "DEFAULT"  # Fall back to DEFAULT profile

        return target_profile

    except Exception as e:
        display_warning(f"Could not create session token: {e}")
        display_warning("Falling back to DEFAULT profile...")
        return "DEFAULT"


def create_oci_client(region: str, profile_name: str) -> Optional[OCIClient]:
    """
    Create and initialize OCI client for a specific region.

    Returns:
        OCIClient or None if initialization fails
    """
    try:
        client = OCIClient(region=region, profile_name=profile_name)
        return client

    except Exception as e:
        display_error(f"Failed to initialize OCI client for region {region}: {e}")
        display_warning(f"Make sure you have configured OCI authentication for region {region}")
        return None


def display_connection_info(client: OCIClient) -> None:
    """Display connection and configuration information."""
    console.print("[bold blue]🔗 Connection Information[/bold blue]")

    # Test connection
    if client.test_connection():
        display_success("✓ Successfully connected to OCI")
    else:
        display_error("✗ Failed to connect to OCI")
        return

    # Display config info
    config_file = client.config.config_file or "~/.oci/config (default)"
    console.print(f"[dim]Config file: {config_file}[/dim]")
    console.print(f"[dim]Profile: {client.config.profile_name}[/dim]")
    console.print(f"[dim]Region: {client.config.region}[/dim]")

    # Display auth type
    if client.config.is_session_token_auth():
        console.print("[dim]Auth type: Session Token[/dim]")
    else:
        console.print("[dim]Auth type: API Key[/dim]")
