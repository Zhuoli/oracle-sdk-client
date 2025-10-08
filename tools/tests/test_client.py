"""Tests for main client module."""

from unittest.mock import Mock, patch

import pytest

from src.oci_client.client import OCIClient
from src.oci_client.models import (
    AuthType,
    BastionInfo,
    BastionType,
    InstanceInfo,
    LifecycleState,
    RegionInfo,
)


class TestOCIClient:
    """Test OCI Client."""

    @pytest.fixture
    def mock_auth_response(self):
        """Mock authentication response."""
        mock_config = {"region": "us-ashburn-1", "tenancy": "ocid1.tenancy.oc1..xxxxx"}
        mock_signer = Mock()
        return mock_config, mock_signer

    @pytest.fixture
    def mock_client(self, mock_auth_response):
        """Create a mock client instance."""
        with patch("src.oci_client.client.OCIAuthenticator") as mock_auth:
            mock_auth.return_value.authenticate.return_value = mock_auth_response
            client = OCIClient(region="us-ashburn-1", profile_name="test_profile")
            return client

    @patch("src.oci_client.client.OCIAuthenticator")
    def test_client_initialization(self, mock_auth):
        """Test client initialization."""
        mock_auth.return_value.authenticate.return_value = ({}, Mock())

        client = OCIClient(region="us-ashburn-1", profile_name="test_profile")

        assert client.config.region == "us-ashburn-1"
        assert client.config.profile_name == "test_profile"
        mock_auth.return_value.authenticate.assert_called_once()

    @patch("src.oci_client.client.OCIAuthenticator")
    def test_client_initialization_with_retry_strategy(self, mock_auth):
        """Test client initialization with custom retry strategy."""
        import oci.retry

        mock_auth.return_value.authenticate.return_value = ({}, Mock())
        custom_retry = oci.retry.DEFAULT_RETRY_STRATEGY

        client = OCIClient(
            region="us-ashburn-1", profile_name="test_profile", retry_strategy=custom_retry
        )

        assert client.retry_strategy == custom_retry

    def test_lazy_loading_compute_client(self, mock_client):
        """Test lazy loading of compute client."""
        with patch("src.oci_client.client.oci.core.ComputeClient") as mock_compute:
            # Initially None
            assert mock_client._compute_client is None

            # Access compute_client property
            _ = mock_client.compute_client

            # Should create compute client
            mock_compute.assert_called_once()

            # Second access should not create new client
            _ = mock_client.compute_client
            assert mock_compute.call_count == 1

    def test_lazy_loading_identity_client(self, mock_client):
        """Test lazy loading of identity client."""
        with patch("src.oci_client.client.oci.identity.IdentityClient") as mock_identity:
            assert mock_client._identity_client is None

            _ = mock_client.identity_client
            mock_identity.assert_called_once()

            _ = mock_client.identity_client
            assert mock_identity.call_count == 1

    def test_lazy_loading_bastion_client(self, mock_client):
        """Test lazy loading of bastion client."""
        with patch("src.oci_client.client.oci.bastion.BastionClient") as mock_bastion:
            assert mock_client._bastion_client is None

            _ = mock_client.bastion_client
            mock_bastion.assert_called_once()

    def test_lazy_loading_network_client(self, mock_client):
        """Test lazy loading of network client."""
        with patch("src.oci_client.client.oci.core.VirtualNetworkClient") as mock_network:
            assert mock_client._network_client is None

            _ = mock_client.network_client
            mock_network.assert_called_once()

    def test_lazy_loading_container_engine_client(self, mock_client):
        """Test lazy loading of container engine client."""
        with patch(
            "src.oci_client.client.oci.container_engine.ContainerEngineClient"
        ) as mock_ce:
            assert mock_client._container_engine_client is None

            _ = mock_client.container_engine_client
            mock_ce.assert_called_once()

    @patch("src.oci_client.client.console")
    def test_test_connection_success(self, mock_console, mock_client):
        """Test successful connection test."""
        mock_regions = Mock()
        mock_regions.data = [Mock(), Mock()]

        mock_identity = Mock()
        mock_identity.list_regions.return_value = mock_regions
        mock_client._identity_client = mock_identity

        result = mock_client.test_connection()

        assert result is True
        mock_identity.list_regions.assert_called_once()

    @patch("src.oci_client.client.console")
    def test_test_connection_failure(self, mock_console, mock_client):
        """Test failed connection test."""
        mock_identity = Mock()
        mock_identity.list_regions.side_effect = Exception("Connection failed")
        mock_client._identity_client = mock_identity

        result = mock_client.test_connection()

        assert result is False

    @patch("src.oci_client.client.logger")
    def test_get_region_info(self, mock_logger, mock_client):
        """Test getting region information."""
        mock_region = Mock()
        mock_region.name = "us-ashburn-1"
        mock_region.key = "IAD"

        mock_tenancy = Mock()
        mock_tenancy.home_region_key = "us-phoenix-1"

        mock_client.config.region = "us-ashburn-1"
        mock_client.oci_config = {"tenancy": "ocid1.tenancy.oc1..xxxxx"}

        mock_identity = Mock()
        mock_identity.list_regions.return_value.data = [mock_region]
        mock_identity.get_tenancy.return_value.data = mock_tenancy
        mock_client._identity_client = mock_identity

        region_info = mock_client.get_region_info()

        assert isinstance(region_info, RegionInfo)
        assert region_info.name == "us-ashburn-1"
        assert region_info.key == "iad"
        assert region_info.is_home_region is False

    @patch("requests.get")
    def test_get_internal_domain(self, mock_requests, mock_client):
        """Test getting internal domain."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"internal_realm_domain": "internal.oraclecloud.com"}
        mock_requests.return_value = mock_response

        with patch.object(mock_client, "get_region_info") as mock_region:
            mock_region.return_value = RegionInfo(name="us-ashburn-1", key="iad")

            domain = mock_client.get_internal_domain()

            assert domain == "internal.oraclecloud.com"

    @patch("src.oci_client.client.logger")
    def test_list_compartments(self, mock_logger, mock_client):
        """Test listing compartments."""
        mock_comp1 = Mock()
        mock_comp1.id = "ocid1.compartment.oc1..comp1"
        mock_comp1.name = "Compartment1"
        mock_comp1.description = "Test compartment 1"
        mock_comp1.lifecycle_state = "ACTIVE"

        mock_comp2 = Mock()
        mock_comp2.id = "ocid1.compartment.oc1..comp2"
        mock_comp2.name = "Compartment2"
        mock_comp2.description = "Test compartment 2"
        mock_comp2.lifecycle_state = "ACTIVE"

        mock_identity = Mock()
        mock_identity.get_compartment.return_value.data = mock_comp1
        mock_identity.list_compartments.return_value.data = [mock_comp2]
        mock_client._identity_client = mock_identity

        compartments = mock_client.list_compartments(
            parent_compartment_id="ocid1.compartment.oc1..parent", include_root=True
        )

        assert len(compartments) == 2
        assert compartments[0]["name"] == "Compartment1"
        assert compartments[1]["name"] == "Compartment2"

    @patch("src.oci_client.client.logger")
    def test_list_instances(self, mock_logger, mock_client):
        """Test listing instances."""
        mock_instance = Mock()
        mock_instance.id = "ocid1.instance.oc1..xxxxx"
        mock_instance.display_name = "test-instance"
        mock_instance.shape = "VM.Standard2.1"
        mock_instance.availability_domain = "AD-1"
        mock_instance.fault_domain = "FAULT-DOMAIN-1"
        mock_instance.metadata = {"test": "metadata"}
        mock_instance.extended_metadata = {}
        mock_instance.freeform_tags = {"env": "test"}
        mock_instance.defined_tags = {}
        mock_instance.lifecycle_state = "RUNNING"

        mock_compute = Mock()
        mock_compute.list_instances.return_value.data = [mock_instance]
        mock_compute.list_instances.return_value.has_next_page = False
        mock_client._compute_client = mock_compute

        with patch.object(mock_client, "_parse_instance") as mock_parse:
            mock_parse.return_value = InstanceInfo(
                instance_id="ocid1.instance.oc1..xxxxx",
                display_name="test-instance",
                private_ip="10.0.0.1",
                subnet_id="ocid1.subnet.oc1..xxxxx",
                shape="VM.Standard2.1",
            )

            instances = mock_client.list_instances(
                compartment_id="ocid1.compartment.oc1..xxxxx",
                lifecycle_state=LifecycleState.RUNNING,
            )

            assert len(instances) == 1
            assert instances[0].display_name == "test-instance"
            assert instances[0].private_ip == "10.0.0.1"

    def test_list_oke_instances(self, mock_client):
        """Test listing OKE instances."""
        oke_instance = InstanceInfo(
            instance_id="ocid1.instance.oc1..oke",
            display_name="oke-node-1",
            private_ip="10.0.0.2",
            subnet_id="ocid1.subnet.oc1..xxxxx",
            metadata={
                "oke-cluster-display-name": "test-cluster",
                "oke-initial-node-labels": {"tot.oraclecloud.com/node-pool-name": "pool1"},
            },
        )

        non_oke_instance = InstanceInfo(
            instance_id="ocid1.instance.oc1..regular",
            display_name="regular-instance",
            private_ip="10.0.0.3",
            subnet_id="ocid1.subnet.oc1..xxxxx",
            metadata={},
        )

        with patch.object(mock_client, "list_instances") as mock_list:
            mock_list.return_value = [oke_instance, non_oke_instance]

            oke_instances = mock_client.list_oke_instances(
                compartment_id="ocid1.compartment.oc1..xxxxx", cluster_name="test-cluster"
            )

            assert len(oke_instances) == 1
            assert oke_instances[0].display_name == "oke-node-1"
            assert oke_instances[0].cluster_name == "test-cluster"

    def test_list_odo_instances(self, mock_client):
        """Test listing ODO instances."""
        odo_instance = InstanceInfo(
            instance_id="ocid1.instance.oc1..odo",
            display_name="odo-instance",
            private_ip="10.0.0.4",
            subnet_id="ocid1.subnet.oc1..xxxxx",
            metadata={
                "extended_metadata": {
                    "compute_management": {"instance_configuration": {"state": "SUCCEEDED"}}
                }
            },
        )

        with patch.object(mock_client, "list_instances") as mock_list:
            mock_list.return_value = [odo_instance]

            odo_instances = mock_client.list_odo_instances(
                compartment_id="ocid1.compartment.oc1..xxxxx"
            )

            assert len(odo_instances) == 1
            assert odo_instances[0].display_name == "odo-instance"

    @patch("src.oci_client.client.logger")
    def test_list_bastions(self, mock_logger, mock_client):
        """Test listing bastions."""
        mock_bastion = Mock()
        mock_bastion.id = "ocid1.bastion.oc1..xxxxx"
        mock_bastion.name = "test-bastion"
        mock_bastion.target_subnet_id = "ocid1.subnet.oc1..xxxxx"
        mock_bastion.bastion_type = "INTERNAL"
        mock_bastion.max_session_ttl_in_seconds = 10800
        mock_bastion.lifecycle_state = "ACTIVE"

        mock_bastion_client = Mock()
        mock_bastion_client.list_bastions.return_value.data = [mock_bastion]
        mock_bastion_client.list_bastions.return_value.has_next_page = False
        mock_client._bastion_client = mock_bastion_client

        bastions = mock_client.list_bastions(
            compartment_id="ocid1.compartment.oc1..xxxxx", bastion_type=BastionType.INTERNAL
        )

        assert len(bastions) == 1
        assert bastions[0].bastion_name == "test-bastion"
        assert bastions[0].bastion_type == BastionType.INTERNAL

    def test_find_bastion_for_subnet(self, mock_client):
        """Test finding bastion for subnet."""
        bastion1 = BastionInfo(
            bastion_id="ocid1.bastion.oc1..bastion1",
            target_subnet_id="ocid1.subnet.oc1..subnet1",
            bastion_name="bastion1",
        )

        bastion2 = BastionInfo(
            bastion_id="ocid1.bastion.oc1..bastion2",
            target_subnet_id="ocid1.subnet.oc1..subnet2",
            bastion_name="bastion2",
        )

        result = mock_client.find_bastion_for_subnet(
            [bastion1, bastion2], "ocid1.subnet.oc1..subnet2"
        )

        assert result == bastion2

        result = mock_client.find_bastion_for_subnet(
            [bastion1, bastion2], "ocid1.subnet.oc1..subnet3"
        )

        assert result is None

    def test_refresh_auth_session_token(self, mock_client):
        """Test refreshing authentication for session token."""
        mock_client.config.auth_type = AuthType.SESSION_TOKEN

        with patch.object(mock_client.authenticator, "refresh_token") as mock_refresh:
            mock_refresh.return_value = True

            result = mock_client.refresh_auth()

            assert result is True
            mock_refresh.assert_called_once()

    def test_refresh_auth_api_key(self, mock_client):
        """Test refreshing authentication for API key."""
        mock_client.config.auth_type = AuthType.API_KEY

        result = mock_client.refresh_auth()

        assert result is True

    def test_context_manager(self, mock_auth_response):
        """Test client as context manager."""
        with patch("src.oci_client.client.OCIAuthenticator") as mock_auth:
            mock_auth.return_value.authenticate.return_value = mock_auth_response

            with OCIClient("us-ashburn-1", "test_profile") as client:
                assert client is not None
                assert client.config.region == "us-ashburn-1"
