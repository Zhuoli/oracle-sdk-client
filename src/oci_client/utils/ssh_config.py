"""
Utility functions for SSH config generation using OCI Python Client.
"""

from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import subprocess
from dataclasses import dataclass
from rich.console import Console

from ..client import OCIClient
from ..models import BastionType

console = Console()


@dataclass
class SSHConfigEntry:
    """Represents a single SSH config entry."""
    host: str
    hostname: str
    proxy_command: str
    metadata: Dict[str, Any] = None
    
    def to_config_text(self) -> str:
        """Convert to SSH config text format."""
        return f"Host {self.host}\n  HostName {self.hostname}\n  ProxyCommand {self.proxy_command}\n"


class SSHConfigGenerator:
    """Generate SSH config entries for OCI resources."""
    
    def __init__(self, client: OCIClient):
        """Initialize with OCI client."""
        self.client = client
        self.region_info = client.get_region_info()
        self.internal_domain = client.get_internal_domain()
        
    def generate_proxy_command(self, compartment_id: str) -> str:
        """Generate standard ProxyCommand for bastion access."""
        return (
            f"ossh proxy -u %r --overlay-bastion --region {self.client.config.region} "
            f"--compartment {compartment_id} -- ssh -A -p 22 "
            f"ztb-internal.bastion.{self.client.config.region}.oci.{self.internal_domain} "
            f"-s proxy:%h:%p"
        )
    
    def generate_oke_entries(
        self,
        compartment_id: str,
        stage: str = "dev",
        realm: str = "oc1",
        host_prefix: str = "today",
        cluster_name: Optional[str] = None
    ) -> List[SSHConfigEntry]:
        """Generate SSH config entries for OKE instances."""
        entries = []
        
        # Get OKE instances
        instances = self.client.list_oke_instances(compartment_id, cluster_name)
        if not instances:
            return entries
        
        # Get bastions
        bastions = self.client.list_bastions(compartment_id, BastionType.INTERNAL)
        if not bastions:
            console.print("[yellow]Warning: No internal bastions found[/yellow]")
            return entries
        
        # Generate proxy command
        proxy_command = self.generate_proxy_command(compartment_id)
        
        # Group instances by cluster
        clusters = {}
        for instance in instances:
            cluster = instance.cluster_name or "default"
            if cluster not in clusters:
                clusters[cluster] = []
            clusters[cluster].append(instance)
        
        # Generate entries
        for cluster, cluster_instances in clusters.items():
            for idx, instance in enumerate(cluster_instances, 1):
                bastion = self.client.find_bastion_for_subnet(bastions, instance.subnet_id)
                if not bastion:
                    continue
                
                host = f"{host_prefix}-{stage}-{self.region_info.key}-{realm}-{idx}"
                hostname = f"{bastion.bastion_id}-{instance.private_ip}"
                
                entry = SSHConfigEntry(
                    host=host,
                    hostname=hostname,
                    proxy_command=proxy_command,
                    metadata={
                        "type": "oke",
                        "cluster": cluster,
                        "instance_id": instance.instance_id,
                        "private_ip": instance.private_ip
                    }
                )
                entries.append(entry)
        
        return entries
    
    def generate_odo_entries(
        self,
        compartment_id: str,
        stage: str = "dev",
        realm: str = "oc1",
        host_prefix: str = "today"
    ) -> List[SSHConfigEntry]:
        """Generate SSH config entries for ODO instances."""
        entries = []
        
        # Get ODO instances
        instances = self.client.list_odo_instances(compartment_id)
        if not instances:
            return entries
        
        # Get bastions
        bastions = self.client.list_bastions(compartment_id, BastionType.INTERNAL)
        if not bastions:
            console.print("[yellow]Warning: No internal bastions found[/yellow]")
            return entries
        
        # Generate proxy command
        proxy_command = self.generate_proxy_command(compartment_id)
        
        # Generate entries
        for idx, instance in enumerate(instances):
            bastion = self.client.find_bastion_for_subnet(bastions, instance.subnet_id)
            if not bastion:
                continue
            
            host = f"odo-{host_prefix}-{stage}-{self.region_info.key}-{realm}-{idx}"
            hostname = f"{bastion.bastion_id}-{instance.private_ip}"
            
            entry = SSHConfigEntry(
                host=host,
                hostname=hostname,
                proxy_command=proxy_command,
                metadata={
                    "type": "odo",
                    "instance_id": instance.instance_id,
                    "display_name": instance.display_name,
                    "private_ip": instance.private_ip
                }
            )
            entries.append(entry)
        
        return entries


class SSHConfigManager:
    """Manage SSH config files."""
    
    @staticmethod
    def parse_config_file(file_path: str) -> Dict[str, SSHConfigEntry]:
        """Parse existing SSH config file into dictionary of entries."""
        entries = {}
        path = Path(file_path)
        
        if not path.exists():
            return entries
        
        current_host = None
        current_hostname = None
        current_proxy = None
        
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                
                if line.startswith('Host '):
                    # Save previous entry if exists
                    if current_host and current_hostname and current_proxy:
                        entries[current_host] = SSHConfigEntry(
                            host=current_host,
                            hostname=current_hostname,
                            proxy_command=current_proxy
                        )
                    
                    # Start new entry
                    current_host = line[5:].strip()
                    current_hostname = None
                    current_proxy = None
                    
                elif line.startswith('HostName '):
                    current_hostname = line[9:].strip()
                    
                elif line.startswith('ProxyCommand '):
                    current_proxy = line[13:].strip()
        
        # Save last entry
        if current_host and current_hostname and current_proxy:
            entries[current_host] = SSHConfigEntry(
                host=current_host,
                hostname=current_hostname,
                proxy_command=current_proxy
            )
        
        return entries
    
    @staticmethod
    def write_config_file(
        entries: List[SSHConfigEntry],
        file_path: str,
        update_existing: bool = True
    ) -> None:
        """Write SSH config entries to file."""
        path = Path(file_path)
        
        if update_existing and path.exists():
            # Merge with existing entries
            existing = SSHConfigManager.parse_config_file(file_path)
            
            # Update with new entries
            for entry in entries:
                existing[entry.host] = entry
            
            # Convert back to list
            entries = list(existing.values())
        
        # Write to file
        with open(path, 'w') as f:
            for entry in entries:
                f.write(entry.to_config_text())
                f.write("\n")
    
    @staticmethod
    def copy_to_clipboard(content: str) -> bool:
        """Copy content to clipboard (macOS only)."""
        try:
            subprocess.run(
                ["pbcopy"],
                input=content,
                text=True,
                check=True
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
    
    @staticmethod
    def update_or_add_entry(
        file_path: str,
        host: str,
        hostname: str,
        proxy_command: str
    ) -> None:
        """Update existing entry or add new one."""
        entries = SSHConfigManager.parse_config_file(file_path)
        
        entries[host] = SSHConfigEntry(
            host=host,
            hostname=hostname,
            proxy_command=proxy_command
        )
        
        SSHConfigManager.write_config_file(
            list(entries.values()),
            file_path,
            update_existing=False
        )