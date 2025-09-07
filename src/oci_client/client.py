"""Main OCI client module with optimized functionality."""

import logging
import subprocess
import webbrowser
import json
import time
from typing import Optional, List, Dict, Any, Union, Tuple
from functools import lru_cache
from pathlib import Path

import oci
import requests
from rich.console import Console
from tenacity import retry, stop_after_attempt, wait_exponential

from .auth import OCIAuthenticator
from .models import (
    OCIConfig, InstanceInfo, BastionInfo, SessionInfo,
    LifecycleState, BastionType, RegionInfo, AuthType
)

logger = logging.getLogger(__name__)
console = Console()


def create_oci_session_token(
    profile_name: str,
    region_name: str,
    tenancy_name: str = "bmc_operator_access",
    config_file_path: Optional[str] = None,
    timeout_minutes: int = 5
) -> bool:
    """
    Standalone function to create OCI session token without requiring an authenticated client.
    
    This function directly calls the OCI CLI to create session tokens and is equivalent to:
    oci session authenticate --profile-name $profile_name --region $region_name --tenancy-name $tenancy_name
    
    Args:
        profile_name: Name of the OCI profile to create/update
        region_name: OCI region name (e.g., 'us-phoenix-1', 'us-ashburn-1')
        tenancy_name: Tenancy name for authentication (default: 'bmc_operator_access')
        config_file_path: Optional custom path to OCI config file (defaults to ~/.oci/config)
        timeout_minutes: Timeout for the authentication process in minutes (default: 5)
        
    Returns:
        bool: True if session token was created successfully, False otherwise
    """
    try:
        # Check if OCI CLI is available
        result = subprocess.run(
            ["oci", "--version"], 
            capture_output=True, 
            text=True, 
            timeout=10
        )
        if result.returncode != 0:
            console.print("[red]OCI CLI not found. Please install it first: pip install oci-cli[/red]")
            return False
        
        console.print(f"[blue]Creating session token for profile '{profile_name}'...[/blue]")
        
        # Build the OCI session authenticate command
        cmd = [
            "oci", "session", "authenticate",
            "--profile-name", profile_name,
            "--region", region_name,
            "--tenancy-name", tenancy_name
        ]
        
        # Add custom config file if specified
        if config_file_path:
            cmd.extend(["--config-file", config_file_path])
        
        console.print(f"[dim]Running: {' '.join(cmd)}[/dim]")
        console.print("[yellow]This will open a web browser for authentication...[/yellow]")
        console.print("[yellow]Please complete the authentication in your browser.[/yellow]")
        
        # Run the authentication command interactively
        result = subprocess.run(
            cmd,
            timeout=timeout_minutes * 60,
            text=True
        )
        
        # Check if the command was successful
        if result.returncode == 0:
            console.print(f"[green]✓ Session token created successfully for profile '{profile_name}'![/green]")
            
            # Verify the session was created by checking if we can load the config
            try:
                config_path = Path(config_file_path) if config_file_path else Path.home() / ".oci" / "config"
                if config_path.exists():
                    test_config = oci.config.from_file(
                        file_location=str(config_path),
                        profile_name=profile_name
                    )
                    if test_config.get("security_token_file"):
                        console.print(
                            f"[dim]Session token file: {test_config['security_token_file']}[/dim]"
                        )
                
            except Exception as e:
                logger.warning(f"Could not verify session token creation: {e}")
            
            return True
        else:
            console.print(f"[red]✗ Failed to create session token. Exit code: {result.returncode}[/red]")
            return False
            
    except subprocess.TimeoutExpired:
        console.print(f"[red]✗ Session authentication timed out after {timeout_minutes} minutes[/red]")
        return False
    except FileNotFoundError:
        console.print("[red]OCI CLI not found. Please install it first: pip install oci-cli[/red]")
        return False
    except Exception as e:
        logger.error(f"Failed to create session token: {e}")
        console.print(f"[red]Error creating session token: {e}[/red]")
        return False


class OCIClient:
    """Enhanced OCI client with session token support and optimizations."""
    
    def __init__(
        self,
        region: str,
        profile_name: str = "DEFAULT",
        config_file: Optional[str] = None,
        retry_strategy: Optional[oci.retry.RetryStrategyBuilder] = None
    ):
        """
        Initialize OCI client with authentication and service clients.
        
        Args:
            region: OCI region name (e.g., 'us-phoenix-1')
            profile_name: OCI config profile name
            config_file: Optional path to config file (defaults to ~/.oci/config)
            retry_strategy: Optional retry strategy for API calls
        """
        self.config = OCIConfig(
            region=region, 
            profile_name=profile_name,
            config_file=config_file
        )
        self.authenticator = OCIAuthenticator(self.config)
        self.oci_config: Optional[Dict[str, Any]] = None
        self.signer: Optional[Any] = None
        
        # Setup retry strategy
        self.retry_strategy = retry_strategy or oci.retry.DEFAULT_RETRY_STRATEGY
        
        # Service clients will be initialized lazily
        self._compute_client: Optional[oci.core.ComputeClient] = None
        self._identity_client: Optional[oci.identity.IdentityClient] = None
        self._bastion_client: Optional[oci.bastion.BastionClient] = None
        self._network_client: Optional[oci.core.VirtualNetworkClient] = None
        
        # Authenticate
        self._authenticate()
    
    def _authenticate(self) -> None:
        """Authenticate with OCI."""
        try:
            self.oci_config, self.signer = self.authenticator.authenticate()
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            raise
    
    @property
    def compute_client(self) -> oci.core.ComputeClient:
        """Lazy-load compute client."""
        if not self._compute_client:
            self._compute_client = oci.core.ComputeClient(
                self.oci_config,
                signer=self.signer,
                retry_strategy=self.retry_strategy
            )
        return self._compute_client
    
    @property
    def identity_client(self) -> oci.identity.IdentityClient:
        """Lazy-load identity client."""
        if not self._identity_client:
            self._identity_client = oci.identity.IdentityClient(
                self.oci_config,
                signer=self.signer,
                retry_strategy=self.retry_strategy
            )
        return self._identity_client
    
    @property
    def bastion_client(self) -> oci.bastion.BastionClient:
        """Lazy-load bastion client."""
        if not self._bastion_client:
            self._bastion_client = oci.bastion.BastionClient(
                self.oci_config,
                signer=self.signer,
                retry_strategy=self.retry_strategy
            )
        return self._bastion_client
    
    @property
    def network_client(self) -> oci.core.VirtualNetworkClient:
        """Lazy-load network client."""
        if not self._network_client:
            self._network_client = oci.core.VirtualNetworkClient(
                self.oci_config,
                signer=self.signer,
                retry_strategy=self.retry_strategy
            )
        return self._network_client
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def test_connection(self) -> bool:
        """Test if the connection to OCI is working."""
        try:
            regions = self.identity_client.list_regions()
            console.print(
                f"[green]✓[/green] Connection test successful. "
                f"Found {len(regions.data)} regions."
            )
            return True
        except Exception as e:
            console.print(f"[red]✗[/red] Connection test failed: {e}")
            return False
    
    @lru_cache(maxsize=1)
    def get_region_info(self) -> RegionInfo:
        """Get information about the current region."""
        try:
            regions = self.identity_client.list_regions().data
            for region in regions:
                if region.name.lower() == self.config.region.lower():
                    # Get home region info
                    tenancy = self.identity_client.get_tenancy(
                        self.oci_config["tenancy"]
                    ).data
                    
                    return RegionInfo(
                        name=region.name,
                        key=region.key.lower(),
                        is_home_region=(region.name == tenancy.home_region_key)
                    )
            
            raise ValueError(f"Region {self.config.region} not found")
            
        except Exception as e:
            logger.error(f"Failed to get region info: {e}")
            raise RuntimeError(f"Failed to get region info: {e}")
    
    def get_internal_domain(self) -> Optional[str]:
        """Get the internal domain for the region (Oracle-specific)."""
        try:
            region_info = self.get_region_info()
            region_identifier = "R2" if region_info.key == "phx" else self.config.region
            
            # This endpoint is Oracle-internal
            response = requests.get(
                f"https://storekeeper.oci.oraclecorp.com/v1/regions/{region_identifier}",
                timeout=10,
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get("internal_realm_domain")
            
            return None
            
        except Exception as e:
            logger.warning(f"Could not get internal domain: {e}")
            return None
    
    def list_compartments(
        self,
        parent_compartment_id: str,
        include_root: bool = False
    ) -> List[Dict[str, Any]]:
        """List all compartments under a parent compartment."""
        try:
            compartments = []
            
            if include_root:
                root = self.identity_client.get_compartment(parent_compartment_id).data
                compartments.append({
                    "id": root.id,
                    "name": root.name,
                    "description": root.description,
                    "lifecycle_state": root.lifecycle_state
                })
            
            # List child compartments
            response = self.identity_client.list_compartments(
                parent_compartment_id,
                compartment_id_in_subtree=True,
                lifecycle_state=LifecycleState.ACTIVE.value
            )
            
            for comp in response.data:
                compartments.append({
                    "id": comp.id,
                    "name": comp.name,
                    "description": comp.description,
                    "lifecycle_state": comp.lifecycle_state
                })
            
            return compartments
            
        except Exception as e:
            logger.error(f"Failed to list compartments: {e}")
            raise RuntimeError(f"Failed to list compartments: {e}")
    
    def list_instances(
        self,
        compartment_id: str,
        lifecycle_state: Optional[LifecycleState] = None,
        availability_domain: Optional[str] = None
    ) -> List[InstanceInfo]:
        """List compute instances in a compartment."""
        try:
            instances = []
            
            # Build request kwargs
            kwargs = {"compartment_id": compartment_id}
            if lifecycle_state:
                kwargs["lifecycle_state"] = lifecycle_state.value
            if availability_domain:
                kwargs["availability_domain"] = availability_domain
            
            # List instances with pagination
            response = self.compute_client.list_instances(**kwargs)
            
            while response.data:
                for instance in response.data:
                    instance_info = self._parse_instance(compartment_id, instance)
                    if instance_info:
                        instances.append(instance_info)
                
                # Check for next page
                if response.has_next_page:
                    response = self.compute_client.list_instances(
                        **kwargs,
                        page=response.next_page
                    )
                else:
                    break
            
            return instances
            
        except Exception as e:
            logger.error(f"Failed to list instances: {e}")
            raise RuntimeError(f"Failed to list instances: {e}")
    
    def list_oke_instances(
        self,
        compartment_id: str,
        cluster_name: Optional[str] = None
    ) -> List[InstanceInfo]:
        """List OKE (Kubernetes) cluster instances."""
        all_instances = self.list_instances(
            compartment_id,
            lifecycle_state=LifecycleState.RUNNING
        )
        
        oke_instances = []
        for instance in all_instances:
            # Check for OKE metadata
            cluster_display_name = instance.metadata.get("oke-cluster-display-name")
            node_labels = instance.metadata.get("oke-initial-node-labels", {})
            
            if cluster_display_name and isinstance(node_labels, dict):
                if "tot.oraclecloud.com/node-pool-name" in node_labels:
                    # Filter by cluster name if specified
                    if cluster_name and cluster_display_name != cluster_name:
                        continue
                    
                    instance.cluster_name = cluster_display_name
                    oke_instances.append(instance)
        
        return sorted(oke_instances, key=lambda x: x.cluster_name or "")
    
    def list_odo_instances(
        self,
        compartment_id: str
    ) -> List[InstanceInfo]:
        """List ODO (Oracle Data Operations) instances."""
        all_instances = self.list_instances(
            compartment_id,
            lifecycle_state=LifecycleState.RUNNING
        )
        
        odo_instances = []
        for instance in all_instances:
            # Check for ODO metadata in extended_metadata
            extended_metadata = instance.metadata.get("extended_metadata", {})
            compute_mgmt = extended_metadata.get("compute_management", {})
            instance_config = compute_mgmt.get("instance_configuration", {})
            
            if instance_config.get("state") == "SUCCEEDED":
                odo_instances.append(instance)
        
        return odo_instances
    
    def list_bastions(
        self,
        compartment_id: str,
        bastion_type: Optional[BastionType] = BastionType.INTERNAL
    ) -> List[BastionInfo]:
        """List bastions in a compartment."""
        try:
            bastions = []
            
            kwargs = {
                "compartment_id": compartment_id,
                "lifecycle_state": LifecycleState.ACTIVE.value
            }
            
            if bastion_type:
                kwargs["bastion_type"] = bastion_type.value
            
            response = self.bastion_client.list_bastions(**kwargs)
            
            for bastion in response.data:
                bastions.append(
                    BastionInfo(
                        bastion_id=bastion.id,
                        target_subnet_id=bastion.target_subnet_id,
                        bastion_name=bastion.name,
                        bastion_type=BastionType(bastion.bastion_type),
                        max_session_ttl=bastion.max_session_ttl_in_seconds,
                        lifecycle_state=LifecycleState(bastion.lifecycle_state)
                    )
                )
            
            return bastions
            
        except Exception as e:
            logger.error(f"Failed to list bastions: {e}")
            raise RuntimeError(f"Failed to list bastions: {e}")
    
    def find_bastion_for_subnet(
        self,
        bastions: List[BastionInfo],
        subnet_id: str
    ) -> Optional[BastionInfo]:
        """Find a bastion that can access the given subnet."""
        for bastion in bastions:
            if bastion.target_subnet_id == subnet_id:
                return bastion
        return None
    
    def create_bastion_session(
        self,
        bastion_id: str,
        target_resource_id: str,
        target_private_ip: str,
        session_ttl: int = 10800,
        key_type: str = "PUB"
    ) -> SessionInfo:
        """Create a new bastion session."""
        try:
            details = oci.bastion.models.CreateSessionDetails(
                bastion_id=bastion_id,
                target_resource_details=oci.bastion.models.CreateManagedSshSessionTargetResourceDetails(
                    session_type="MANAGED_SSH",
                    target_resource_id=target_resource_id,
                    target_resource_private_ip_address=target_private_ip
                ),
                key_details=oci.bastion.models.PublicKeyDetails(
                    public_key_content=self._get_or_generate_ssh_key()
                ),
                session_ttl_in_seconds=session_ttl
            )
            
            response = self.bastion_client.create_session(details)
            session = response.data
            
            return SessionInfo(
                session_id=session.id,
                bastion_id=session.bastion_id,
                target_resource_id=target_resource_id,
                target_resource_private_ip=target_private_ip,
                ssh_metadata=session.ssh_metadata,
                lifecycle_state=LifecycleState(session.lifecycle_state)
            )
            
        except Exception as e:
            logger.error(f"Failed to create bastion session: {e}")
            raise RuntimeError(f"Failed to create bastion session: {e}")
    
    def _parse_instance(
        self,
        compartment_id: str,
        instance: Any
    ) -> Optional[InstanceInfo]:
        """Parse OCI instance object into InstanceInfo."""
        try:
            # Get VNIC information
            vnic_info = self._get_instance_vnic(compartment_id, instance.id)
            if not vnic_info:
                return None
            
            private_ip, public_ip, subnet_id = vnic_info
            
            # Parse metadata
            metadata = instance.metadata or {}
            extended_metadata = instance.extended_metadata or {}
            
            # Combine metadata
            all_metadata = {**metadata, "extended_metadata": extended_metadata}
            
            return InstanceInfo(
                instance_id=instance.id,
                display_name=instance.display_name,
                private_ip=private_ip,
                public_ip=public_ip,
                subnet_id=subnet_id,
                shape=instance.shape,
                availability_domain=instance.availability_domain,
                fault_domain=instance.fault_domain,
                metadata=all_metadata,
                tags={
                    **instance.freeform_tags,
                    **instance.defined_tags
                }
            )
            
        except Exception as e:
            logger.warning(f"Failed to parse instance {instance.id}: {e}")
            return None
    
    def _get_instance_vnic(
        self,
        compartment_id: str,
        instance_id: str
    ) -> Optional[Tuple[str, Optional[str], str]]:
        """Get VNIC information for an instance."""
        try:
            # List VNIC attachments
            vnics = self.compute_client.list_vnic_attachments(
                compartment_id=compartment_id,
                instance_id=instance_id
            ).data
            
            for vnic_attachment in vnics:
                if vnic_attachment.lifecycle_state == "ATTACHED":
                    # Get VNIC details
                    vnic = self.network_client.get_vnic(vnic_attachment.vnic_id).data
                    
                    if vnic.lifecycle_state == "AVAILABLE" and vnic.private_ip:
                        # Skip VNICs created by other services
                        if not vnic.freeform_tags.get("CreatedBy"):
                            return (
                                vnic.private_ip,
                                vnic.public_ip,
                                vnic.subnet_id
                            )
            
            return None
            
        except Exception as e:
            logger.warning(f"Failed to get VNIC for instance {instance_id}: {e}")
            return None
    
    def _get_or_generate_ssh_key(self) -> str:
        """Get existing SSH public key or generate a new one."""
        ssh_path = Path.home() / ".ssh"
        pub_key_path = ssh_path / "id_rsa.pub"
        
        if pub_key_path.exists():
            with open(pub_key_path, 'r') as f:
                return f.read().strip()
        
        # Generate new key pair if needed
        import subprocess
        priv_key_path = ssh_path / "id_rsa"
        
        if not priv_key_path.exists():
            ssh_path.mkdir(mode=0o700, exist_ok=True)
            subprocess.run([
                "ssh-keygen", "-t", "rsa", "-b", "2048",
                "-f", str(priv_key_path),
                "-N", ""  # No passphrase
            ], check=True)
        
        with open(pub_key_path, 'r') as f:
            return f.read().strip()
    
    def create_session_token(
        self,
        profile_name: str,
        region_name: str,
        tenancy_name: str = "bmc_operator_access",
        config_file_path: Optional[str] = None,
        timeout_minutes: int = 5
    ) -> bool:
        """
        Create a temporary session token for authenticating with Oracle Cloud Infrastructure.
        
        This method is equivalent to running:
        oci session authenticate --profile-name $profile_name --region $region_name --tenancy-name $tenancy_name
        
        Args:
            profile_name: Name of the OCI profile to create/update
            region_name: OCI region name (e.g., 'us-phoenix-1', 'us-ashburn-1')
            tenancy_name: Tenancy name for authentication (default: 'bmc_operator_access')
            config_file_path: Optional custom path to OCI config file (defaults to ~/.oci/config)
            timeout_minutes: Timeout for the authentication process in minutes (default: 5)
            
        Returns:
            bool: True if session token was created successfully, False otherwise
            
        Raises:
            RuntimeError: If the OCI CLI is not installed or authentication fails
            TimeoutError: If the authentication process times out
        """
        try:
            # Check if OCI CLI is available
            result = subprocess.run(
                ["oci", "--version"], 
                capture_output=True, 
                text=True, 
                timeout=10
            )
            if result.returncode != 0:
                raise RuntimeError(
                    "OCI CLI not found. Please install it first: pip install oci-cli"
                )
            
            console.print(f"[blue]Creating session token for profile '{profile_name}'...[/blue]")
            
            # Build the OCI session authenticate command
            cmd = [
                "oci", "session", "authenticate",
                "--profile-name", profile_name,
                "--region", region_name,
                "--tenancy-name", tenancy_name
            ]
            
            # Add custom config file if specified
            if config_file_path:
                cmd.extend(["--config-file", config_file_path])
            
            console.print(f"[dim]Running: {' '.join(cmd)}[/dim]")
            console.print("[yellow]This will open a web browser for authentication...[/yellow]")
            console.print("[yellow]Please complete the authentication in your browser.[/yellow]")
            
            # Run the authentication command interactively
            # This allows the browser to open and user interaction to occur
            result = subprocess.run(
                cmd,
                timeout=timeout_minutes * 60,
                text=True,
                # Don't capture output to allow interactive flow
                # stdin, stdout, stderr will use parent process (terminal)
            )
            
            # Check if the command was successful
            if result.returncode == 0:
                console.print(f"[green]✓ Session token created successfully for profile '{profile_name}'![/green]")
                
                # Verify the session was created by checking if we can load the config
                try:
                    config_path = Path(config_file_path) if config_file_path else Path.home() / ".oci" / "config"
                    if config_path.exists():
                        test_config = oci.config.from_file(
                            file_location=str(config_path),
                            profile_name=profile_name
                        )
                        if test_config.get("security_token_file"):
                            console.print(
                                f"[dim]Session token file: {test_config['security_token_file']}[/dim]"
                            )
                    
                except Exception as e:
                    logger.warning(f"Could not verify session token creation: {e}")
                
                return True
            else:
                console.print(f"[red]✗ Failed to create session token. Exit code: {result.returncode}[/red]")
                return False
                
        except subprocess.TimeoutExpired:
            console.print(f"[red]✗ Session authentication timed out after {timeout_minutes} minutes[/red]")
            return False
        except FileNotFoundError:
            raise RuntimeError(
                "OCI CLI not found. Please install it first:\n"
                "pip install oci-cli\n"
                "or follow instructions at: https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/cliinstall.htm"
            )
        except Exception as e:
            logger.error(f"Failed to create session token: {e}")
            console.print(f"[red]Error creating session token: {e}[/red]")
            return False
    
    def create_and_use_session_token(
        self,
        profile_name: str,
        region_name: str,
        tenancy_name: str = "bmc_operator_access",
        config_file_path: Optional[str] = None,
        timeout_minutes: int = 5
    ) -> bool:
        """
        Create a session token and reinitialize the client to use it.
        
        This is a convenience method that:
        1. Creates a new session token using create_session_token()
        2. Updates the current client configuration to use the new profile
        3. Re-authenticates with the new session token
        
        Args:
            profile_name: Name of the OCI profile to create/update
            region_name: OCI region name
            tenancy_name: Tenancy name for authentication (default: 'bmc_operator_access')
            config_file_path: Optional custom path to OCI config file
            timeout_minutes: Timeout for the authentication process in minutes (default: 5)
            
        Returns:
            bool: True if session token was created and client was updated successfully
        """
        try:
            # Create the session token
            if not self.create_session_token(
                profile_name=profile_name,
                region_name=region_name, 
                tenancy_name=tenancy_name,
                config_file_path=config_file_path,
                timeout_minutes=timeout_minutes
            ):
                return False
            
            # Update client configuration to use the new profile
            console.print(f"[blue]Switching client to use profile '{profile_name}'...[/blue]")
            
            # Create new config with the session token profile
            new_config = OCIConfig(
                region=region_name,
                profile_name=profile_name,
                config_file=config_file_path
            )
            
            # Re-initialize authenticator and authenticate
            self.config = new_config
            self.authenticator = OCIAuthenticator(self.config)
            
            # Clear existing clients so they get re-created with new auth
            self._compute_client = None
            self._identity_client = None
            self._bastion_client = None
            self._network_client = None
            
            # Re-authenticate with new session token
            self._authenticate()
            
            console.print("[green]✓ Client updated to use new session token![/green]")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create and use session token: {e}")
            console.print(f"[red]Failed to update client with session token: {e}[/red]")
            return False
    
    def refresh_auth(self) -> bool:
        """Refresh authentication if using session tokens."""
        if self.config.auth_type == AuthType.SESSION_TOKEN:
            return self.authenticator.refresh_token()
        return True
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup resources."""
        # Close any open clients
        pass