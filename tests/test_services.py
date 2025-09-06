"""Tests for service modules."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from src.oci_client.services.compute import ComputeService
from src.oci_client.services.identity import IdentityService
from src.oci_client.services.bastion import BastionService
from src.oci_client.models import (
    LifecycleState, BastionType, BastionInfo, SessionInfo,
    CompartmentInfo, RegionInfo
)


class TestComputeService:
    """Test Compute Service."""
    
    @pytest.fixture
    def compute_service(self):
        """Create compute service with mock clients."""
        mock_compute_client = Mock()
        mock_network_client = Mock()
        return ComputeService(mock_compute_client, mock_network_client)
    
    def test_get_instance(self, compute_service):
        """Test getting instance details."""
        mock_instance = Mock()
        mock_instance.id = "ocid1.instance.oc1..xxxxx"
        mock_instance.display_name = "test-instance"
        mock_instance.shape = "VM.Standard2.1"
        mock_instance.shape_config.ocpus = 1
        mock_instance.shape_config.memory_in_gbs = 16
        mock_instance.availability_domain = "AD-1"
        mock_instance.fault_domain = "FD-1"
        mock_instance.lifecycle_state = "RUNNING"
        mock_instance.time_created = datetime.now()
        mock_instance.compartment_id = "ocid1.compartment.oc1..xxxxx"
        mock_instance.metadata = {"test": "metadata"}
        mock_instance.extended_metadata = {}
        mock_instance.freeform_tags = {"env": "test"}
        mock_instance.defined_tags = {}
        
        mock_boot_attachment = Mock()
        mock_boot_attachment.boot_volume_id = "ocid1.bootvolume.oc1..xxxxx"
        
        compute_service.compute_client.get_instance.return_value.data = mock_instance
        compute_service.compute_client.list_boot_volume_attachments.return_value.data = [mock_boot_attachment]
        compute_service.compute_client.list_volume_attachments.return_value.data = []
        
        result = compute_service.get_instance("ocid1.instance.oc1..xxxxx")
        
        assert result["id"] == "ocid1.instance.oc1..xxxxx"
        assert result["display_name"] == "test-instance"
        assert result["shape"] == "VM.Standard2.1"
        assert result["boot_volume_id"] == "ocid1.bootvolume.oc1..xxxxx"
    
    def test_start_instance(self, compute_service):
        """Test starting an instance."""
        result = compute_service.start_instance("ocid1.instance.oc1..xxxxx")
        
        assert result is True
        compute_service.compute_client.instance_action.assert_called_once_with(
            instance_id="ocid1.instance.oc1..xxxxx",
            action="START"
        )
    
    def test_stop_instance(self, compute_service):
        """Test stopping an instance."""
        result = compute_service.stop_instance("ocid1.instance.oc1..xxxxx", force=False)
        
        assert result is True
        compute_service.compute_client.instance_action.assert_called_once_with(
            instance_id="ocid1.instance.oc1..xxxxx",
            action="STOP"
        )
    
    def test_reboot_instance(self, compute_service):
        """Test rebooting an instance."""
        result = compute_service.reboot_instance("ocid1.instance.oc1..xxxxx", force=True)
        
        assert result is True
        compute_service.compute_client.instance_action.assert_called_once_with(
            instance_id="ocid1.instance.oc1..xxxxx",
            action="RESET"
        )


class TestIdentityService:
    """Test Identity Service."""
    
    @pytest.fixture
    def identity_service(self):
        """Create identity service with mock client."""
        mock_identity_client = Mock()
        return IdentityService(mock_identity_client)
    
    def test_get_tenancy(self, identity_service):
        """Test getting tenancy information."""
        mock_tenancy = Mock()
        mock_tenancy.id = "ocid1.tenancy.oc1..xxxxx"
        mock_tenancy.name = "test-tenancy"
        mock_tenancy.description = "Test tenancy"
        mock_tenancy.home_region_key = "us-phoenix-1"
        mock_tenancy.freeform_tags = {"env": "test"}
        mock_tenancy.defined_tags = {}
        
        identity_service.identity_client.get_tenancy.return_value.data = mock_tenancy
        
        result = identity_service.get_tenancy("ocid1.tenancy.oc1..xxxxx")
        
        assert result["id"] == "ocid1.tenancy.oc1..xxxxx"
        assert result["name"] == "test-tenancy"
        assert result["home_region_key"] == "us-phoenix-1"
    
    def test_list_regions(self, identity_service):
        """Test listing regions."""
        mock_region1 = Mock()
        mock_region1.name = "us-phoenix-1"
        mock_region1.key = "PHX"
        mock_region1.realm_key = "oc1"
        
        mock_region2 = Mock()
        mock_region2.name = "us-ashburn-1"
        mock_region2.key = "IAD"
        mock_region2.realm_key = "oc1"
        
        identity_service.identity_client.list_regions.return_value.data = [mock_region1, mock_region2]
        
        regions = identity_service.list_regions()
        
        assert len(regions) == 2
        assert isinstance(regions[0], RegionInfo)
        assert regions[0].name == "us-phoenix-1"
        assert regions[0].key == "PHX"
        assert regions[1].name == "us-ashburn-1"
    
    def test_list_availability_domains(self, identity_service):
        """Test listing availability domains."""
        mock_ad1 = Mock()
        mock_ad1.name = "AD-1"
        mock_ad1.id = "ocid1.ad.oc1..ad1"
        mock_ad1.compartment_id = "ocid1.compartment.oc1..xxxxx"
        
        mock_ad2 = Mock()
        mock_ad2.name = "AD-2"
        mock_ad2.id = "ocid1.ad.oc1..ad2"
        mock_ad2.compartment_id = "ocid1.compartment.oc1..xxxxx"
        
        identity_service.identity_client.list_availability_domains.return_value.data = [mock_ad1, mock_ad2]
        
        ads = identity_service.list_availability_domains("ocid1.compartment.oc1..xxxxx")
        
        assert len(ads) == 2
        assert ads[0]["name"] == "AD-1"
        assert ads[1]["name"] == "AD-2"
    
    def test_list_compartments(self, identity_service):
        """Test listing compartments."""
        mock_comp = Mock()
        mock_comp.id = "ocid1.compartment.oc1..xxxxx"
        mock_comp.name = "test-compartment"
        mock_comp.description = "Test compartment"
        mock_comp.lifecycle_state = "ACTIVE"
        mock_comp.time_created = datetime.now()
        
        identity_service.identity_client.list_compartments.return_value.data = [mock_comp]
        identity_service.identity_client.list_compartments.return_value.has_next_page = False
        
        compartments = identity_service.list_compartments(
            compartment_id="ocid1.compartment.oc1..parent",
            lifecycle_state=LifecycleState.ACTIVE
        )
        
        assert len(compartments) == 1
        assert isinstance(compartments[0], CompartmentInfo)
        assert compartments[0].name == "test-compartment"
    
    def test_list_users(self, identity_service):
        """Test listing users."""
        mock_user = Mock()
        mock_user.id = "ocid1.user.oc1..xxxxx"
        mock_user.name = "test-user"
        mock_user.description = "Test user"
        mock_user.email = "test@example.com"
        mock_user.lifecycle_state = "ACTIVE"
        mock_user.time_created = datetime.now()
        mock_user.is_mfa_activated = True
        mock_user.freeform_tags = {}
        mock_user.defined_tags = {}
        
        identity_service.identity_client.list_users.return_value.data = [mock_user]
        
        users = identity_service.list_users("ocid1.compartment.oc1..xxxxx")
        
        assert len(users) == 1
        assert users[0]["name"] == "test-user"
        assert users[0]["email"] == "test@example.com"
        assert users[0]["is_mfa_activated"] is True
    
    def test_list_policies(self, identity_service):
        """Test listing policies."""
        mock_policy = Mock()
        mock_policy.id = "ocid1.policy.oc1..xxxxx"
        mock_policy.name = "test-policy"
        mock_policy.description = "Test policy"
        mock_policy.statements = ["Allow group TestGroup to read all-resources in compartment TestCompartment"]
        mock_policy.lifecycle_state = "ACTIVE"
        mock_policy.time_created = datetime.now()
        mock_policy.freeform_tags = {}
        mock_policy.defined_tags = {}
        
        identity_service.identity_client.list_policies.return_value.data = [mock_policy]
        
        policies = identity_service.list_policies("ocid1.compartment.oc1..xxxxx")
        
        assert len(policies) == 1
        assert policies[0]["name"] == "test-policy"
        assert len(policies[0]["statements"]) == 1


class TestBastionService:
    """Test Bastion Service."""
    
    @pytest.fixture
    def bastion_service(self):
        """Create bastion service with mock client."""
        mock_bastion_client = Mock()
        return BastionService(mock_bastion_client)
    
    def test_get_bastion(self, bastion_service):
        """Test getting bastion details."""
        mock_bastion = Mock()
        mock_bastion.id = "ocid1.bastion.oc1..xxxxx"
        mock_bastion.name = "test-bastion"
        mock_bastion.target_subnet_id = "ocid1.subnet.oc1..xxxxx"
        mock_bastion.bastion_type = "INTERNAL"
        mock_bastion.max_session_ttl_in_seconds = 10800
        mock_bastion.lifecycle_state = "ACTIVE"
        
        bastion_service.bastion_client.get_bastion.return_value.data = mock_bastion
        
        result = bastion_service.get_bastion("ocid1.bastion.oc1..xxxxx")
        
        assert isinstance(result, BastionInfo)
        assert result.bastion_id == "ocid1.bastion.oc1..xxxxx"
        assert result.bastion_name == "test-bastion"
        assert result.bastion_type == BastionType.INTERNAL
    
    def test_list_bastions(self, bastion_service):
        """Test listing bastions."""
        mock_bastion = Mock()
        mock_bastion.id = "ocid1.bastion.oc1..xxxxx"
        mock_bastion.name = "test-bastion"
        mock_bastion.target_subnet_id = "ocid1.subnet.oc1..xxxxx"
        mock_bastion.bastion_type = "STANDARD"
        mock_bastion.max_session_ttl_in_seconds = 10800
        mock_bastion.lifecycle_state = "ACTIVE"
        
        bastion_service.bastion_client.list_bastions.return_value.data = [mock_bastion]
        bastion_service.bastion_client.list_bastions.return_value.has_next_page = False
        
        bastions = bastion_service.list_bastions(
            compartment_id="ocid1.compartment.oc1..xxxxx",
            bastion_type=BastionType.STANDARD
        )
        
        assert len(bastions) == 1
        assert isinstance(bastions[0], BastionInfo)
        assert bastions[0].bastion_type == BastionType.STANDARD
    
    @patch("src.oci_client.services.bastion.oci.bastion.models.CreateBastionDetails")
    def test_create_bastion(self, mock_details, bastion_service):
        """Test creating a bastion."""
        mock_bastion = Mock()
        mock_bastion.id = "ocid1.bastion.oc1..new"
        mock_bastion.name = "new-bastion"
        mock_bastion.target_subnet_id = "ocid1.subnet.oc1..xxxxx"
        mock_bastion.bastion_type = "INTERNAL"
        mock_bastion.max_session_ttl_in_seconds = 7200
        mock_bastion.lifecycle_state = "CREATING"
        
        bastion_service.bastion_client.create_bastion.return_value.data = mock_bastion
        
        result = bastion_service.create_bastion(
            compartment_id="ocid1.compartment.oc1..xxxxx",
            target_subnet_id="ocid1.subnet.oc1..xxxxx",
            name="new-bastion",
            bastion_type=BastionType.INTERNAL,
            max_session_ttl=7200
        )
        
        assert isinstance(result, BastionInfo)
        assert result.bastion_id == "ocid1.bastion.oc1..new"
        assert result.bastion_name == "new-bastion"
    
    def test_delete_bastion(self, bastion_service):
        """Test deleting a bastion."""
        result = bastion_service.delete_bastion("ocid1.bastion.oc1..xxxxx")
        
        assert result is True
        bastion_service.bastion_client.delete_bastion.assert_called_once_with("ocid1.bastion.oc1..xxxxx")
    
    @patch("builtins.open", create=True)
    @patch("src.oci_client.services.bastion.Path")
    def test_create_session_with_existing_key(self, mock_path, mock_open, bastion_service):
        """Test creating a session with existing SSH key."""
        # Setup SSH key mocks
        mock_path.home.return_value = Mock()
        mock_ssh_path = Mock()
        mock_path.home.return_value.__truediv__.return_value = mock_ssh_path
        mock_pub_key_path = Mock()
        mock_ssh_path.__truediv__.return_value = mock_pub_key_path
        mock_pub_key_path.exists.return_value = True
        
        mock_open.return_value.__enter__.return_value.read.return_value = "ssh-rsa AAAAB3... test-key"
        
        # Setup session mocks
        mock_session = Mock()
        mock_session.id = "ocid1.bastionsession.oc1..xxxxx"
        mock_session.bastion_id = "ocid1.bastion.oc1..xxxxx"
        mock_session.lifecycle_state = "ACTIVE"
        mock_session.ssh_metadata = {"command": "ssh -o ProxyCommand='...' opc@10.0.0.1"}
        
        bastion_service.bastion_client.create_session.return_value.data.id = "ocid1.bastionsession.oc1..xxxxx"
        bastion_service.bastion_client.get_session.return_value.data = mock_session
        
        result = bastion_service.create_session(
            bastion_id="ocid1.bastion.oc1..xxxxx",
            target_resource_id="ocid1.instance.oc1..xxxxx",
            target_private_ip="10.0.0.1"
        )
        
        assert isinstance(result, SessionInfo)
        assert result.session_id == "ocid1.bastionsession.oc1..xxxxx"
        assert result.target_resource_private_ip == "10.0.0.1"
    
    def test_get_session(self, bastion_service):
        """Test getting session details."""
        mock_session = Mock()
        mock_session.id = "ocid1.bastionsession.oc1..xxxxx"
        mock_session.bastion_id = "ocid1.bastion.oc1..xxxxx"
        mock_session.lifecycle_state = "ACTIVE"
        mock_session.ssh_metadata = {"command": "ssh command"}
        
        mock_target = Mock()
        mock_target.target_resource_id = "ocid1.instance.oc1..xxxxx"
        mock_target.target_resource_private_ip_address = "10.0.0.1"
        mock_session.target_resource_details = mock_target
        
        bastion_service.bastion_client.get_session.return_value.data = mock_session
        
        result = bastion_service.get_session("ocid1.bastionsession.oc1..xxxxx")
        
        assert isinstance(result, SessionInfo)
        assert result.session_id == "ocid1.bastionsession.oc1..xxxxx"
        assert result.target_resource_private_ip == "10.0.0.1"
    
    def test_list_sessions(self, bastion_service):
        """Test listing sessions."""
        mock_session = Mock()
        mock_session.id = "ocid1.bastionsession.oc1..xxxxx"
        mock_session.bastion_id = "ocid1.bastion.oc1..xxxxx"
        mock_session.lifecycle_state = "ACTIVE"
        mock_session.ssh_metadata = {}
        
        mock_target = Mock()
        mock_target.target_resource_id = "ocid1.instance.oc1..xxxxx"
        mock_target.target_resource_private_ip_address = "10.0.0.1"
        mock_session.target_resource_details = mock_target
        
        bastion_service.bastion_client.list_sessions.return_value.data = [mock_session]
        bastion_service.bastion_client.list_sessions.return_value.has_next_page = False
        
        sessions = bastion_service.list_sessions(
            bastion_id="ocid1.bastion.oc1..xxxxx",
            lifecycle_state=LifecycleState.ACTIVE
        )
        
        assert len(sessions) == 1
        assert isinstance(sessions[0], SessionInfo)
        assert sessions[0].lifecycle_state == LifecycleState.ACTIVE
    
    def test_delete_session(self, bastion_service):
        """Test deleting a session."""
        result = bastion_service.delete_session("ocid1.bastionsession.oc1..xxxxx")
        
        assert result is True
        bastion_service.bastion_client.delete_session.assert_called_once_with("ocid1.bastionsession.oc1..xxxxx")
    
    def test_get_ssh_command(self, bastion_service):
        """Test generating SSH command from session."""
        session = SessionInfo(
            session_id="ocid1.bastionsession.oc1..xxxxx",
            bastion_id="ocid1.bastion.oc1..xxxxx",
            target_resource_id="ocid1.instance.oc1..xxxxx",
            target_resource_private_ip="10.0.0.1",
            ssh_metadata={
                "command": "ssh -o ProxyCommand='nc proxy 22' opc@10.0.0.1",
                "username": "opc",
                "proxy_command": "nc proxy 22"
            }
        )
        
        command = bastion_service.get_ssh_command(session)
        
        assert command == "ssh -o ProxyCommand='nc proxy 22' opc@10.0.0.1"
        
        # Test with only proxy_command
        session.ssh_metadata = {
            "username": "ubuntu",
            "proxy_command": "nc proxy 22"
        }
        
        command = bastion_service.get_ssh_command(session)
        
        assert command == "ssh -o ProxyCommand='nc proxy 22' ubuntu@10.0.0.1"
        
        # Test with no metadata
        session.ssh_metadata = {}
        
        command = bastion_service.get_ssh_command(session)
        
        assert command is None