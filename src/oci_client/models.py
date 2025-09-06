"""Data models for OCI client."""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict


class AuthType(str, Enum):
    """Authentication types supported."""
    SESSION_TOKEN = "session_token"
    API_KEY = "api_key"
    INSTANCE_PRINCIPAL = "instance_principal"
    RESOURCE_PRINCIPAL = "resource_principal"


class LifecycleState(str, Enum):
    """Common lifecycle states in OCI."""
    CREATING = "CREATING"
    PROVISIONING = "PROVISIONING"
    RUNNING = "RUNNING"
    ACTIVE = "ACTIVE"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    TERMINATING = "TERMINATING"
    TERMINATED = "TERMINATED"


class BastionType(str, Enum):
    """Types of bastions."""
    STANDARD = "STANDARD"
    INTERNAL = "INTERNAL"


@dataclass
class InstanceInfo:
    """Information about an OCI compute instance."""
    instance_id: str
    private_ip: str
    subnet_id: str
    display_name: Optional[str] = None
    cluster_name: Optional[str] = None
    public_ip: Optional[str] = None
    shape: Optional[str] = None
    availability_domain: Optional[str] = None
    fault_domain: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class BastionInfo:
    """Information about an OCI bastion."""
    bastion_id: str
    target_subnet_id: str
    bastion_name: Optional[str] = None
    bastion_type: BastionType = BastionType.INTERNAL
    max_session_ttl: int = 10800  # 3 hours default
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE


@dataclass
class SessionInfo:
    """Information about a bastion session."""
    session_id: str
    bastion_id: str
    target_resource_id: str
    target_resource_private_ip: str
    ssh_metadata: Dict[str, str] = field(default_factory=dict)
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE


class OCIConfig(BaseModel):
    """OCI configuration model with validation."""
    model_config = ConfigDict(validate_assignment=True)
    
    region: str
    profile_name: str = "DEFAULT"
    config_file: Optional[str] = None
    tenancy: Optional[str] = None
    user: Optional[str] = None
    fingerprint: Optional[str] = None
    key_file: Optional[str] = None
    security_token_file: Optional[str] = None
    pass_phrase: Optional[str] = None
    auth_type: AuthType = AuthType.SESSION_TOKEN
    
    def is_session_token_auth(self) -> bool:
        """Check if using session token authentication."""
        return self.security_token_file is not None
    
    def is_api_key_auth(self) -> bool:
        """Check if using API key authentication."""
        return (
            self.key_file is not None 
            and self.fingerprint is not None 
            and self.security_token_file is None
        )


class RegionInfo(BaseModel):
    """Region information model."""
    name: str
    key: str
    realm_key: Optional[str] = None
    internal_domain: Optional[str] = None
    is_home_region: bool = False


class CompartmentInfo(BaseModel):
    """Compartment information model."""
    id: str
    name: str
    description: Optional[str] = None
    parent_compartment_id: Optional[str] = None
    lifecycle_state: LifecycleState
    time_created: Optional[str] = None
