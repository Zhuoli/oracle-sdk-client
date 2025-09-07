#!/usr/bin/env python3
"""
Main demonstration script for OCI Python Client.
Demonstrates listing OKE cluster instances and ODO instances.
"""

import sys
import logging
import argparse
from typing import Optional, Dict
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.logging import RichHandler

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from oci_client.client import OCIClient, create_oci_session_token
from oci_client.models import LifecycleState
from oci_client.utils.yamler import get_region_compartment_pairs, ConfigNotFoundError

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True)]
)

logger = logging.getLogger(__name__)
console = Console()


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="OCI Python Client Demo - List OKE, ODO & Bastion instances",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py remote-observer dev
  python main.py today-all staging
  python main.py remote-observer prod
        """
    )
    
    parser.add_argument(
        'project_name',
        help='Project name (e.g., remote-observer, today-all)'
    )
    
    parser.add_argument(
        'stage', 
        help='Deployment stage (e.g., dev, staging, prod)'
    )
    
    parser.add_argument(
        '--config-file',
        default='meta.yaml',
        help='Path to the YAML configuration file (default: meta.yaml)'
    )
    
    return parser.parse_args()


def load_region_compartments(project_name: str, stage: str, config_file: str = 'meta.yaml') -> Dict[str, str]:
    """
    Load region:compartment_id pairs from the YAML configuration.
    
    Args:
        project_name: Project name from the YAML file
        stage: Stage name from the YAML file
        config_file: Path to the YAML configuration file
        
    Returns:
        Dict[str, str]: Dictionary with region as key and compartment_id as value
    """
    try:
        region_compartments = get_region_compartment_pairs(
            yaml_file_path=config_file,
            project_name=project_name,
            stage=stage
        )
        
        if not region_compartments:
            raise ValueError(f"No region:compartment_id pairs found for project '{project_name}' stage '{stage}'")
            
        return region_compartments
        
    except ConfigNotFoundError as e:
        console.print(f"[red]Configuration Error: {e}[/red]")
        sys.exit(1)
    except FileNotFoundError as e:
        console.print(f"[red]File Error: {e}[/red]")
        console.print(f"[yellow]Make sure the configuration file exists at: {config_file}[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected error loading configuration: {e}[/red]")
        sys.exit(1)



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
    """Main function to demonstrate OKE, ODO instance and bastion listing with YAML configuration."""
    console.print("[bold green]🌟 OCI Python Client Demo - OKE, ODO & Bastion Instances[/bold green]")
    console.print("This demo will list OKE cluster instances, ODO instances, and bastions using YAML configuration.\n")
    
    # Parse command line arguments
    args = parse_arguments()
    project_name = args.project_name
    stage = args.stage
    config_file = args.config_file
    
    # Load region:compartment_id pairs from YAML configuration
    console.print("[bold]Loading Configuration...[/bold]")
    region_compartments = load_region_compartments(project_name, stage, config_file)
    
    console.print(f"[bold]Configuration:[/bold]")
    console.print(f"  • Project: {project_name}")
    console.print(f"  • Stage: {stage}")
    console.print(f"  • Config File: {config_file}")
    console.print(f"  • Regions Found: {len(region_compartments)}")
    
    # Display found region:compartment pairs
    console.print("\n[bold]Region:Compartment Pairs:[/bold]")
    for region, compartment_id in region_compartments.items():
        console.print(f"  • [cyan]{region}[/cyan]: {compartment_id[:50]}...")
    
    # Process each region:compartment pair
    all_oke_instances = []
    all_odo_instances = []
    all_bastions = []
    
    for region, compartment_id in region_compartments.items():
        console.print(f"\n[bold blue]🌍 Processing Region: {region}[/bold blue]")
        
        # Create session token target_profile for this region
        target_profile = f"demo_{project_name}_{stage}_{region.replace('-', '_')}"
        console.print(f"[bold blue]🔐 Creating Session Token for Profile '{target_profile}'...[/bold blue]")
        
        try:
            # Create session token using standalone function (no client needed)
            token_success = create_oci_session_token(
                profile_name=target_profile,
                region_name=region,
                tenancy_name="bmc_operator_access"
            )
            
            if not token_success:
                console.print("[red]Failed to create session token. Using DEFAULT target_profile...[/red]")
                target_profile = "DEFAULT"  # Fall back to DEFAULT target_profile
                
        except Exception as e:
            console.print(f"[yellow]Warning: Could not create session token: {e}[/yellow]")
            console.print("[yellow]Falling back to DEFAULT target_profile...[/yellow]")
            target_profile = "DEFAULT"
        
        # Initialize OCI client for this region
        try:
            console.print(f"[bold]Initializing OCI Client for region {region}...[/bold]")
            
            client = OCIClient(
                region=region,
                profile_name=target_profile
            )
            
            # Display connection info for this region
            display_connection_info(client)
            
            # List OKE instances for this region/compartment
            console.print(f"\n[bold cyan]🚀 OKE Instances in {region}[/bold cyan]")
            try:
                oke_instances = client.list_oke_instances(compartment_id=compartment_id)
                if oke_instances:
                    all_oke_instances.extend(oke_instances)
                    console.print(f"[green]Found {len(oke_instances)} OKE instances in {region}[/green]")
                    
                    # Display in table format
                    table = Table(title=f"OKE Instances - {region}")
                    table.add_column("Cluster", style="cyan")
                    table.add_column("Instance", style="magenta")
                    table.add_column("Private IP", style="green")
                    table.add_column("Shape", style="yellow")
                    
                    for instance in oke_instances[:5]:  # Show first 5 per region
                        cluster_name = instance.cluster_name or "N/A"
                        display_name = instance.display_name or instance.instance_id[:20] + "..."
                        private_ip = instance.private_ip or "N/A"
                        shape = instance.shape or "N/A"
                        table.add_row(cluster_name, display_name, private_ip, shape)
                    
                    console.print(table)
                else:
                    console.print(f"[dim]No OKE instances found in {region}[/dim]")
            except Exception as e:
                console.print(f"[red]Error listing OKE instances in {region}: {e}[/red]")
            
            # List ODO instances for this region/compartment
            console.print(f"\n[bold cyan]🏗️  ODO Instances in {region}[/bold cyan]")
            try:
                odo_instances = client.list_odo_instances(compartment_id=compartment_id)
                if odo_instances:
                    all_odo_instances.extend(odo_instances)
                    console.print(f"[green]Found {len(odo_instances)} ODO instances in {region}[/green]")
                    
                    # Display in table format
                    table = Table(title=f"ODO Instances - {region}")
                    table.add_column("Display Name", style="cyan")
                    table.add_column("Private IP", style="green")
                    table.add_column("Shape", style="yellow")
                    
                    for instance in odo_instances[:5]:  # Show first 5 per region
                        display_name = instance.display_name or "N/A"
                        private_ip = instance.private_ip or "N/A"
                        shape = instance.shape or "N/A"
                        table.add_row(display_name, private_ip, shape)
                    
                    console.print(table)
                else:
                    console.print(f"[dim]No ODO instances found in {region}[/dim]")
            except Exception as e:
                console.print(f"[red]Error listing ODO instances in {region}: {e}[/red]")
            
            # List Bastions for this region/compartment
            console.print(f"\n[bold cyan]🛡️  Bastions in {region}[/bold cyan]")
            try:
                bastions = client.list_bastions(compartment_id=compartment_id)
                if bastions:
                    all_bastions.extend(bastions)
                    console.print(f"[green]Found {len(bastions)} bastions in {region}[/green]")
                    
                    # Display in table format
                    table = Table(title=f"Bastions - {region}")
                    table.add_column("Bastion Name", style="cyan")
                    table.add_column("Type", style="magenta")
                    table.add_column("Max Session TTL", style="yellow")
                    table.add_column("Lifecycle State", style="green")
                    table.add_column("Target Subnet", style="blue")
                    
                    for bastion in bastions[:5]:  # Show first 5 per region
                        bastion_name = bastion.bastion_name or "N/A"
                        bastion_type = bastion.bastion_type.value if bastion.bastion_type else "N/A"
                        max_ttl = f"{bastion.max_session_ttl // 3600}h" if bastion.max_session_ttl else "N/A"
                        lifecycle_state = bastion.lifecycle_state.value if bastion.lifecycle_state else "N/A"
                        target_subnet = bastion.target_subnet_id[:20] + "..." if bastion.target_subnet_id else "N/A"
                        
                        table.add_row(bastion_name, bastion_type, max_ttl, lifecycle_state, target_subnet)
                    
                    console.print(table)
                else:
                    console.print(f"[dim]No bastions found in {region}[/dim]")
            except Exception as e:
                console.print(f"[red]Error listing bastions in {region}: {e}[/red]")
                
        except Exception as e:
            logger.error(f"Failed to initialize OCI client for region {region}: {e}")
            console.print(f"[red]Failed to initialize OCI client for region {region}: {e}[/red]")
            console.print(f"[yellow]Make sure you have configured OCI authentication for region {region}[/yellow]")
            continue  # Continue with next region
    
    # Summary
    console.print(f"\n[bold green]📊 Summary:[/bold green]")
    console.print(f"  • Total regions processed: {len(region_compartments)}")
    console.print(f"  • Total OKE instances found: {len(all_oke_instances)}")
    console.print(f"  • Total ODO instances found: {len(all_odo_instances)}")
    console.print(f"  • Total bastions found: {len(all_bastions)}")
    
    # Demonstrate session token functionality
    console.print("\n[bold blue]🔐 Session Token Management Examples:[/bold blue]")
    console.print("[dim]# Create session token for specific region and target_profile[/dim]")
    console.print("[cyan]client.create_session_token('my_profile', 'us-phoenix-1', 'bmc_operator_access')[/cyan]")
    console.print()
    console.print("[dim]# Create session token and switch client to use it[/dim]")
    console.print("[cyan]client.create_and_use_session_token('my_profile', 'us-phoenix-1')[/cyan]")
    console.print()
    console.print("[dim]# Equivalent OCI CLI command[/dim]")
    console.print("[yellow]oci session authenticate --target_profile-name my_profile --region us-phoenix-1 --tenancy-name bmc_operator_access[/yellow]")
    
    console.print("\n[bold green]✅ Multi-region demo completed successfully![/bold green]")
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        console.print("\n[yellow]Program interrupted by user.[/yellow]")
        sys.exit(1)