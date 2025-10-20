"""Main OCI client module with optimized functionality."""

import logging
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import oci
import requests
from oci.container_engine.models import UpdateClusterDetails, UpdateNodePoolDetails
from oci.pagination import list_call_get_all_results
from rich.console import Console
from tenacity import retry, stop_after_attempt, wait_exponential

from .auth import OCIAuthenticator
from .models import (
    AuthType,
    BastionInfo,
    BastionType,
    InstanceInfo,
    LifecycleState,
    OKEClusterInfo,
    OKENodePoolInfo,
    OCIConfig,
    RegionInfo,
    SessionInfo,
)

logger = logging.getLogger(__name__)
console = Console()


def create_oci_session_token(
    profile_name: str,
    region_name: str,
    tenancy_name: str = "bmc_operator_access",
    config_file_path: Optional[str] = None,
    timeout_minutes: int = 5,
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
        result = subprocess.run(["oci", "--version"], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            console.print(
                "[red]OCI CLI not found. Please install it first: pip install oci-cli[/red]"
            )
            return False

        console.print(f"[blue]Creating session token for profile '{profile_name}'...[/blue]")

        # Build the OCI session authenticate command
        cmd = [
            "oci",
            "session",
            "authenticate",
            "--profile-name",
            profile_name,
            "--region",
            region_name,
            "--tenancy-name",
            tenancy_name,
        ]

        # Add custom config file if specified
        if config_file_path:
            cmd.extend(["--config-file", config_file_path])

        console.print(f"[dim]Running: {' '.join(cmd)}[/dim]")
        console.print("[yellow]This will open a web browser for authentication...[/yellow]")
        console.print("[yellow]Please complete the authentication in your browser.[/yellow]")

        # Run the authentication command interactively
        result = subprocess.run(cmd, timeout=timeout_minutes * 60, text=True)

        # Check if the command was successful
        if result.returncode == 0:
            console.print(
                f"[green]✓ Session token created successfully for profile '{profile_name}'![/green]"
            )

            # Verify the session was created by checking if we can load the config
            try:
                config_path = (
                    Path(config_file_path) if config_file_path else Path.home() / ".oci" / "config"
                )
                if config_path.exists():
                    test_config = oci.config.from_file(
                        file_location=str(config_path), profile_name=profile_name
                    )
                    if test_config.get("security_token_file"):
                        console.print(
                            f"[dim]Session token file: {test_config['security_token_file']}[/dim]"
                        )

            except Exception as e:
                logger.warning(f"Could not verify session token creation: {e}")

            return True
        else:
            console.print(
                f"[red]✗ Failed to create session token. Exit code: {result.returncode}[/red]"
            )
            return False

    except subprocess.TimeoutExpired:
        console.print(
            f"[red]✗ Session authentication timed out after {timeout_minutes} minutes[/red]"
        )
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
        retry_strategy: Optional[oci.retry.RetryStrategyBuilder] = None,
    ):
        """
        Initialize OCI client with authentication and service clients.

        Args:
            region: OCI region name (e.g., 'us-phoenix-1')
            profile_name: OCI config profile name
            config_file: Optional path to config file (defaults to ~/.oci/config)
            retry_strategy: Optional retry strategy for API calls
        """
        self.config = OCIConfig(region=region, profile_name=profile_name, config_file=config_file)
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
        self._object_storage_client: Optional[oci.object_storage.ObjectStorageClient] = None
        self._container_engine_client: Optional[oci.container_engine.ContainerEngineClient] = None

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
                self.oci_config, signer=self.signer, retry_strategy=self.retry_strategy
            )
        return self._compute_client

    @property
    def identity_client(self) -> oci.identity.IdentityClient:
        """Lazy-load identity client."""
        if not self._identity_client:
            self._identity_client = oci.identity.IdentityClient(
                self.oci_config, signer=self.signer, retry_strategy=self.retry_strategy
            )
        return self._identity_client

    @property
    def bastion_client(self) -> oci.bastion.BastionClient:
        """Lazy-load bastion client."""
        if not self._bastion_client:
            self._bastion_client = oci.bastion.BastionClient(
                self.oci_config, signer=self.signer, retry_strategy=self.retry_strategy
            )
        return self._bastion_client

    @property
    def network_client(self) -> oci.core.VirtualNetworkClient:
        """Lazy-load network client."""
        if not self._network_client:
            self._network_client = oci.core.VirtualNetworkClient(
                self.oci_config, signer=self.signer, retry_strategy=self.retry_strategy
            )
        return self._network_client

    @property
    def object_storage_client(self) -> oci.object_storage.ObjectStorageClient:
        """Lazy-load object storage client."""
        if not self._object_storage_client:
            self._object_storage_client = oci.object_storage.ObjectStorageClient(
                self.oci_config, signer=self.signer, retry_strategy=self.retry_strategy
            )
        return self._object_storage_client

    @property
    def container_engine_client(self) -> oci.container_engine.ContainerEngineClient:
        """Lazy-load OKE container engine client."""
        if not self._container_engine_client:
            self._container_engine_client = oci.container_engine.ContainerEngineClient(
                self.oci_config, signer=self.signer, retry_strategy=self.retry_strategy
            )
        return self._container_engine_client

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
                    if self.oci_config is None:
                        raise ValueError("OCI config is not initialized")
                    tenancy = self.identity_client.get_tenancy(self.oci_config["tenancy"]).data

                    return RegionInfo(
                        name=region.name,
                        key=region.key.lower(),
                        is_home_region=(region.name == tenancy.home_region_key),
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
                domain = data.get("internal_realm_domain")
                return str(domain) if domain is not None else None

            return None

        except Exception as e:
            logger.warning(f"Could not get internal domain: {e}")
            return None

    def list_compartments(
        self, parent_compartment_id: str, include_root: bool = False
    ) -> List[Dict[str, Any]]:
        """List all compartments under a parent compartment."""
        try:
            compartments = []

            if include_root:
                root = self.identity_client.get_compartment(parent_compartment_id).data
                compartments.append(
                    {
                        "id": root.id,
                        "name": root.name,
                        "description": root.description,
                        "lifecycle_state": root.lifecycle_state,
                    }
                )

            # List child compartments
            response = self.identity_client.list_compartments(
                parent_compartment_id,
                compartment_id_in_subtree=True,
                lifecycle_state=LifecycleState.ACTIVE.value,
            )

            for comp in response.data:
                compartments.append(
                    {
                        "id": comp.id,
                        "name": comp.name,
                        "description": comp.description,
                        "lifecycle_state": comp.lifecycle_state,
                    }
                )

            return compartments

        except Exception as e:
            logger.error(f"Failed to list compartments: {e}")
            raise RuntimeError(f"Failed to list compartments: {e}")

    def list_instances(
        self,
        compartment_id: str,
        lifecycle_state: Optional[LifecycleState] = None,
        availability_domain: Optional[str] = None,
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
                    response = self.compute_client.list_instances(**kwargs, page=response.next_page)
                else:
                    break

            return instances

        except Exception as e:
            logger.error(f"Failed to list instances: {e}")
            raise RuntimeError(f"Failed to list instances: {e}")

    def list_oke_clusters(
        self,
        compartment_id: str,
        lifecycle_state: Optional[LifecycleState] = None,
    ) -> List[OKEClusterInfo]:
        """List OKE clusters in a compartment."""
        try:
            ce_client = self.container_engine_client
            request_kwargs: Dict[str, Any] = {"compartment_id": compartment_id}
            if lifecycle_state:
                request_kwargs["lifecycle_state"] = lifecycle_state.value

            response = list_call_get_all_results(ce_client.list_clusters, **request_kwargs)
            clusters: List[OKEClusterInfo] = []

            for cluster in getattr(response, "data", []) or []:
                cluster_id = getattr(cluster, "id", None)
                if not cluster_id:
                    logger.debug(
                        "Skipping OKE cluster without an ID in compartment %s", compartment_id
                    )
                    continue

                available_upgrades_attr = getattr(cluster, "available_kubernetes_upgrades", None)
                if available_upgrades_attr is None:
                    available_upgrades_attr = getattr(cluster, "available_upgrades", None)
                available_upgrades = list(available_upgrades_attr or [])

                if not available_upgrades:
                    try:
                        cluster_details = ce_client.get_cluster(cluster_id).data
                        upgrades_from_details = getattr(
                            cluster_details, "available_kubernetes_upgrades", None
                        )
                        if upgrades_from_details is None:
                            upgrades_from_details = getattr(
                                cluster_details, "available_upgrades", None
                            )
                        available_upgrades = list(upgrades_from_details or [])
                    except Exception as details_error:  # pragma: no cover - diagnostic only
                        logger.debug(
                            "Could not fetch detailed upgrades for cluster %s: %s",
                            cluster_id,
                            details_error,
                        )

                cluster_info = OKEClusterInfo(
                    cluster_id=cluster_id,
                    name=getattr(cluster, "name", cluster_id),
                    kubernetes_version=getattr(cluster, "kubernetes_version", None),
                    lifecycle_state=getattr(cluster, "lifecycle_state", None),
                    compartment_id=getattr(cluster, "compartment_id", compartment_id),
                    available_upgrades=available_upgrades,
                )
                clusters.append(cluster_info)

            return clusters

        except Exception as e:
            logger.error(f"Failed to list OKE clusters in compartment {compartment_id}: {e}")
            raise RuntimeError(f"Failed to list OKE clusters in compartment {compartment_id}: {e}") from e

    def list_node_pools(
        self,
        cluster_id: str,
        compartment_id: Optional[str] = None,
    ) -> List[OKENodePoolInfo]:
        """List node pools for an OKE cluster."""
        try:
            ce_client = self.container_engine_client
            request_kwargs: Dict[str, Any] = {"cluster_id": cluster_id}
            if compartment_id:
                request_kwargs["compartment_id"] = compartment_id

            response = list_call_get_all_results(ce_client.list_node_pools, **request_kwargs)
            node_pools: List[OKENodePoolInfo] = []

            for node_pool in getattr(response, "data", []) or []:
                node_pool_id = getattr(node_pool, "id", None)
                if not node_pool_id:
                    logger.debug("Skipping node pool without an ID for cluster %s", cluster_id)
                    continue

                node_pool_info = OKENodePoolInfo(
                    node_pool_id=node_pool_id,
                    name=getattr(node_pool, "name", node_pool_id),
                    kubernetes_version=getattr(node_pool, "kubernetes_version", None),
                    lifecycle_state=getattr(node_pool, "lifecycle_state", None),
                )
                node_pools.append(node_pool_info)

            return node_pools

        except Exception as e:
            logger.error(f"Failed to list node pools for cluster {cluster_id}: {e}")
            raise RuntimeError(f"Failed to list node pools for cluster {cluster_id}: {e}") from e

    def get_oke_cluster(self, cluster_id: str) -> OKEClusterInfo:
        """Retrieve detailed information for an OKE cluster."""
        ce_client = self.container_engine_client
        try:
            cluster = ce_client.get_cluster(cluster_id).data
        except Exception as exc:
            logger.error(
                "Failed to fetch OKE cluster details: cluster_id=%s region=%s error=%s",
                cluster_id,
                self.config.region,
                exc,
            )
            raise RuntimeError(f"Failed to fetch cluster {cluster_id}: {exc}") from exc

        available_upgrades_attr = getattr(cluster, "available_kubernetes_upgrades", None)
        if available_upgrades_attr is None:
            available_upgrades_attr = getattr(cluster, "available_upgrades", None)
        available_upgrades = list(available_upgrades_attr or [])

        cluster_info = OKEClusterInfo(
            cluster_id=cluster_id,
            name=getattr(cluster, "name", cluster_id),
            kubernetes_version=getattr(cluster, "kubernetes_version", None),
            lifecycle_state=getattr(cluster, "lifecycle_state", None),
            compartment_id=getattr(cluster, "compartment_id", None),
            available_upgrades=available_upgrades,
        )

        return cluster_info

    def upgrade_oke_cluster(self, cluster_id: str, target_version: str) -> str:
        """
        Initiate an upgrade of the specified OKE cluster to the target Kubernetes version.

        Returns:
            str: Work request ID for tracking the upgrade.
        """
        ce_client = self.container_engine_client
        logger.info(
            "Initiating OKE cluster upgrade: cluster_id=%s target_version=%s region=%s",
            cluster_id,
            target_version,
            self.config.region,
        )
        try:
            update_details = UpdateClusterDetails(kubernetes_version=target_version)
            response = ce_client.update_cluster(cluster_id, update_details)
            work_request_id = response.headers.get("opc-work-request-id", "")
            if not work_request_id:
                logger.debug(
                    "No work request ID returned from update_cluster call for cluster_id=%s target_version=%s",
                    cluster_id,
                    target_version,
                )
            return work_request_id
        except Exception as exc:
            logger.error(
                "Failed to initiate OKE cluster upgrade: cluster_id=%s target_version=%s region=%s error=%s",
                cluster_id,
                target_version,
                self.config.region,
                exc,
            )
            raise RuntimeError(
                f"Failed to initiate upgrade for cluster {cluster_id} to {target_version}: {exc}"
            ) from exc

    def upgrade_oke_node_pool(self, node_pool_id: str, target_version: str) -> str:
        """
        Initiate an upgrade of the specified OKE node pool to the target Kubernetes version.

        Returns:
            str: Work request ID for tracking the upgrade.
        """
        ce_client = self.container_engine_client
        logger.info(
            "Initiating OKE node pool upgrade: node_pool_id=%s target_version=%s region=%s",
            node_pool_id,
            target_version,
            self.config.region,
        )
        try:
            update_details = UpdateNodePoolDetails(kubernetes_version=target_version)
            response = ce_client.update_node_pool(node_pool_id, update_details)
            work_request_id = response.headers.get("opc-work-request-id", "")
            if not work_request_id:
                logger.debug(
                    "No work request ID returned from update_node_pool call for node_pool_id=%s target_version=%s",
                    node_pool_id,
                    target_version,
                )
            return work_request_id
        except Exception as exc:
            logger.error(
                "Failed to initiate OKE node pool upgrade: node_pool_id=%s target_version=%s region=%s error=%s",
                node_pool_id,
                target_version,
                self.config.region,
                exc,
            )
            raise RuntimeError(
                f"Failed to initiate upgrade for node pool {node_pool_id} to {target_version}: {exc}"
            ) from exc

    def list_oke_instances(
        self, compartment_id: str, cluster_name: Optional[str] = None
    ) -> List[InstanceInfo]:
        """List OKE (Kubernetes) cluster instances."""
        all_instances = self.list_instances(compartment_id, lifecycle_state=LifecycleState.RUNNING)

        oke_instances = []
        logger.info(f"Checking {len(all_instances)} instances for OKE metadata...")

        for instance in all_instances:
            is_oke = False
            detected_cluster_name = None
            detection_method = None

            # Method 1: Check traditional OKE metadata fields
            cluster_display_name = instance.metadata.get("oke-cluster-display-name")
            node_labels = instance.metadata.get("oke-initial-node-labels", {})

            if cluster_display_name and isinstance(node_labels, dict):
                if "tot.oraclecloud.com/node-pool-name" in node_labels:
                    is_oke = True
                    detected_cluster_name = cluster_display_name
                    detection_method = "traditional oke-cluster-display-name"

            # Method 2: Check for newer OKE metadata patterns
            if not is_oke:
                # Check for cluster ID in metadata
                cluster_id = instance.metadata.get(
                    "oci.oraclecloud.com/oke-cluster-id"
                ) or instance.metadata.get("oke-cluster-id")

                if cluster_id:
                    is_oke = True
                    detected_cluster_name = (
                        instance.metadata.get("oci.oraclecloud.com/oke-cluster-name")
                        or instance.metadata.get("oke-cluster-name")
                        or cluster_id
                    )
                    detection_method = "cluster-id metadata"

            # Method 3: Check for Kubernetes-related metadata
            if not is_oke:
                # Look for node pool or kubernetes-related tags
                k8s_metadata = instance.metadata.get("kubernetes", {})
                if k8s_metadata or "node-pool" in str(instance.metadata).lower():
                    is_oke = True
                    detected_cluster_name = (
                        k8s_metadata.get("cluster-name")
                        if isinstance(k8s_metadata, dict)
                        else "unknown"
                    )
                    detection_method = "kubernetes metadata"

            # Method 4: Check defined tags for OKE patterns
            if not is_oke and hasattr(instance, "defined_tags"):
                for tag_namespace, tags in instance.defined_tags.items():
                    if "oke" in tag_namespace.lower() or "kubernetes" in tag_namespace.lower():
                        is_oke = True
                        detected_cluster_name = tags.get(
                            "cluster-name", tags.get("cluster_name", "unknown")
                        )
                        detection_method = f"defined tags ({tag_namespace})"
                        break

            # Method 5: Check display name patterns
            if not is_oke and instance.display_name:
                display_name_lower = instance.display_name.lower()
                if any(
                    pattern in display_name_lower
                    for pattern in ["oke-", "k8s-", "kubernetes", "node-pool"]
                ):
                    is_oke = True
                    detected_cluster_name = "detected-from-name"
                    detection_method = "display name pattern"

            if is_oke:
                # Filter by cluster name if specified
                if cluster_name and detected_cluster_name != cluster_name:
                    logger.debug(
                        f"Skipping OKE instance {instance.instance_id}: cluster mismatch ({detected_cluster_name} != {cluster_name})"
                    )
                    continue

                instance.cluster_name = detected_cluster_name
                oke_instances.append(instance)
                logger.info(
                    f"Found OKE instance {instance.instance_id} in cluster '{detected_cluster_name}' via {detection_method}"
                )
            else:
                # Debug: Log instances that weren't detected as OKE
                logger.debug(
                    f"Instance {instance.instance_id} ({instance.display_name}) - not detected as OKE"
                )
                if logger.level <= 10:  # DEBUG level
                    logger.debug(f"  Metadata keys: {list(instance.metadata.keys())}")
                    if hasattr(instance, "defined_tags"):
                        logger.debug(
                            f"  Defined tag namespaces: {list(instance.defined_tags.keys())}"
                        )

        if len(oke_instances) == 0 and len(all_instances) > 0:
            logger.warning(
                "No OKE instances found. Set OCI_LOG_LEVEL=DEBUG to see detailed metadata analysis."
            )
            logger.info(
                "You can also check instance metadata manually to identify the correct OKE detection pattern."
            )

        logger.info(f"Found {len(oke_instances)} OKE instances total")
        return sorted(oke_instances, key=lambda x: x.cluster_name or "")

    def debug_instance_metadata(
        self, compartment_id: str, instance_id: Optional[str] = None
    ) -> None:
        """Debug helper: Print detailed metadata for instances to help identify OKE detection patterns."""
        all_instances = self.list_instances(compartment_id, lifecycle_state=LifecycleState.RUNNING)

        if instance_id:
            # Show specific instance
            instances_to_show = [inst for inst in all_instances if inst.instance_id == instance_id]
            if not instances_to_show:
                logger.error(f"Instance {instance_id} not found")
                return
        else:
            # Show first few instances as examples
            instances_to_show = all_instances[:3]

        for instance in instances_to_show:
            logger.info(f"\n=== Instance {instance.instance_id} ({instance.display_name}) ===")
            logger.info(f"Metadata keys: {list(instance.metadata.keys())}")
            logger.info("Metadata content:")
            for key, value in instance.metadata.items():
                if isinstance(value, dict):
                    logger.info(f"  {key}: {list(value.keys())} (dict)")
                else:
                    logger.info(f"  {key}: {value}")

            if hasattr(instance, "defined_tags"):
                logger.info("Defined tags:")
                for tag_namespace, tags in instance.defined_tags.items():
                    logger.info(f"  {tag_namespace}: {tags}")

            if hasattr(instance, "freeform_tags"):
                logger.info(f"Freeform tags: {instance.freeform_tags}")

    def list_odo_instances(self, compartment_id: str) -> List[InstanceInfo]:
        """List ODO (Oracle Data Operations) instances."""
        all_instances = self.list_instances(compartment_id, lifecycle_state=LifecycleState.RUNNING)

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
        self, compartment_id: str, bastion_type: Optional[BastionType] = BastionType.INTERNAL
    ) -> List[BastionInfo]:
        """List bastions in a compartment."""
        try:
            bastions = []

            # Only pass valid parameters to the OCI API
            kwargs = {"compartment_id": compartment_id}

            response = self.bastion_client.list_bastions(**kwargs)

            for bastion in response.data:
                # Filter by lifecycle_state on the client side
                if hasattr(bastion, "lifecycle_state"):
                    try:
                        bastion_lifecycle_state = LifecycleState(bastion.lifecycle_state)
                        if bastion_lifecycle_state != LifecycleState.ACTIVE:
                            continue  # Skip non-active bastions
                    except (ValueError, AttributeError):
                        # If we can't determine lifecycle state, include it
                        pass

                # Filter by bastion_type on the client side
                if bastion_type and hasattr(bastion, "bastion_type"):
                    try:
                        bastion_bastion_type = BastionType(bastion.bastion_type)
                        if bastion_bastion_type != bastion_type:
                            continue  # Skip bastions that don't match the requested type
                    except (ValueError, AttributeError):
                        # If we can't determine bastion type, include it
                        pass

                # Get max session TTL - check multiple possible attribute names
                max_session_ttl = None
                for attr_name in ["max_session_ttl_in_seconds", "max_session_ttl", "session_ttl"]:
                    if hasattr(bastion, attr_name):
                        max_session_ttl = getattr(bastion, attr_name)
                        break

                target_subnet_id = getattr(bastion, "target_subnet_id", "")
                if not target_subnet_id:
                    continue  # Skip bastions without target subnet

                bastion_type = BastionType.INTERNAL  # default
                if hasattr(bastion, "bastion_type"):
                    try:
                        bastion_type = BastionType(bastion.bastion_type)
                    except (ValueError, TypeError):
                        pass  # Keep default

                lifecycle_state = LifecycleState.ACTIVE  # default
                if hasattr(bastion, "lifecycle_state"):
                    try:
                        lifecycle_state = LifecycleState(bastion.lifecycle_state)
                    except (ValueError, TypeError):
                        pass  # Keep default

                bastions.append(
                    BastionInfo(
                        bastion_id=bastion.id,
                        target_subnet_id=target_subnet_id,
                        bastion_name=getattr(bastion, "name", None),
                        bastion_type=bastion_type,
                        max_session_ttl=max_session_ttl or 10800,
                        lifecycle_state=lifecycle_state,
                    )
                )

            return bastions

        except Exception as e:
            logger.error(f"Failed to list bastions: {e}")
            raise RuntimeError(f"Failed to list bastions: {e}")

    def find_bastion_for_subnet(
        self, bastions: List[BastionInfo], subnet_id: str, instance_id: Optional[str] = None
    ) -> Optional[BastionInfo]:
        """
        Find the best bastion that can access the given subnet.

        When multiple bastions target the same subnet, uses deterministic selection
        based on instance_id to ensure consistent pairing.

        Args:
            bastions: List of available bastions
            subnet_id: Target subnet ID to find bastion for
            instance_id: Optional instance ID for deterministic selection

        Returns:
            Best matching bastion or None if no match found
        """
        # Find all bastions that can access the target subnet
        matching_bastions = [
            bastion for bastion in bastions if bastion.target_subnet_id == subnet_id
        ]

        if not matching_bastions:
            return None

        if len(matching_bastions) == 1:
            return matching_bastions[0]

        # Multiple bastions found - use intelligent selection
        # Sort bastions by name for deterministic ordering
        matching_bastions.sort(key=lambda b: b.bastion_name or b.bastion_id)

        if instance_id:
            # Use hash-based selection for consistent instance-to-bastion pairing
            import hashlib

            hash_value = int(hashlib.md5(instance_id.encode()).hexdigest(), 16)
            selected_index = hash_value % len(matching_bastions)
            selected_bastion = matching_bastions[selected_index]

            # Log the selection for visibility
            if len(matching_bastions) > 1:
                logger.info(
                    f"Selected bastion {selected_bastion.bastion_name or selected_bastion.bastion_id} "
                    f"for instance {instance_id} (chose {selected_index + 1} of {len(matching_bastions)} available)"
                )

            return selected_bastion

        # Fallback: return first bastion (alphabetically)
        return matching_bastions[0]

    def create_bastion_session(
        self,
        bastion_id: str,
        target_resource_id: str,
        target_private_ip: str,
        session_ttl: int = 10800,
        key_type: str = "PUB",
    ) -> SessionInfo:
        """Create a new bastion session."""
        try:
            details = oci.bastion.models.CreateSessionDetails(
                bastion_id=bastion_id,
                target_resource_details=oci.bastion.models.CreateManagedSshSessionTargetResourceDetails(
                    session_type="MANAGED_SSH",
                    target_resource_id=target_resource_id,
                    target_resource_private_ip_address=target_private_ip,
                ),
                key_details=oci.bastion.models.PublicKeyDetails(
                    public_key_content=self._get_or_generate_ssh_key()
                ),
                session_ttl_in_seconds=session_ttl,
            )

            response = self.bastion_client.create_session(details)
            session = response.data

            return SessionInfo(
                session_id=session.id,
                bastion_id=session.bastion_id,
                target_resource_id=target_resource_id,
                target_resource_private_ip=target_private_ip,
                ssh_metadata=session.ssh_metadata,
                lifecycle_state=LifecycleState(session.lifecycle_state),
            )

        except Exception as e:
            logger.error(f"Failed to create bastion session: {e}")
            raise RuntimeError(f"Failed to create bastion session: {e}")

    def _parse_instance(self, compartment_id: str, instance: Any) -> Optional[InstanceInfo]:
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
                tags={**instance.freeform_tags, **instance.defined_tags},
            )

        except Exception as e:
            logger.warning(f"Failed to parse instance {instance.id}: {e}")
            return None

    def _get_instance_vnic(
        self, compartment_id: str, instance_id: str
    ) -> Optional[Tuple[str, Optional[str], str]]:
        """Get VNIC information for an instance."""
        try:
            # List VNIC attachments
            vnics = self.compute_client.list_vnic_attachments(
                compartment_id=compartment_id, instance_id=instance_id
            ).data

            for vnic_attachment in vnics:
                if vnic_attachment.lifecycle_state == "ATTACHED":
                    # Get VNIC details
                    vnic = self.network_client.get_vnic(vnic_attachment.vnic_id).data

                    if vnic.lifecycle_state == "AVAILABLE" and vnic.private_ip:
                        # Skip VNICs created by other services
                        if not vnic.freeform_tags.get("CreatedBy"):
                            return (vnic.private_ip, vnic.public_ip, vnic.subnet_id)

            return None

        except Exception as e:
            logger.warning(f"Failed to get VNIC for instance {instance_id}: {e}")
            return None

    def _get_or_generate_ssh_key(self) -> str:
        """Get existing SSH public key or generate a new one."""
        ssh_path = Path.home() / ".ssh"
        pub_key_path = ssh_path / "id_rsa.pub"

        if pub_key_path.exists():
            with open(pub_key_path, "r") as f:
                return f.read().strip()

        # Generate new key pair if needed
        import subprocess

        priv_key_path = ssh_path / "id_rsa"

        if not priv_key_path.exists():
            ssh_path.mkdir(mode=0o700, exist_ok=True)
            subprocess.run(
                [
                    "ssh-keygen",
                    "-t",
                    "rsa",
                    "-b",
                    "2048",
                    "-f",
                    str(priv_key_path),
                    "-N",
                    "",  # No passphrase
                ],
                check=True,
            )

        with open(pub_key_path, "r") as f:
            return f.read().strip()

    def create_session_token(
        self,
        profile_name: str,
        region_name: str,
        tenancy_name: str = "bmc_operator_access",
        config_file_path: Optional[str] = None,
        timeout_minutes: int = 5,
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
                ["oci", "--version"], capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                raise RuntimeError(
                    "OCI CLI not found. Please install it first: pip install oci-cli"
                )

            console.print(f"[blue]Creating session token for profile '{profile_name}'...[/blue]")

            # Build the OCI session authenticate command
            cmd = [
                "oci",
                "session",
                "authenticate",
                "--profile-name",
                profile_name,
                "--region",
                region_name,
                "--tenancy-name",
                tenancy_name,
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
                console.print(
                    f"[green]✓ Session token created successfully for profile '{profile_name}'![/green]"
                )

                # Verify the session was created by checking if we can load the config
                try:
                    config_path = (
                        Path(config_file_path)
                        if config_file_path
                        else Path.home() / ".oci" / "config"
                    )
                    if config_path.exists():
                        test_config = oci.config.from_file(
                            file_location=str(config_path), profile_name=profile_name
                        )
                        if test_config.get("security_token_file"):
                            console.print(
                                f"[dim]Session token file: {test_config['security_token_file']}[/dim]"
                            )

                except Exception as e:
                    logger.warning(f"Could not verify session token creation: {e}")

                return True
            else:
                console.print(
                    f"[red]✗ Failed to create session token. Exit code: {result.returncode}[/red]"
                )
                return False

        except subprocess.TimeoutExpired:
            console.print(
                f"[red]✗ Session authentication timed out after {timeout_minutes} minutes[/red]"
            )
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
        timeout_minutes: int = 5,
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
                timeout_minutes=timeout_minutes,
            ):
                return False

            # Update client configuration to use the new profile
            console.print(f"[blue]Switching client to use profile '{profile_name}'...[/blue]")

            # Create new config with the session token profile
            new_config = OCIConfig(
                region=region_name, profile_name=profile_name, config_file=config_file_path
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

    def __enter__(self) -> "OCIClient":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit - cleanup resources."""
        # Close any open clients
        pass
