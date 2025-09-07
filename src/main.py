#!/usr/bin/env python3
"""
Main demonstration script for OCI Python Client.
Demonstrates listing OKE cluster instances, ODO instances, and bastions.
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


def display_demo_header():
    """Display the demo introduction."""
    from rich.console import Console
    console = Console()
    
    console.print("[bold green]🌟 OCI Python Client Demo - OKE, ODO & Bastion Instances[/bold green]")
    console.print("This demo will list OKE cluster instances, ODO instances, and bastions using YAML configuration.\n")


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
    """Main function to demonstrate OKE, ODO instance and bastion listing with YAML configuration."""
    display_demo_header()
    
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
    
    for region, compartment_id in region_compartments.items():
        oke_instances, odo_instances, bastions = process_region(
            project_name, stage, region, compartment_id
        )
        
        # Aggregate results
        all_oke_instances.extend(oke_instances)
        all_odo_instances.extend(odo_instances)
        all_bastions.extend(bastions)
    
    # Display final summary
    display_summary(
        len(region_compartments), 
        len(all_oke_instances), 
        len(all_odo_instances), 
        len(all_bastions)
    )
    
    # Show examples
    display_session_token_examples()
    
    # Display completion
    display_completion()
    
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