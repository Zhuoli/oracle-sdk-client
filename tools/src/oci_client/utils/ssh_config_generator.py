"""
SSH Config generation utilities for OCI SSH Sync tool.
"""

from pathlib import Path
from typing import Any, Dict, List

from rich.console import Console

from ..client import OCIClient
from ..models import BastionInfo, InstanceInfo

console = Console()


def generate_ssh_config_entries(
    client: OCIClient,
    oke_instances: List[InstanceInfo],
    odo_instances: List[InstanceInfo],
    bastions: List[BastionInfo],
    compartment_id: str,
    project_name: str,
    stage: str,
    region: str,
) -> List[Dict[str, str]]:
    """
    Generate SSH config entries for OKE and ODO instances.

    Args:
        client: Authenticated OCI client
        oke_instances: List of OKE instances
        odo_instances: List of ODO instances
        bastions: List of available bastions
        compartment_id: OCI compartment ID
        project_name: Project name from YAML config
        stage: Stage from YAML config
        region: Region name

    Returns:
        List of SSH config entry dictionaries
    """
    config_entries = []

    # Get region info for naming
    try:
        region_info = client.get_region_info()
        region_key = region_info.key
    except:
        # Fallback to extracting region key from region name
        region_key = region.split("-")[1][:3]  # e.g., us-phoenix-1 -> pho

    # Get internal domain for proxy command
    try:
        internal_domain = client.get_internal_domain()
    except:
        internal_domain = "oraclecloud.com"  # Fallback

    # Generate proxy command template
    proxy_command_template = (
        f"ossh proxy -u %r --overlay-bastion --region {region} "
        f"--compartment {compartment_id} -- ssh -A -p 22 "
        f"ztb-internal.bastion.{region}.oci.{internal_domain} -s proxy:%h:%p"
    )

    # Process OKE instances
    if oke_instances:
        console.print(
            f"[bold cyan]Generating SSH config for {len(oke_instances)} OKE instances[/bold cyan]"
        )
        cluster_counts = {}

        for instance in oke_instances:
            # Find matching bastion using intelligent selection
            bastion = client.find_bastion_for_subnet(
                bastions, instance.subnet_id, instance.instance_id
            )
            if not bastion:
                console.print(
                    f"[yellow]No bastion found for OKE instance {instance.instance_id}[/yellow]"
                )
                continue

            # Track instance count per cluster
            cluster = instance.cluster_name or "default"
            if cluster not in cluster_counts:
                cluster_counts[cluster] = 0
            cluster_counts[cluster] += 1

            # Generate host entry
            host_name = f"{project_name}-{stage}-{region_key}-oc1-{cluster_counts[cluster]}"
            hostname = f"{bastion.bastion_id}-{instance.private_ip}"

            config_entries.append(
                {
                    "host": host_name,
                    "hostname": hostname,
                    "proxy_command": proxy_command_template,
                    "type": "oke",
                    "cluster": cluster,
                    "instance_id": instance.instance_id,
                    "private_ip": instance.private_ip,
                    "region": region,
                }
            )

    # Process ODO instances
    if odo_instances:
        console.print(
            f"[bold cyan]Generating SSH config for {len(odo_instances)} ODO instances[/bold cyan]"
        )

        for i, instance in enumerate(odo_instances, 1):
            # Find matching bastion using intelligent selection
            bastion = client.find_bastion_for_subnet(
                bastions, instance.subnet_id, instance.instance_id
            )
            if not bastion:
                console.print(
                    f"[yellow]No bastion found for ODO instance {instance.instance_id}[/yellow]"
                )
                continue

            # Generate host entry
            host_name = f"odo-{project_name}-{stage}-{region_key}-oc1-{i}"
            hostname = f"{bastion.bastion_id}-{instance.private_ip}"

            config_entries.append(
                {
                    "host": host_name,
                    "hostname": hostname,
                    "proxy_command": proxy_command_template,
                    "type": "odo",
                    "instance_id": instance.instance_id,
                    "display_name": instance.display_name or "N/A",
                    "private_ip": instance.private_ip,
                    "region": region,
                }
            )

    return config_entries


def write_ssh_config_file(
    config_entries: List[Dict[str, str]],
    output_file: str = "ssh_config_output.txt",
    project_name: str = "",
    stage: str = "",
) -> None:
    """
    Write SSH config entries to file.

    Args:
        config_entries: List of config entry dictionaries
        output_file: Output file path
        project_name: Project name for header
        stage: Stage for header
    """
    if not config_entries:
        console.print("[yellow]No SSH config entries to write[/yellow]")
        return

    output_path = Path(output_file)

    # Create parent directory if it doesn't exist
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        # Write header
        f.write(f"# SSH Config for {project_name} ({stage})\n")
        f.write(f"# Generated by OCI SSH Sync\n")
        f.write(f"# Total entries: {len(config_entries)}\n\n")

        # Write entries
        for entry in config_entries:
            f.write(f"Host {entry['host']}\n")
            f.write(f"  HostName {entry['hostname']}\n")
            f.write(f"  ProxyCommand {entry['proxy_command']}\n")
            f.write(f"  # Type: {entry['type'].upper()}\n")
            f.write(f"  # Private IP: {entry['private_ip']}\n")
            f.write(f"  # Region: {entry['region']}\n")
            if entry["type"] == "oke" and "cluster" in entry:
                f.write(f"  # Cluster: {entry['cluster']}\n")
            f.write("\n")

    console.print(f"\n[bold green]✅ SSH config written to {output_file}[/bold green]")
    console.print(f"[green]Generated {len(config_entries)} SSH config entries[/green]")

    # Show summary by type
    oke_count = sum(1 for entry in config_entries if entry["type"] == "oke")
    odo_count = sum(1 for entry in config_entries if entry["type"] == "odo")

    console.print(f"[dim]  • OKE entries: {oke_count}[/dim]")
    console.print(f"[dim]  • ODO entries: {odo_count}[/dim]")


def display_ssh_config_summary(config_entries: List[Dict[str, str]]) -> None:
    """Display a summary table of SSH config entries."""
    if not config_entries:
        return

    from rich.table import Table

    table = Table(title="Generated SSH Config Entries")
    table.add_column("Host", style="cyan")
    table.add_column("Type", style="magenta")
    table.add_column("Private IP", style="green")
    table.add_column("Region", style="yellow")
    table.add_column("Cluster/Name", style="blue")

    for entry in config_entries:
        cluster_or_name = ""
        if entry["type"] == "oke" and "cluster" in entry:
            cluster_or_name = entry["cluster"]
        elif entry["type"] == "odo" and "display_name" in entry:
            cluster_or_name = entry["display_name"]

        table.add_row(
            entry["host"],
            entry["type"].upper(),
            entry["private_ip"],
            entry["region"],
            cluster_or_name,
        )

    console.print("\n", table)
