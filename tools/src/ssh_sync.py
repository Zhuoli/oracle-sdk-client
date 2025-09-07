#!/usr/bin/env python3
"""
OCI SSH Sync - SSH Configuration Generator for Oracle Cloud Infrastructure

This tool synchronizes SSH configurations by:
1. Discovering OKE and ODO instances across specified regions
2. Finding appropriate bastions for each instance
3. Generating SSH config entries with ProxyCommand for bastion access
4. Writing the configuration to an SSH config file

Usage:
    python ssh_sync.py <project_name> <stage> [--config-file meta.yaml]
"""

import sys
import logging
import argparse
from pathlib import Path
from rich.logging import RichHandler

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from oci_client.utils.config import load_region_compartments
from oci_client.utils.display import (
    display_configuration_info, display_region_header, display_client_initialization,
    display_oke_instances, display_odo_instances, display_bastions,
    display_summary, display_session_token_examples, display_completion
)
from oci_client.utils.session import setup_session_token, create_oci_client, display_connection_info
from oci_client.utils.resources import collect_all_resources
from oci_client.utils.ssh_config_generator import (
    generate_ssh_config_entries, write_ssh_config_file, display_ssh_config_summary
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True)]
)

logger = logging.getLogger(__name__)


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="OCI SSH Sync - Generate SSH config for OCI instances",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python ssh_sync.py remote-observer dev
  python ssh_sync.py today-all staging
  python ssh_sync.py remote-observer prod --config-file custom.yaml
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


def display_ssh_sync_header():
    """Display the SSH sync tool introduction."""
    from rich.console import Console
    console = Console()
    
    console.print("[bold green]🔧 OCI SSH Sync - SSH Configuration Generator[/bold green]")
    console.print("This tool generates SSH configurations for OKE and ODO instances using bastion ProxyCommands.\n")


def process_region(project_name: str, stage: str, region: str, compartment_id: str) -> tuple:
    """
    Process a single region and collect all resources.
    
    Returns:
        Tuple of (oke_instances, odo_instances, bastions) or ([], [], []) on failure
    """
    display_region_header(region)
    
    # Setup session token
    profile_name = setup_session_token(project_name, stage, region)
    
    # Create OCI client
    display_client_initialization(region)
    client = create_oci_client(region, profile_name)
    
    if not client:
        return [], [], []
    
    # Display connection info
    display_connection_info(client)
    
    # Collect all resources
    oke_instances, odo_instances, bastions = collect_all_resources(client, compartment_id, region)
    
    # Display resources
    display_oke_instances(region, oke_instances)
    display_odo_instances(region, odo_instances)
    display_bastions(region, bastions)
    
    return oke_instances, odo_instances, bastions


def main():
    """Main function to generate SSH configuration for OKE and ODO instances with YAML configuration."""
    display_ssh_sync_header()
    
    # Parse command line arguments
    args = parse_arguments()
    project_name = args.project_name
    stage = args.stage
    config_file = args.config_file
    
    # Load region:compartment_id pairs from YAML configuration
    from rich.console import Console
    console = Console()
    console.print("[bold]Loading Configuration...[/bold]")
    
    region_compartments = load_region_compartments(project_name, stage, config_file)
    
    display_configuration_info(project_name, stage, config_file, len(region_compartments), region_compartments)
    
    # Process each region:compartment pair
    all_oke_instances = []
    all_odo_instances = []
    all_bastions = []
    region_data = []  # For SSH config generation
    
    for region, compartment_id in region_compartments.items():
        oke_instances, odo_instances, bastions = process_region(
            project_name, stage, region, compartment_id
        )
        
        # Aggregate results
        all_oke_instances.extend(oke_instances)
        all_odo_instances.extend(odo_instances)
        all_bastions.extend(bastions)
        
        # Store region data for SSH config generation
        if oke_instances or odo_instances:
            region_data.append({
                'region': region,
                'compartment_id': compartment_id,
                'oke_instances': oke_instances,
                'odo_instances': odo_instances,
                'bastions': bastions
            })
    
    # Display final summary
    display_summary(
        len(region_compartments), 
        len(all_oke_instances), 
        len(all_odo_instances), 
        len(all_bastions)
    )
    
    # Generate SSH config if we have instances
    if region_data:
        console.print("\n[bold blue]🔧 Generating SSH Config...[/bold blue]")
        all_ssh_entries = []
        
        for data in region_data:
            # Create a client for this region to generate SSH config
            profile_name = setup_session_token(project_name, stage, data['region'])
            client = create_oci_client(data['region'], profile_name)
            
            if client:
                ssh_entries = generate_ssh_config_entries(
                    client=client,
                    oke_instances=data['oke_instances'],
                    odo_instances=data['odo_instances'],
                    bastions=data['bastions'],
                    compartment_id=data['compartment_id'],
                    project_name=project_name,
                    stage=stage,
                    region=data['region']
                )
                all_ssh_entries.extend(ssh_entries)
        
        if all_ssh_entries:
            # Display SSH config summary
            display_ssh_config_summary(all_ssh_entries)
            
            # Write SSH config file to ssh_configs directory
            ssh_config_filename = f"../ssh_configs/ssh_config_{project_name}_{stage}.txt"
            write_ssh_config_file(all_ssh_entries, ssh_config_filename, project_name, stage)
        else:
            console.print("[yellow]No SSH config entries could be generated[/yellow]")
    
    # Display completion message
    console.print("\n[bold green]✅ SSH Configuration Sync Complete![/bold green]")
    if region_data and all_ssh_entries:
        ssh_config_filename = f"ssh_configs/ssh_config_{project_name}_{stage}.txt"
        console.print(f"[green]SSH config saved to: {ssh_config_filename}[/green]")
        console.print("[dim]You can now use these SSH configurations to connect to your OCI instances.[/dim]")
    
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        from rich.console import Console
        console = Console()
        console.print("\n[yellow]Program interrupted by user.[/yellow]")
        sys.exit(1)