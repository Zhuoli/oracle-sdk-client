"""Bastion service operations for OCI."""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from pathlib import Path

import oci
from tenacity import retry, stop_after_attempt, wait_exponential

from ..models import BastionInfo, SessionInfo, LifecycleState, BastionType

logger = logging.getLogger(__name__)


class BastionService:
    """Service class for bastion-related operations."""
    
    def __init__(self, bastion_client: oci.bastion.BastionClient):
        """Initialize bastion service."""
        self.bastion_client = bastion_client
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def get_bastion(self, bastion_id: str) -> BastionInfo:
        """Get details of a specific bastion."""
        try:
            bastion = self.bastion_client.get_bastion(bastion_id).data
            
            return BastionInfo(
                bastion_id=bastion.id,
                target_subnet_id=bastion.target_subnet_id,
                bastion_name=bastion.name,
                bastion_type=BastionType(bastion.bastion_type),
                max_session_ttl=bastion.max_session_ttl_in_seconds,
                lifecycle_state=LifecycleState(bastion.lifecycle_state)
            )
        except Exception as e:
            logger.error(f"Failed to get bastion {bastion_id}: {e}")
            raise
    
    def list_bastions(
        self,
        compartment_id: str,
        bastion_type: Optional[BastionType] = None,
        lifecycle_state: Optional[LifecycleState] = None,
        name: Optional[str] = None
    ) -> List[BastionInfo]:
        """List bastions in a compartment with filtering options."""
        try:
            kwargs = {"compartment_id": compartment_id}
            
            if bastion_type:
                kwargs["bastion_type"] = bastion_type.value
            if lifecycle_state:
                kwargs["lifecycle_state"] = lifecycle_state.value
            if name:
                kwargs["name"] = name
            
            bastions = []
            response = self.bastion_client.list_bastions(**kwargs)
            
            while response.data:
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
                
                if response.has_next_page:
                    response = self.bastion_client.list_bastions(
                        **kwargs,
                        page=response.next_page
                    )
                else:
                    break
            
            return bastions
            
        except Exception as e:
            logger.error(f"Failed to list bastions: {e}")
            raise
    
    def create_bastion(
        self,
        compartment_id: str,
        target_subnet_id: str,
        name: str,
        bastion_type: BastionType = BastionType.STANDARD,
        client_cidr_list: Optional[List[str]] = None,
        max_session_ttl: int = 10800,
        freeform_tags: Optional[Dict[str, str]] = None,
        defined_tags: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> BastionInfo:
        """Create a new bastion."""
        try:
            details = oci.bastion.models.CreateBastionDetails(
                compartment_id=compartment_id,
                target_subnet_id=target_subnet_id,
                name=name,
                bastion_type=bastion_type.value,
                client_cidr_block_allow_list=client_cidr_list or ["0.0.0.0/0"],
                max_session_ttl_in_seconds=max_session_ttl,
                freeform_tags=freeform_tags or {},
                defined_tags=defined_tags or {}
            )
            
            response = self.bastion_client.create_bastion(details)
            bastion = response.data
            
            logger.info(f"Created bastion {bastion.id} with name {name}")
            
            return BastionInfo(
                bastion_id=bastion.id,
                target_subnet_id=bastion.target_subnet_id,
                bastion_name=bastion.name,
                bastion_type=BastionType(bastion.bastion_type),
                max_session_ttl=bastion.max_session_ttl_in_seconds,
                lifecycle_state=LifecycleState(bastion.lifecycle_state)
            )
            
        except Exception as e:
            logger.error(f"Failed to create bastion: {e}")
            raise
    
    def delete_bastion(self, bastion_id: str) -> bool:
        """Delete a bastion."""
        try:
            self.bastion_client.delete_bastion(bastion_id)
            logger.info(f"Deleted bastion {bastion_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete bastion {bastion_id}: {e}")
            return False
    
    def create_session(
        self,
        bastion_id: str,
        target_resource_id: str,
        target_private_ip: str,
        session_type: str = "MANAGED_SSH",
        session_ttl: int = 10800,
        display_name: Optional[str] = None,
        key_type: str = "PUB",
        public_key_content: Optional[str] = None
    ) -> SessionInfo:
        """Create a new bastion session."""
        try:
            # Get or generate SSH key
            if not public_key_content:
                public_key_content = self._get_or_generate_ssh_key()
            
            # Create session details based on type
            if session_type == "MANAGED_SSH":
                target_details = oci.bastion.models.CreateManagedSshSessionTargetResourceDetails(
                    session_type=session_type,
                    target_resource_id=target_resource_id,
                    target_resource_private_ip_address=target_private_ip
                )
            elif session_type == "PORT_FORWARDING":
                target_details = oci.bastion.models.CreatePortForwardingSessionTargetResourceDetails(
                    session_type=session_type,
                    target_resource_id=target_resource_id,
                    target_resource_private_ip_address=target_private_ip,
                    target_resource_port=22  # Default SSH port
                )
            else:
                raise ValueError(f"Unsupported session type: {session_type}")
            
            # Create key details
            key_details = oci.bastion.models.PublicKeyDetails(
                public_key_content=public_key_content
            )
            
            # Create session
            details = oci.bastion.models.CreateSessionDetails(
                bastion_id=bastion_id,
                target_resource_details=target_details,
                key_details=key_details,
                session_ttl_in_seconds=session_ttl,
                display_name=display_name
            )
            
            response = self.bastion_client.create_session(details)
            session = self.bastion_client.get_session(response.data.id).data
            
            logger.info(f"Created bastion session {session.id}")
            
            return SessionInfo(
                session_id=session.id,
                bastion_id=session.bastion_id,
                target_resource_id=target_resource_id,
                target_resource_private_ip=target_private_ip,
                ssh_metadata=session.ssh_metadata or {},
                lifecycle_state=LifecycleState(session.lifecycle_state)
            )
            
        except Exception as e:
            logger.error(f"Failed to create bastion session: {e}")
            raise
    
    def get_session(self, session_id: str) -> SessionInfo:
        """Get details of a specific session."""
        try:
            session = self.bastion_client.get_session(session_id).data
            
            # Extract target info based on session type
            target_details = session.target_resource_details
            target_resource_id = None
            target_private_ip = None
            
            if hasattr(target_details, 'target_resource_id'):
                target_resource_id = target_details.target_resource_id
            if hasattr(target_details, 'target_resource_private_ip_address'):
                target_private_ip = target_details.target_resource_private_ip_address
            
            return SessionInfo(
                session_id=session.id,
                bastion_id=session.bastion_id,
                target_resource_id=target_resource_id,
                target_resource_private_ip=target_private_ip,
                ssh_metadata=session.ssh_metadata or {},
                lifecycle_state=LifecycleState(session.lifecycle_state)
            )
            
        except Exception as e:
            logger.error(f"Failed to get session {session_id}: {e}")
            raise
    
    def list_sessions(
        self,
        bastion_id: str,
        lifecycle_state: Optional[LifecycleState] = None,
        session_name: Optional[str] = None
    ) -> List[SessionInfo]:
        """List sessions for a bastion."""
        try:
            kwargs = {"bastion_id": bastion_id}
            
            if lifecycle_state:
                kwargs["session_lifecycle_state"] = lifecycle_state.value
            if session_name:
                kwargs["display_name"] = session_name
            
            sessions = []
            response = self.bastion_client.list_sessions(**kwargs)
            
            while response.data:
                for session in response.data:
                    # Extract target info
                    target_details = session.target_resource_details
                    target_resource_id = None
                    target_private_ip = None
                    
                    if hasattr(target_details, 'target_resource_id'):
                        target_resource_id = target_details.target_resource_id
                    if hasattr(target_details, 'target_resource_private_ip_address'):
                        target_private_ip = target_details.target_resource_private_ip_address
                    
                    sessions.append(
                        SessionInfo(
                            session_id=session.id,
                            bastion_id=session.bastion_id,
                            target_resource_id=target_resource_id,
                            target_resource_private_ip=target_private_ip,
                            ssh_metadata=session.ssh_metadata or {},
                            lifecycle_state=LifecycleState(session.lifecycle_state)
                        )
                    )
                
                if response.has_next_page:
                    response = self.bastion_client.list_sessions(
                        **kwargs,
                        page=response.next_page
                    )
                else:
                    break
            
            return sessions
            
        except Exception as e:
            logger.error(f"Failed to list sessions: {e}")
            raise
    
    def delete_session(self, session_id: str) -> bool:
        """Delete a bastion session."""
        try:
            self.bastion_client.delete_session(session_id)
            logger.info(f"Deleted session {session_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete session {session_id}: {e}")
            return False
    
    def get_ssh_command(self, session: SessionInfo) -> Optional[str]:
        """Generate SSH command for a session."""
        try:
            if session.ssh_metadata:
                command = session.ssh_metadata.get("command")
                if command:
                    return command
            
            # If no command in metadata, construct it
            if session.ssh_metadata:
                username = session.ssh_metadata.get("username", "opc")
                proxy_command = session.ssh_metadata.get("proxy_command")
                
                if proxy_command:
                    return f"ssh -o ProxyCommand='{proxy_command}' {username}@{session.target_resource_private_ip}"
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to generate SSH command: {e}")
            return None
    
    def _get_or_generate_ssh_key(self) -> str:
        """Get existing SSH public key or generate a new one."""
        ssh_path = Path.home() / ".ssh"
        pub_key_path = ssh_path / "id_rsa.pub"
        
        # Check for existing key
        if pub_key_path.exists():
            try:
                with open(pub_key_path, 'r') as f:
                    key_content = f.read().strip()
                    if key_content:
                        return key_content
            except Exception as e:
                logger.warning(f"Could not read existing SSH key: {e}")
        
        # Generate new key pair if needed
        priv_key_path = ssh_path / "id_rsa"
        
        try:
            import subprocess
            
            if not priv_key_path.exists():
                ssh_path.mkdir(mode=0o700, exist_ok=True)
                
                result = subprocess.run(
                    [
                        "ssh-keygen", "-t", "rsa", "-b", "2048",
                        "-f", str(priv_key_path),
                        "-N", "",  # No passphrase
                        "-C", "oci-bastion-key"
                    ],
                    capture_output=True,
                    text=True,
                    check=True
                )
                
                logger.info("Generated new SSH key pair")
            
            with open(pub_key_path, 'r') as f:
                return f.read().strip()
                
        except Exception as e:
            logger.error(f"Failed to generate SSH key: {e}")
            raise RuntimeError(f"Could not get or generate SSH key: {e}")