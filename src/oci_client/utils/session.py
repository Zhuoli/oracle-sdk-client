"""
Session management utilities for OCI authentication.
"""

from typing import Optional
from rich.console import Console

from ..client import create_oci_session_token, OCIClient
from .display import display_session_token_header, display_error, display_warning, display_success

console = Console()


def create_profile_for_region(project_name: str, stage: str, region: str) -> str:
    """Generate profile name for a specific project, stage, and region."""
    return f"demo_{project_name}_{stage}_{region.replace('-', '_')}"


def setup_session_token(project_name: str, stage: str, region: str) -> str:
    """
    Create session token for a region and return the profile name to use.
    
    Returns:
        str: Profile name to use (either the created profile or fallback to DEFAULT)
    """
    target_profile = create_profile_for_region(project_name, stage, region)
    display_session_token_header(target_profile)
    
    try:
        # Create session token using standalone function (no client needed)
        token_success = create_oci_session_token(
            profile_name=target_profile,
            region_name=region,
            tenancy_name="bmc_operator_access"
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
        client = OCIClient(
            region=region,
            profile_name=profile_name
        )
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