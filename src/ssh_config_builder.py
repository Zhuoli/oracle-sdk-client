#!/usr/bin/env python3
"""
SSH Config Builder using OCI Python Client.
Generates SSH config entries for OKE and ODO instances with bastion proxy commands.
"""

import sys
import argparse
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from rich.console import Console
from rich.table import Table

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from oci_client.client import OCIClient
from oci_client.models import LifecycleState, BastionType

console = Console()


class SSHConfigBuilder:
    """Build SSH config entries for OCI instances."""
    
    def __init__(self, region: str, compartment_id: str, profile_name: str, stage: str = "dev", realm: str = "oc1"):
        """Initialize SSH Config Builder."""
        self.region = region
        self.compartment_id = compartment_id
        self.profile_name = profile_name
        self.stage = stage
        self.realm = realm
        self.client = None
        self.internal_domain = None
        self.region_key = None
        
    def authenticate(self) -> bool:
        """Authenticate with OCI using session token."""
        try:
            # Run oci session authenticate
            cmd = [
                "oci", "session", "authenticate",
                "--profile-name", self.profile_name,
                "--region", self.region.lower(),
                "--tenancy-name", "bmc_operator_access"
            ]
            
            console.print(f"[yellow]Authenticating with OCI for region {self.region}...[/yellow]")
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                console.print(f"[red]Authentication failed: {result.stderr}[/red]")
                return False
            
            # Initialize OCI client
            self.client = OCIClient(
                region=self.region,
                profile_name=self.profile_name
            )
            
            # Test connection
            if not self.client.test_connection():
                return False
            
            # Get region info
            region_info = self.client.get_region_info()
            self.region_key = region_info.key
            
            # Get internal domain
            self.internal_domain = self.client.get_internal_domain()
            if not self.internal_domain:
                console.print("[red]Failed to get internal domain from StoreKeeper[/red]")
                return False
            
            console.print(f"[green]✓ Authenticated successfully[/green]")
            console.print(f"  Region key: {self.region_key}")
            console.print(f"  Internal domain: {self.internal_domain}")
            
            return True
            
        except Exception as e:
            console.print(f"[red]Authentication error: {e}[/red]")
            return False
    
    def generate_proxy_command(self) -> str:
        """Generate the ProxyCommand for SSH config."""
        return (
            f"ossh proxy -u %r --overlay-bastion --region {self.region} "
            f"--compartment {self.compartment_id} -- ssh -A -p 22 "
            f"ztb-internal.bastion.{self.region}.oci.{self.internal_domain} -s proxy:%h:%p"
        )
    
    def build_oke_config(
        self, 
        host_prefix: str = "today",
        cluster_name: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """Build SSH config entries for OKE instances."""
        console.print("\n[bold blue]Building OKE instance SSH config...[/bold blue]")
        
        # Get OKE instances
        oke_instances = self.client.list_oke_instances(
            compartment_id=self.compartment_id,
            cluster_name=cluster_name
        )
        
        if not oke_instances:
            console.print("[yellow]No OKE instances found[/yellow]")
            return []
        
        console.print(f"Found {len(oke_instances)} OKE instances")
        
        # Get bastions
        bastions = self.client.list_bastions(
            compartment_id=self.compartment_id,
            bastion_type=BastionType.INTERNAL
        )
        
        if not bastions:
            console.print("[red]No internal bastions found[/red]")
            return []
        
        # Build config entries
        config_entries = []
        proxy_command = self.generate_proxy_command()
        
        cluster_counts = {}
        
        for instance in oke_instances:
            # Find matching bastion
            bastion = self.client.find_bastion_for_subnet(bastions, instance.subnet_id)
            if not bastion:
                console.print(f"[yellow]No bastion found for instance {instance.instance_id}[/yellow]")
                continue
            
            # Track instance count per cluster
            cluster = instance.cluster_name or "default"
            if cluster not in cluster_counts:
                cluster_counts[cluster] = 0
            cluster_counts[cluster] += 1
            
            # Generate host entry
            host_name = f"{host_prefix}-{self.stage}-{self.region_key}-{self.realm}-{cluster_counts[cluster]}"
            hostname = f"{bastion.bastion_id}-{instance.private_ip}"
            
            config_entries.append({
                "host": host_name,
                "hostname": hostname,
                "proxy_command": proxy_command,
                "cluster": cluster,
                "instance_id": instance.instance_id
            })
            
            console.print(f"  [green]✓[/green] {host_name} -> {instance.private_ip}")
        
        return config_entries
    
    def build_odo_config(
        self,
        host_prefix: str = "today"
    ) -> List[Dict[str, str]]:
        """Build SSH config entries for ODO instances."""
        console.print("\n[bold blue]Building ODO instance SSH config...[/bold blue]")
        
        # Get ODO instances
        odo_instances = self.client.list_odo_instances(
            compartment_id=self.compartment_id
        )
        
        if not odo_instances:
            console.print("[yellow]No ODO instances found[/yellow]")
            return []
        
        console.print(f"Found {len(odo_instances)} ODO instances")
        
        # Get bastions
        bastions = self.client.list_bastions(
            compartment_id=self.compartment_id,
            bastion_type=BastionType.INTERNAL
        )
        
        if not bastions:
            console.print("[red]No internal bastions found[/red]")
            return []
        
        # Build config entries
        config_entries = []
        proxy_command = self.generate_proxy_command()
        
        for i, instance in enumerate(odo_instances):
            # Find matching bastion
            bastion = self.client.find_bastion_for_subnet(bastions, instance.subnet_id)
            if not bastion:
                console.print(f"[yellow]No bastion found for instance {instance.instance_id}[/yellow]")
                continue
            
            # Generate host entry
            host_name = f"odo-{host_prefix}-{self.stage}-{self.region_key}-{self.realm}-{i}"
            hostname = f"{bastion.bastion_id}-{instance.private_ip}"
            
            config_entries.append({
                "host": host_name,
                "hostname": hostname,
                "proxy_command": proxy_command,
                "instance_id": instance.instance_id,
                "display_name": instance.display_name
            })
            
            console.print(f"  [green]✓[/green] {host_name} -> {instance.private_ip}")
        
        return config_entries
    
    def write_config_file(
        self,
        config_entries: List[Dict[str, str]],
        output_file: str,
        update_existing: bool = True
    ) -> None:
        """Write SSH config entries to file."""
        output_path = Path(output_file)
        
        if update_existing and output_path.exists():
            # Read existing content
            existing_content = output_path.read_text()
            existing_hosts = self._parse_existing_hosts(existing_content)
            
            # Update or add entries
            for entry in config_entries:
                existing_hosts[entry["host"]] = entry
            
            # Write back all entries
            config_entries = list(existing_hosts.values())
        
        # Write config file
        with open(output_path, 'w') as f:
            for entry in config_entries:
                f.write(f"Host {entry['host']}\n")
                f.write(f"  HostName {entry['hostname']}\n")
                f.write(f"  ProxyCommand {entry['proxy_command']}\n")
                f.write("\n")
        
        console.print(f"\n[green]SSH config written to {output_file}[/green]")
        
        # Copy to clipboard (macOS)
        try:
            subprocess.run(["pbcopy"], input=output_path.read_text(), text=True, check=True)
            console.print("[green]✓ Config copied to clipboard[/green]")
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass  # pbcopy not available or failed
    
    def _parse_existing_hosts(self, content: str) -> Dict[str, Dict[str, str]]:
        """Parse existing SSH config content into dictionary."""
        hosts = {}
        current_host = None
        current_entry = {}
        
        for line in content.split('\n'):
            line = line.strip()
            if line.startswith('Host '):
                if current_host:
                    hosts[current_host] = current_entry
                current_host = line[5:].strip()
                current_entry = {"host": current_host}
            elif line.startswith('HostName '):
                current_entry["hostname"] = line[9:].strip()
            elif line.startswith('ProxyCommand '):
                current_entry["proxy_command"] = line[13:].strip()
        
        if current_host:
            hosts[current_host] = current_entry
        
        return hosts
    
    def display_summary(self, config_entries: List[Dict[str, str]]) -> None:
        """Display summary table of generated configs."""
        if not config_entries:
            return
        
        table = Table(title="Generated SSH Config Entries")
        table.add_column("Host", style="cyan")
        table.add_column("IP Address", style="magenta")
        table.add_column("Type", style="green")
        
        for entry in config_entries:
            # Extract IP from hostname (format: bastion_id-ip_address)
            ip = entry["hostname"].split('-')[-1] if '-' in entry["hostname"] else "N/A"
            entry_type = "ODO" if entry["host"].startswith("odo-") else "OKE"
            table.add_row(entry["host"], ip, entry_type)
        
        console.print("\n", table)


def main():
    """Main function for SSH config builder."""
    parser = argparse.ArgumentParser(
        description="Generate SSH config for OCI instances",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate OKE configs for dev environment
  python ssh_config_builder.py ocid1.compartment.oc1..xxxxx --type oke --stage dev
  
  # Generate ODO configs for production
  python ssh_config_builder.py ocid1.compartment.oc1..xxxxx --type odo --stage prod --region us-ashburn-1
  
  # Generate both OKE and ODO configs
  python ssh_config_builder.py ocid1.compartment.oc1..xxxxx --type both
  
  # Generate for specific OKE cluster
  python ssh_config_builder.py ocid1.compartment.oc1..xxxxx --type oke --cluster-name my-cluster
        """
    )
    
    parser.add_argument(
        "compartment_id",
        help="OCI compartment OCID"
    )
    
    parser.add_argument(
        "--type",
        choices=["oke", "odo", "both"],
        default="both",
        help="Type of instances to generate config for (default: both)"
    )
    
    parser.add_argument(
        "--stage",
        default="dev",
        help="Deployment stage (default: dev)"
    )
    
    parser.add_argument(
        "--region",
        default="us-phoenix-1",
        help="OCI region (default: us-phoenix-1)"
    )
    
    parser.add_argument(
        "--realm",
        default="oc1",
        help="OCI realm (default: oc1)"
    )
    
    parser.add_argument(
        "--host-prefix",
        default="today",
        help="Prefix for host names (default: today)"
    )
    
    parser.add_argument(
        "--cluster-name",
        help="Specific OKE cluster name to filter"
    )
    
    parser.add_argument(
        "--output",
        default="ssh_config_output.txt",
        help="Output file name (default: ssh_config_output.txt)"
    )
    
    parser.add_argument(
        "--profile",
        help="OCI profile name (default: ssh_builder for OKE, ssh_builder_odo for ODO)"
    )
    
    args = parser.parse_args()
    
    # Determine profile name
    if args.profile:
        profile_name = args.profile
    elif args.type == "odo":
        profile_name = "ssh_builder_odo"
    else:
        profile_name = "ssh_builder"
    
    # Create builder
    builder = SSHConfigBuilder(
        region=args.region,
        compartment_id=args.compartment_id,
        profile_name=profile_name,
        stage=args.stage,
        realm=args.realm
    )
    
    # Authenticate
    if not builder.authenticate():
        console.print("[red]Failed to authenticate with OCI[/red]")
        return 1
    
    # Generate configs
    all_configs = []
    
    if args.type in ["oke", "both"]:
        oke_configs = builder.build_oke_config(
            host_prefix=args.host_prefix,
            cluster_name=args.cluster_name
        )
        all_configs.extend(oke_configs)
    
    if args.type in ["odo", "both"]:
        odo_configs = builder.build_odo_config(
            host_prefix=args.host_prefix
        )
        all_configs.extend(odo_configs)
    
    if not all_configs:
        console.print("[yellow]No configurations generated[/yellow]")
        return 0
    
    # Display summary
    builder.display_summary(all_configs)
    
    # Write to file
    builder.write_config_file(all_configs, args.output)
    
    console.print(f"\n[green]✅ SSH config generation complete![/green]")
    console.print(f"Generated {len(all_configs)} entries")
    
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        sys.exit(1)