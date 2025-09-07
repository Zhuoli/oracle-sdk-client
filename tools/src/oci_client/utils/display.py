"""
Display utilities for formatting and presenting OCI resources.
"""

from typing import List
from rich.console import Console
from rich.table import Table

from ..models import InstanceInfo, BastionInfo

console = Console()


def display_configuration_info(project_name: str, stage: str, config_file: str, region_count: int, region_compartments: dict) -> None:
    """Display configuration information."""
    console.print(f"[bold]Configuration:[/bold]")
    console.print(f"  • Project: {project_name}")
    console.print(f"  • Stage: {stage}")
    console.print(f"  • Config File: {config_file}")
    console.print(f"  • Regions Found: {region_count}")
    
    console.print("\n[bold]Region:Compartment Pairs:[/bold]")
    for region, compartment_id in region_compartments.items():
        console.print(f"  • [cyan]{region}[/cyan]: {compartment_id[:50]}...")


def display_region_header(region: str) -> None:
    """Display region processing header."""
    console.print(f"\n[bold blue]🌍 Processing Region: {region}[/bold blue]")


def display_session_token_header(profile_name: str) -> None:
    """Display session token creation header."""
    console.print(f"[bold blue]🔐 Creating Session Token for Profile '{profile_name}'...[/bold blue]")


def display_client_initialization(region: str) -> None:
    """Display client initialization message."""
    console.print(f"[bold]Initializing OCI Client for region {region}...[/bold]")


def display_oke_instances(region: str, instances: List[InstanceInfo]) -> None:
    """Display OKE instances in a formatted table."""
    console.print(f"\n[bold cyan]🚀 OKE Instances in {region}[/bold cyan]")
    
    if not instances:
        console.print(f"[dim]No OKE instances found in {region}[/dim]")
        return
    
    console.print(f"[green]Found {len(instances)} OKE instances in {region}[/green]")
    
    # Display in table format
    table = Table(title=f"OKE Instances - {region}")
    table.add_column("Cluster", style="cyan")
    table.add_column("Instance", style="magenta")
    table.add_column("Private IP", style="green")
    table.add_column("Shape", style="yellow")
    
    for instance in instances[:5]:  # Show first 5 per region
        cluster_name = instance.cluster_name or "N/A"
        display_name = instance.display_name or instance.instance_id[:20] + "..."
        private_ip = instance.private_ip or "N/A"
        shape = instance.shape or "N/A"
        table.add_row(cluster_name, display_name, private_ip, shape)
    
    console.print(table)


def display_odo_instances(region: str, instances: List[InstanceInfo]) -> None:
    """Display ODO instances in a formatted table."""
    console.print(f"\n[bold cyan]🏗️  ODO Instances in {region}[/bold cyan]")
    
    if not instances:
        console.print(f"[dim]No ODO instances found in {region}[/dim]")
        return
    
    console.print(f"[green]Found {len(instances)} ODO instances in {region}[/green]")
    
    # Display in table format
    table = Table(title=f"ODO Instances - {region}")
    table.add_column("Display Name", style="cyan")
    table.add_column("Private IP", style="green")
    table.add_column("Shape", style="yellow")
    
    for instance in instances[:5]:  # Show first 5 per region
        display_name = instance.display_name or "N/A"
        private_ip = instance.private_ip or "N/A"
        shape = instance.shape or "N/A"
        table.add_row(display_name, private_ip, shape)
    
    console.print(table)


def display_bastions(region: str, bastions: List[BastionInfo]) -> None:
    """Display bastions in a formatted table."""
    console.print(f"\n[bold cyan]🛡️  Bastions in {region}[/bold cyan]")
    
    if not bastions:
        console.print(f"[dim]No bastions found in {region}[/dim]")
        return
    
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


def display_summary(region_count: int, oke_count: int, odo_count: int, bastion_count: int) -> None:
    """Display final summary statistics."""
    console.print(f"\n[bold green]📊 Summary:[/bold green]")
    console.print(f"  • Total regions processed: {region_count}")
    console.print(f"  • Total OKE instances found: {oke_count}")
    console.print(f"  • Total ODO instances found: {odo_count}")
    console.print(f"  • Total bastions found: {bastion_count}")


def display_session_token_examples() -> None:
    """Display session token management examples."""
    console.print("\n[bold blue]🔐 Session Token Management Examples:[/bold blue]")
    console.print("[dim]# Create session token for specific region and profile[/dim]")
    console.print("[cyan]client.create_session_token('my_profile', 'us-phoenix-1', 'bmc_operator_access')[/cyan]")
    console.print()
    console.print("[dim]# Create session token and switch client to use it[/dim]")
    console.print("[cyan]client.create_and_use_session_token('my_profile', 'us-phoenix-1')[/cyan]")
    console.print()
    console.print("[dim]# Equivalent OCI CLI command[/dim]")
    console.print("[yellow]oci session authenticate --profile-name my_profile --region us-phoenix-1 --tenancy-name bmc_operator_access[/yellow]")


def display_completion() -> None:
    """Display completion message."""
    console.print("\n[bold green]✅ Multi-region SSH sync completed successfully![/bold green]")


def display_error(message: str) -> None:
    """Display error message."""
    console.print(f"[red]{message}[/red]")


def display_warning(message: str) -> None:
    """Display warning message."""
    console.print(f"[yellow]{message}[/yellow]")


def display_success(message: str) -> None:
    """Display success message."""
    console.print(f"[green]{message}[/green]")