#!/usr/bin/env python3
"""
Main demonstration script for OCI Python Client.
Demonstrates listing OKE cluster instances and ODO instances.
"""

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
    
    # Hardcoded configuration values
    region = "us-phoenix-1"
    profile = "demo_profile"
    compartment_id = "ocid1.compartment.oc1..aaaaaaaexample123456789"  # Replace with actual compartment ID
    config_file = None
    
    console.print("[bold]Configuration:[/bold]")
    console.print(f"  • Region: {region}")
    console.print(f"  • Profile: {profile}")
    console.print(f"  • Compartment ID: {compartment_id[:30]}...")
    if config_file:
        console.print(f"  • Config File: {config_file}")
    
    # Create session token first
    console.print(f"\n[bold blue]🔐 Creating Session Token for Profile '{profile}'...[/bold blue]")
    try:
        # Initialize a temporary client to create session token
        temp_client = OCIClient(region=region, profile_name="DEFAULT")  # Use existing profile for token creation
        
        # Create session token for the demo profile
        token_success = temp_client.create_session_token(
            profile_name=profile,
            region_name=region,
            tenancy_name="bmc_operator_access"
        )
        
        if not token_success:
            console.print("[red]Failed to create session token. Using existing authentication...[/red]")
            profile = "DEFAULT"  # Fall back to DEFAULT profile
        else:
            console.print(f"[green]✓ Session token created successfully for profile '{profile}'![/green]")
            
    except Exception as e:
        console.print(f"[yellow]Warning: Could not create session token: {e}[/yellow]")
        console.print("[yellow]Falling back to DEFAULT profile...[/yellow]")
        profile = "DEFAULT"
    
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
        console.print(f"1. For session token: oci session authenticate --profile-name {profile} --region {region}")
        console.print("2. For API key: Set up your ~/.oci/config with API key details")
        return 1
    
    try:
        # Display connection info
        display_connection_info(client)
        
        # List OKE instances
        display_oke_instances(client, compartment_id)
        
        # List ODO instances  
        display_odo_instances(client, compartment_id)
        
        # Demonstrate session token functionality
        console.print("\n[bold blue]🔐 Session Token Management:[/bold blue]")
        
        if client.config.is_session_token_auth():
            console.print("[green]✓ Currently using session token authentication[/green]")
        else:
            console.print("[yellow]Currently using API key authentication[/yellow]")
        
        # Show session token creation examples
        console.print("\n[bold]Available Session Token Methods:[/bold]")
        console.print("[dim]# Create a new session token profile[/dim]")
        console.print("[cyan]client.create_session_token('my_profile', 'us-phoenix-1', 'bmc_operator_access')[/cyan]")
        console.print()
        console.print("[dim]# Create session token and switch client to use it[/dim]")
        console.print("[cyan]client.create_and_use_session_token('my_profile', 'us-phoenix-1')[/cyan]")
        console.print()
        console.print("[dim]# Equivalent OCI CLI command[/dim]")
        console.print("[yellow]oci session authenticate --profile-name my_profile --region us-phoenix-1 --tenancy-name bmc_operator_access[/yellow]")
        
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