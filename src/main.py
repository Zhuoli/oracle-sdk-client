#!/usr/bin/env python3
"""
Main demonstration script for OCI Python Client.
Demonstrates listing OKE cluster instances and ODO instances.
"""

import os
import sys
import logging
from typing import Optional
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.logging import RichHandler

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from oci_client.client import OCIClient
from oci_client.models import LifecycleState

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True)]
)

logger = logging.getLogger(__name__)
console = Console()


def get_config_from_env() -> tuple[str, str, str, Optional[str]]:
    """Get configuration from environment variables."""
    region = os.getenv("OCI_REGION", "us-phoenix-1")
    profile = os.getenv("OCI_PROFILE", "DEFAULT")
    compartment_id = os.getenv("OCI_COMPARTMENT_ID")
    config_file = os.getenv("OCI_CONFIG_FILE")  # Optional custom config file
    
    if not compartment_id:
        console.print("[red]Error: OCI_COMPARTMENT_ID environment variable is required[/red]")
        console.print("\nSet it with:")
        console.print("export OCI_COMPARTMENT_ID=ocid1.compartment.oc1..your-compartment-id")
        sys.exit(1)
    
    return region, profile, compartment_id, config_file


def display_oke_instances(client: OCIClient, compartment_id: str) -> None:
    """Display OKE cluster instances in a formatted table."""
    console.print("\n[bold blue]🚀 OKE Cluster Instances[/bold blue]")
    
    try:
        oke_instances = client.list_oke_instances(compartment_id=compartment_id)
        
        if not oke_instances:
            console.print("No OKE instances found in the compartment.")
            return
        
        # Create table
        table = Table(title=f"OKE Instances ({len(oke_instances)} found)")
        table.add_column("Cluster Name", style="cyan", no_wrap=True)
        table.add_column("Instance Name", style="magenta")
        table.add_column("Private IP", style="green")
        table.add_column("Shape", style="yellow")
        table.add_column("Availability Domain", style="blue")
        
        for instance in oke_instances:
            cluster_name = instance.cluster_name or "N/A"
            display_name = instance.display_name or instance.instance_id[:20] + "..."
            private_ip = instance.private_ip or "N/A"
            shape = instance.shape or "N/A"
            ad = instance.availability_domain.split(":")[-1] if instance.availability_domain else "N/A"
            
            table.add_row(cluster_name, display_name, private_ip, shape, ad)
        
        console.print(table)
        
        # Group by cluster for summary
        clusters = {}
        for instance in oke_instances:
            cluster = instance.cluster_name or "Unknown"
            if cluster not in clusters:
                clusters[cluster] = 0
            clusters[cluster] += 1
        
        console.print(f"\n[bold]Summary by Cluster:[/bold]")
        for cluster, count in clusters.items():
            console.print(f"  • {cluster}: {count} instances")
            
    except Exception as e:
        logger.error(f"Failed to list OKE instances: {e}")
        console.print(f"[red]Error listing OKE instances: {e}[/red]")


def display_odo_instances(client: OCIClient, compartment_id: str) -> None:
    """Display ODO instances in a formatted table."""
    console.print("\n[bold blue]🏗️  ODO Instances[/bold blue]")
    
    try:
        odo_instances = client.list_odo_instances(compartment_id=compartment_id)
        
        if not odo_instances:
            console.print("No ODO instances found in the compartment.")
            return
        
        # Create table
        table = Table(title=f"ODO Instances ({len(odo_instances)} found)")
        table.add_column("Display Name", style="cyan")
        table.add_column("Private IP", style="green")
        table.add_column("Shape", style="yellow")
        table.add_column("Availability Domain", style="blue")
        table.add_column("Instance ID", style="dim")
        
        for instance in odo_instances:
            display_name = instance.display_name or "N/A"
            private_ip = instance.private_ip or "N/A"
            shape = instance.shape or "N/A"
            ad = instance.availability_domain.split(":")[-1] if instance.availability_domain else "N/A"
            instance_id = instance.instance_id[:20] + "..." if instance.instance_id else "N/A"
            
            table.add_row(display_name, private_ip, shape, ad, instance_id)
        
        console.print(table)
        console.print(f"\n[bold]Total ODO instances: {len(odo_instances)}[/bold]")
        
    except Exception as e:
        logger.error(f"Failed to list ODO instances: {e}")
        console.print(f"[red]Error listing ODO instances: {e}[/red]")


def display_connection_info(client: OCIClient) -> None:
    """Display connection and configuration information."""
    console.print("[bold blue]🔗 Connection Information[/bold blue]")
    
    # Test connection
    if client.test_connection():
        console.print("[green]✓ Successfully connected to OCI[/green]")
    else:
        console.print("[red]✗ Failed to connect to OCI[/red]")
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


def main():
    """Main function to demonstrate OKE and ODO instance listing."""
    console.print("[bold green]🌟 OCI Python Client Demo - OKE & ODO Instances[/bold green]")
    console.print("This demo will list OKE cluster instances and ODO instances.\n")
    
    # Get configuration
    try:
        region, profile, compartment_id, config_file = get_config_from_env()
        
        console.print("[bold]Configuration:[/bold]")
        console.print(f"  • Region: {region}")
        console.print(f"  • Profile: {profile}")
        console.print(f"  • Compartment ID: {compartment_id[:30]}...")
        if config_file:
            console.print(f"  • Config File: {config_file}")
        
    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled by user.[/yellow]")
        return 1
    
    # Initialize OCI client
    try:
        console.print("\n[bold]Initializing OCI Client...[/bold]")
        
        client_kwargs = {
            "region": region,
            "profile_name": profile
        }
        if config_file:
            client_kwargs["config_file"] = config_file
            
        client = OCIClient(**client_kwargs)
        
    except Exception as e:
        logger.error(f"Failed to initialize OCI client: {e}")
        console.print(f"[red]Failed to initialize OCI client: {e}[/red]")
        console.print("\n[yellow]Make sure you have configured OCI authentication:[/yellow]")
        console.print("1. For session token: oci session authenticate --profile-name {profile} --region {region}")
        console.print("2. For API key: Set up your ~/.oci/config with API key details")
        return 1
    
    try:
        # Display connection info
        display_connection_info(client)
        
        # List OKE instances
        display_oke_instances(client, compartment_id)
        
        # List ODO instances  
        display_odo_instances(client, compartment_id)
        
        console.print("\n[bold green]✅ Demo completed successfully![/bold green]")
        
    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled by user.[/yellow]")
        return 1
    except Exception as e:
        logger.error(f"Demo failed: {e}")
        console.print(f"[red]Demo failed: {e}[/red]")
        return 1
    
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        console.print("\n[yellow]Program interrupted by user.[/yellow]")
        sys.exit(1)