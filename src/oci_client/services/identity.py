"""Identity service operations for OCI."""

import logging
from typing import List, Optional, Dict, Any
from functools import lru_cache

import oci
from tenacity import retry, stop_after_attempt, wait_exponential

from ..models import LifecycleState, CompartmentInfo, RegionInfo

logger = logging.getLogger(__name__)


class IdentityService:
    """Service class for identity-related operations."""
    
    def __init__(self, identity_client: oci.identity.IdentityClient):
        """Initialize identity service."""
        self.identity_client = identity_client
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def get_tenancy(self, tenancy_id: str) -> Dict[str, Any]:
        """Get tenancy information."""
        try:
            tenancy = self.identity_client.get_tenancy(tenancy_id).data
            
            return {
                "id": tenancy.id,
                "name": tenancy.name,
                "description": tenancy.description,
                "home_region_key": tenancy.home_region_key,
                "freeform_tags": tenancy.freeform_tags,
                "defined_tags": tenancy.defined_tags
            }
        except Exception as e:
            logger.error(f"Failed to get tenancy {tenancy_id}: {e}")
            raise
    
    @lru_cache(maxsize=32)
    def list_regions(self) -> List[RegionInfo]:
        """List all available regions."""
        try:
            regions = self.identity_client.list_regions().data
            
            return [
                RegionInfo(
                    name=region.name,
                    key=region.key,
                    realm_key=getattr(region, 'realm_key', None)
                )
                for region in regions
            ]
        except Exception as e:
            logger.error(f"Failed to list regions: {e}")
            raise
    
    def list_availability_domains(self, compartment_id: str) -> List[Dict[str, Any]]:
        """List availability domains in a compartment."""
        try:
            ads = self.identity_client.list_availability_domains(compartment_id).data
            
            return [
                {
                    "name": ad.name,
                    "id": ad.id,
                    "compartment_id": ad.compartment_id
                }
                for ad in ads
            ]
        except Exception as e:
            logger.error(f"Failed to list availability domains: {e}")
            raise
    
    def list_compartments(
        self,
        compartment_id: str,
        compartment_id_in_subtree: bool = False,
        access_level: str = "ACCESSIBLE",
        lifecycle_state: Optional[LifecycleState] = None
    ) -> List[CompartmentInfo]:
        """List compartments."""
        try:
            kwargs = {
                "compartment_id": compartment_id,
                "compartment_id_in_subtree": compartment_id_in_subtree,
                "access_level": access_level
            }
            
            if lifecycle_state:
                kwargs["lifecycle_state"] = lifecycle_state.value
            
            compartments = []
            response = self.identity_client.list_compartments(**kwargs)
            
            while response.data:
                for comp in response.data:
                    compartments.append(
                        CompartmentInfo(
                            id=comp.id,
                            name=comp.name,
                            description=comp.description,
                            parent_compartment_id=compartment_id,
                            lifecycle_state=LifecycleState(comp.lifecycle_state),
                            time_created=comp.time_created.isoformat() if comp.time_created else None
                        )
                    )
                
                if response.has_next_page:
                    response = self.identity_client.list_compartments(
                        **kwargs,
                        page=response.next_page
                    )
                else:
                    break
            
            return compartments
            
        except Exception as e:
            logger.error(f"Failed to list compartments: {e}")
            raise
    
    def get_compartment(self, compartment_id: str) -> CompartmentInfo:
        """Get a specific compartment."""
        try:
            comp = self.identity_client.get_compartment(compartment_id).data
            
            return CompartmentInfo(
                id=comp.id,
                name=comp.name,
                description=comp.description,
                parent_compartment_id=comp.compartment_id,
                lifecycle_state=LifecycleState(comp.lifecycle_state),
                time_created=comp.time_created.isoformat() if comp.time_created else None
            )
        except Exception as e:
            logger.error(f"Failed to get compartment {compartment_id}: {e}")
            raise
    
    def list_users(self, compartment_id: str) -> List[Dict[str, Any]]:
        """List users in a compartment."""
        try:
            users = self.identity_client.list_users(compartment_id).data
            
            return [
                {
                    "id": user.id,
                    "name": user.name,
                    "description": user.description,
                    "email": user.email,
                    "lifecycle_state": user.lifecycle_state,
                    "time_created": user.time_created.isoformat() if user.time_created else None,
                    "is_mfa_activated": user.is_mfa_activated,
                    "freeform_tags": user.freeform_tags,
                    "defined_tags": user.defined_tags
                }
                for user in users
            ]
        except Exception as e:
            logger.error(f"Failed to list users: {e}")
            raise
    
    def list_groups(self, compartment_id: str) -> List[Dict[str, Any]]:
        """List groups in a compartment."""
        try:
            groups = self.identity_client.list_groups(compartment_id).data
            
            return [
                {
                    "id": group.id,
                    "name": group.name,
                    "description": group.description,
                    "lifecycle_state": group.lifecycle_state,
                    "time_created": group.time_created.isoformat() if group.time_created else None,
                    "freeform_tags": group.freeform_tags,
                    "defined_tags": group.defined_tags
                }
                for group in groups
            ]
        except Exception as e:
            logger.error(f"Failed to list groups: {e}")
            raise
    
    def list_policies(self, compartment_id: str) -> List[Dict[str, Any]]:
        """List policies in a compartment."""
        try:
            policies = self.identity_client.list_policies(compartment_id).data
            
            return [
                {
                    "id": policy.id,
                    "name": policy.name,
                    "description": policy.description,
                    "statements": policy.statements,
                    "lifecycle_state": policy.lifecycle_state,
                    "time_created": policy.time_created.isoformat() if policy.time_created else None,
                    "freeform_tags": policy.freeform_tags,
                    "defined_tags": policy.defined_tags
                }
                for policy in policies
            ]
        except Exception as e:
            logger.error(f"Failed to list policies: {e}")
            raise
    
    def list_tag_namespaces(self, compartment_id: str) -> List[Dict[str, Any]]:
        """List tag namespaces in a compartment."""
        try:
            namespaces = self.identity_client.list_tag_namespaces(compartment_id).data
            
            return [
                {
                    "id": ns.id,
                    "name": ns.name,
                    "description": ns.description,
                    "is_retired": ns.is_retired,
                    "lifecycle_state": ns.lifecycle_state,
                    "time_created": ns.time_created.isoformat() if ns.time_created else None,
                    "freeform_tags": ns.freeform_tags,
                    "defined_tags": ns.defined_tags
                }
                for ns in namespaces
            ]
        except Exception as e:
            logger.error(f"Failed to list tag namespaces: {e}")
            raise
    
    def list_cost_tracking_tags(self, compartment_id: str) -> List[Dict[str, Any]]:
        """List cost tracking tags in a compartment."""
        try:
            tags = self.identity_client.list_cost_tracking_tags(compartment_id).data
            
            return [
                {
                    "tag_namespace_id": tag.tag_namespace_id,
                    "tag_namespace_name": tag.tag_namespace_name,
                    "tag_definition_id": tag.tag_definition_id,
                    "tag_definition_name": tag.tag_definition_name,
                    "is_cost_tracking": tag.is_cost_tracking,
                    "is_retired": tag.is_retired
                }
                for tag in tags
            ]
        except Exception as e:
            logger.error(f"Failed to list cost tracking tags: {e}")
            raise
    
    def get_user(self, user_id: str) -> Dict[str, Any]:
        """Get a specific user."""
        try:
            user = self.identity_client.get_user(user_id).data
            
            return {
                "id": user.id,
                "name": user.name,
                "description": user.description,
                "email": user.email,
                "lifecycle_state": user.lifecycle_state,
                "time_created": user.time_created.isoformat() if user.time_created else None,
                "is_mfa_activated": user.is_mfa_activated,
                "capabilities": {
                    "can_use_console_password": user.capabilities.can_use_console_password,
                    "can_use_api_keys": user.capabilities.can_use_api_keys,
                    "can_use_auth_tokens": user.capabilities.can_use_auth_tokens,
                    "can_use_smtp_credentials": user.capabilities.can_use_smtp_credentials,
                    "can_use_db_credentials": user.capabilities.can_use_db_credentials,
                    "can_use_customer_secret_keys": user.capabilities.can_use_customer_secret_keys
                } if user.capabilities else {},
                "freeform_tags": user.freeform_tags,
                "defined_tags": user.defined_tags
            }
        except Exception as e:
            logger.error(f"Failed to get user {user_id}: {e}")
            raise