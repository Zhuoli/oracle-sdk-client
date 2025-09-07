"""
Resource collection utilities for OCI services.
"""

from typing import List, Optional
from ..client import OCIClient
from ..models import InstanceInfo, BastionInfo
from .display import display_error


def collect_oke_instances(client: OCIClient, compartment_id: str, region: str) -> List[InstanceInfo]:
    """
    Collect OKE instances for a specific compartment and region.
    
    Returns:
        List of OKE instances or empty list if collection fails
    """
    try:
        oke_instances = client.list_oke_instances(compartment_id=compartment_id)
        return oke_instances
        
    except Exception as e:
        display_error(f"Error listing OKE instances in {region}: {e}")
        return []


def collect_odo_instances(client: OCIClient, compartment_id: str, region: str) -> List[InstanceInfo]:
    """
    Collect ODO instances for a specific compartment and region.
    
    Returns:
        List of ODO instances or empty list if collection fails
    """
    try:
        odo_instances = client.list_odo_instances(compartment_id=compartment_id)
        return odo_instances
        
    except Exception as e:
        display_error(f"Error listing ODO instances in {region}: {e}")
        return []


def collect_bastions(client: OCIClient, compartment_id: str, region: str) -> List[BastionInfo]:
    """
    Collect bastions for a specific compartment and region.
    
    Returns:
        List of bastions or empty list if collection fails
    """
    try:
        bastions = client.list_bastions(compartment_id=compartment_id)
        return bastions
        
    except Exception as e:
        display_error(f"Error listing bastions in {region}: {e}")
        return []


def collect_all_resources(client: OCIClient, compartment_id: str, region: str) -> tuple[List[InstanceInfo], List[InstanceInfo], List[BastionInfo]]:
    """
    Collect all resources (OKE, ODO, Bastions) for a specific compartment and region.
    
    Returns:
        Tuple of (oke_instances, odo_instances, bastions)
    """
    oke_instances = collect_oke_instances(client, compartment_id, region)
    odo_instances = collect_odo_instances(client, compartment_id, region)
    bastions = collect_bastions(client, compartment_id, region)
    
    return oke_instances, odo_instances, bastions