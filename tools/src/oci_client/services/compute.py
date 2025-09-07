"""Compute service operations for OCI."""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

import oci
from tenacity import retry, stop_after_attempt, wait_exponential

from ..models import InstanceInfo, LifecycleState

logger = logging.getLogger(__name__)


class ComputeService:
    """Service class for compute-related operations."""
    
    def __init__(self, compute_client: oci.core.ComputeClient, network_client: oci.core.VirtualNetworkClient):
        """Initialize compute service."""
        self.compute_client = compute_client
        self.network_client = network_client
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def get_instance(self, instance_id: str) -> Dict[str, Any]:
        """Get detailed information about a specific instance."""
        try:
            instance = self.compute_client.get_instance(instance_id).data
            
            # Get boot volume
            boot_volume = None
            boot_volume_attachments = self.compute_client.list_boot_volume_attachments(
                availability_domain=instance.availability_domain,
                compartment_id=instance.compartment_id,
                instance_id=instance_id
            ).data
            
            if boot_volume_attachments:
                boot_volume = boot_volume_attachments[0].boot_volume_id
            
            # Get block volumes
            block_volumes = []
            volume_attachments = self.compute_client.list_volume_attachments(
                compartment_id=instance.compartment_id,
                instance_id=instance_id
            ).data
            
            for attachment in volume_attachments:
                if attachment.lifecycle_state == "ATTACHED":
                    block_volumes.append({
                        "volume_id": attachment.volume_id,
                        "device": attachment.device,
                        "is_read_only": attachment.is_read_only
                    })
            
            return {
                "id": instance.id,
                "display_name": instance.display_name,
                "shape": instance.shape,
                "shape_config": {
                    "ocpus": instance.shape_config.ocpus if instance.shape_config else None,
                    "memory_in_gbs": instance.shape_config.memory_in_gbs if instance.shape_config else None
                },
                "availability_domain": instance.availability_domain,
                "fault_domain": instance.fault_domain,
                "lifecycle_state": instance.lifecycle_state,
                "time_created": instance.time_created.isoformat() if instance.time_created else None,
                "boot_volume_id": boot_volume,
                "block_volumes": block_volumes,
                "metadata": instance.metadata,
                "extended_metadata": instance.extended_metadata,
                "freeform_tags": instance.freeform_tags,
                "defined_tags": instance.defined_tags
            }
            
        except Exception as e:
            logger.error(f"Failed to get instance {instance_id}: {e}")
            raise
    
    def start_instance(self, instance_id: str) -> bool:
        """Start a stopped instance."""
        try:
            self.compute_client.instance_action(
                instance_id=instance_id,
                action="START"
            )
            logger.info(f"Started instance {instance_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start instance {instance_id}: {e}")
            return False
    
    def stop_instance(self, instance_id: str, force: bool = False) -> bool:
        """Stop a running instance."""
        try:
            action = "STOP" if not force else "SOFTSTOP"
            self.compute_client.instance_action(
                instance_id=instance_id,
                action=action
            )
            logger.info(f"Stopped instance {instance_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop instance {instance_id}: {e}")
            return False
    
    def reboot_instance(self, instance_id: str, force: bool = False) -> bool:
        """Reboot an instance."""
        try:
            action = "SOFTRESET" if not force else "RESET"
            self.compute_client.instance_action(
                instance_id=instance_id,
                action=action
            )
            logger.info(f"Rebooted instance {instance_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to reboot instance {instance_id}: {e}")
            return False
    
    def get_instance_metrics(
        self,
        instance_id: str,
        metric_names: Optional[List[str]] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Get metrics for an instance (requires monitoring service)."""
        # This would require the monitoring client
        # Placeholder for monitoring integration
        return {
            "instance_id": instance_id,
            "metrics": [],
            "message": "Monitoring service integration required"
        }
    
    def list_instance_console_connections(
        self,
        compartment_id: str,
        instance_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List console connections for instances."""
        try:
            kwargs = {"compartment_id": compartment_id}
            if instance_id:
                kwargs["instance_id"] = instance_id
            
            connections = self.compute_client.list_instance_console_connections(**kwargs).data
            
            return [
                {
                    "id": conn.id,
                    "instance_id": conn.instance_id,
                    "connection_string": conn.connection_string,
                    "fingerprint": conn.fingerprint,
                    "lifecycle_state": conn.lifecycle_state
                }
                for conn in connections
            ]
            
        except Exception as e:
            logger.error(f"Failed to list console connections: {e}")
            raise
    
    def create_instance_console_connection(
        self,
        instance_id: str,
        public_key: str
    ) -> Dict[str, Any]:
        """Create a new console connection for an instance."""
        try:
            details = oci.core.models.CreateInstanceConsoleConnectionDetails(
                instance_id=instance_id,
                public_key=public_key
            )
            
            response = self.compute_client.create_instance_console_connection(details)
            conn = response.data
            
            return {
                "id": conn.id,
                "instance_id": conn.instance_id,
                "connection_string": conn.connection_string,
                "fingerprint": conn.fingerprint,
                "vnc_connection_string": conn.vnc_connection_string
            }
            
        except Exception as e:
            logger.error(f"Failed to create console connection: {e}")
            raise