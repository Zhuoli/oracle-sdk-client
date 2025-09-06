#!/usr/bin/env python3
"""Example usage of the OCI Python client."""

import sys
import logging
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.logging import RichHandler

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.oci_client.client import OCIClient
from src.oci_client.models import LifecycleState, BastionType

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True)]
)

console = Console()


def main():
    """Main example function."""
    
    # Configuration
    REGION = "us-phoenix-1"
    PROFILE_NAME = "ssh_builder_odo"  # Your profile name
    COMPARTMENT_ID = "your-compartment-ocid"  # Replace with your compartment OCID
    
    try:
        # Initialize the client
        console.print("\n[bold blue]Initializing OCI Client...[/bold blue]")
        client = OCIClient(
            region=REGION,
            profile_name=PROFILE_NAME
        )
        
        # Test connection
        if not client.test_connection():
            console.print("[red]Failed to connect to OCI[/red]")
            return
        
        # Get region information
        console.print("\n[bold blue]Region Information:[/bold blue]")
        region_info = client.get_region_info()
        console.print(f"Region: {region_info.name}")
        console.print(f"Region Key: {region_info.key}")
        console.print(f"Is Home Region: {region_info.is_home_region}")
        
        # Check for internal domain (Oracle-specific)
        internal_domain = client.get_internal_domain()
        if internal_domain:
            console.print(f"Internal Domain: {internal_domain}")
        
        # List compartments
        console.print("\n[bold blue]Compartments:[/bold blue]")
        compartments = client.list_compartments(
            parent_compartment_id=COMPARTMENT_ID,
            include_root=True
        )
        
        table = Table(title="Compartments")
        table.add_column("Name", style="cyan")
        table.add_column("ID", style="magenta")
        table.add_column("State", style="green")
        
        for comp in compartments[:5]:  # Show first 5
            table.add_row(
                comp["name"],
                comp["id"][:30] + "...",
                comp["lifecycle_state"]
            )
        
        console.print(table)
        
        # List running instances
        console.print("\n[bold blue]Running Instances:[/bold blue]")
        instances = client.list_instances(
            compartment_id=COMPARTMENT_ID,
            lifecycle_state=LifecycleState.RUNNING
        )
        
        if instances:
            table = Table(title="Compute Instances")
            table.add_column("Display Name", style="cyan")
            table.add_column("Private IP", style="magenta")
            table.add_column("Shape", style="green")
            table.add_column("AD", style="yellow")
            
            for instance in instances[:10]:  # Show first 10
                table.add_row(
                    instance.display_name or "N/A",
                    instance.private_ip,
                    instance.shape or "N/A",
                    instance.availability_domain.split(":")[-1] if instance.availability_domain else "N/A"
                )
            
            console.print(table)
            console.print(f"Total instances: {len(instances)}")
        else:
            console.print("No running instances found")
        
        # List OKE instances
        console.print("\n[bold blue]OKE Cluster Instances:[/bold blue]")
        oke_instances = client.list_oke_instances(compartment_id=COMPARTMENT_ID)
        
        if oke_instances:
            table = Table(title="OKE Instances")
            table.add_column("Cluster", style="cyan")
            table.add_column("Instance", style="magenta")
            table.add_column("Private IP", style="green")
            
            for instance in oke_instances[:10]:  # Show first 10
                table.add_row(
                    instance.cluster_name or "N/A",
                    instance.display_name or instance.instance_id[:20] + "...",
                    instance.private_ip
                )
            
            console.print(table)
            console.print(f"Total OKE instances: {len(oke_instances)}")
        else:
            console.print("No OKE instances found")
        
        # List ODO instances
        console.print("\n[bold blue]ODO Instances:[/bold blue]")
        odo_instances = client.list_odo_instances(compartment_id=COMPARTMENT_ID)
        
        if odo_instances:
            console.print(f"Found {len(odo_instances)} ODO instances")
            for instance in odo_instances[:5]:
                console.print(f"  - {instance.display_name}: {instance.private_ip}")
        else:
            console.print("No ODO instances found")
        
        # List bastions
        console.print("\n[bold blue]Active Bastions:[/bold blue]")
        bastions = client.list_bastions(
            compartment_id=COMPARTMENT_ID,
            bastion_type=BastionType.INTERNAL
        )
        
        if bastions:
            table = Table(title="Bastions")
            table.add_column("Name", style="cyan")
            table.add_column("Type", style="magenta")
            table.add_column("Max TTL", style="green")
            
            for bastion in bastions:
                table.add_row(
                    bastion.bastion_name or "N/A",
                    bastion.bastion_type.value,
                    f"{bastion.max_session_ttl // 3600} hours"
                )
            
            console.print(table)
            
            # Example: Find bastion for a specific subnet
            if instances and bastions:
                first_instance = instances[0]
                matching_bastion = client.find_bastion_for_subnet(
                    bastions,
                    first_instance.subnet_id
                )
                
                if matching_bastion:
                    console.print(
                        f"\n[green]Found bastion '{matching_bastion.bastion_name}' "
                        f"for instance subnet[/green]"
                    )
        else:
            console.print("No active bastions found")
        
        # Demonstrate session token refresh (if using session tokens)
        if client.config.is_session_token_auth():
            console.print("\n[bold blue]Session Token Status:[/bold blue]")
            console.print("Using session token authentication")
            
            # You can refresh the token if needed
            # success = client.refresh_auth()
            # console.print(f"Token refresh: {'✓' if success else '✗'}")
        
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        logging.exception("An error occurred")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
