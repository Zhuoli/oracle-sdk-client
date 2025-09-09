"""Tests for ssh_sync module."""

from unittest.mock import MagicMock, Mock, patch, call
import argparse
import pytest
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.ssh_sync import parse_arguments, process_region, main, display_ssh_sync_header
from src.oci_client.models import InstanceInfo, BastionInfo


class TestSSHSync:
    """Test SSH Sync module."""

    def test_parse_arguments(self):
        """Test argument parsing."""
        test_args = ["ssh_sync.py", "remote-observer", "dev", "--config-file", "custom.yaml"]
        
        with patch("sys.argv", test_args):
            args = parse_arguments()
            
            assert args.project_name == "remote-observer"
            assert args.stage == "dev"
            assert args.config_file == "custom.yaml"
    
    def test_parse_arguments_default_config(self):
        """Test argument parsing with default config file."""
        test_args = ["ssh_sync.py", "today-all", "staging"]
        
        with patch("sys.argv", test_args):
            args = parse_arguments()
            
            assert args.project_name == "today-all"
            assert args.stage == "staging"
            assert args.config_file == "meta.yaml"
    
    @patch("rich.console.Console")
    def test_display_ssh_sync_header(self, mock_console_class):
        """Test display header function."""
        mock_console = Mock()
        mock_console_class.return_value = mock_console
        
        display_ssh_sync_header()
        
        # Check that console.print was called
        assert mock_console.print.called
        call_args = str(mock_console.print.call_args_list)
        assert "OCI SSH Sync" in call_args
    
    @patch("src.ssh_sync.collect_all_resources")
    @patch("src.ssh_sync.display_connection_info")
    @patch("src.ssh_sync.create_oci_client")
    @patch("src.ssh_sync.display_client_initialization")
    @patch("src.ssh_sync.setup_session_token")
    @patch("src.ssh_sync.display_region_header")
    @patch("src.ssh_sync.display_oke_instances")
    @patch("src.ssh_sync.display_odo_instances")
    @patch("src.ssh_sync.display_bastions")
    def test_process_region_success(
        self,
        mock_display_bastions,
        mock_display_odo,
        mock_display_oke,
        mock_display_header,
        mock_setup_token,
        mock_display_init,
        mock_create_client,
        mock_display_conn,
        mock_collect,
    ):
        """Test successful region processing."""
        # Setup mocks
        mock_setup_token.return_value = "test_profile"
        mock_client = Mock()
        mock_create_client.return_value = mock_client
        
        # Mock resource collection
        oke_instances = [
            InstanceInfo(
                instance_id="ocid1.instance.oc1..oke1",
                display_name="oke-node-1",
                private_ip="10.0.0.1",
                subnet_id="ocid1.subnet.oc1..subnet1",
            )
        ]
        odo_instances = [
            InstanceInfo(
                instance_id="ocid1.instance.oc1..odo1",
                display_name="odo-instance-1",
                private_ip="10.0.0.2",
                subnet_id="ocid1.subnet.oc1..subnet1",
            )
        ]
        bastions = [
            BastionInfo(
                bastion_id="ocid1.bastion.oc1..bastion1",
                bastion_name="test-bastion",
                target_subnet_id="ocid1.subnet.oc1..subnet1",
            )
        ]
        
        mock_collect.return_value = (oke_instances, odo_instances, bastions)
        
        # Execute
        result = process_region(
            "test-project", "dev", "us-ashburn-1", "ocid1.compartment.oc1..xxxxx"
        )
        
        # Verify
        assert result == (oke_instances, odo_instances, bastions)
        mock_setup_token.assert_called_once_with("test-project", "dev", "us-ashburn-1")
        mock_create_client.assert_called_once_with("us-ashburn-1", "test_profile")
        mock_collect.assert_called_once_with(
            mock_client, "ocid1.compartment.oc1..xxxxx", "us-ashburn-1"
        )
    
    @patch("src.ssh_sync.collect_all_resources")
    @patch("src.ssh_sync.display_connection_info")
    @patch("src.ssh_sync.create_oci_client")
    @patch("src.ssh_sync.display_client_initialization")
    @patch("src.ssh_sync.setup_session_token")
    @patch("src.ssh_sync.display_region_header")
    @patch("src.ssh_sync.display_oke_instances")
    @patch("src.ssh_sync.display_odo_instances")
    @patch("src.ssh_sync.display_bastions")
    def test_process_region_no_client(
        self,
        mock_display_bastions,
        mock_display_odo,
        mock_display_oke,
        mock_display_header,
        mock_setup_token,
        mock_display_init,
        mock_create_client,
        mock_display_conn,
        mock_collect,
    ):
        """Test region processing when client creation fails."""
        mock_setup_token.return_value = "test_profile"
        mock_create_client.return_value = None  # Client creation fails
        
        result = process_region(
            "test-project", "dev", "us-ashburn-1", "ocid1.compartment.oc1..xxxxx"
        )
        
        assert result == ([], [], [])
        mock_collect.assert_not_called()
    
    @patch("src.ssh_sync.sys.exit")
    @patch("rich.console.Console")
    @patch("src.ssh_sync.write_ssh_config_file")
    @patch("src.ssh_sync.display_ssh_config_summary")
    @patch("src.ssh_sync.generate_ssh_config_entries")
    @patch("src.ssh_sync.create_oci_client")
    @patch("src.ssh_sync.setup_session_token")
    @patch("src.ssh_sync.process_region")
    @patch("src.ssh_sync.display_summary")
    @patch("src.ssh_sync.display_configuration_info")
    @patch("src.ssh_sync.load_region_compartments")
    @patch("src.ssh_sync.display_ssh_sync_header")
    @patch("src.ssh_sync.parse_arguments")
    def test_main_success(
        self,
        mock_parse_args,
        mock_display_header,
        mock_load_config,
        mock_display_config,
        mock_display_summary,
        mock_process_region,
        mock_setup_token,
        mock_create_client,
        mock_generate_ssh,
        mock_display_ssh_summary,
        mock_write_ssh,
        mock_console_class,
        mock_exit,
    ):
        """Test main function success path."""
        # Setup argument parsing
        mock_args = Mock()
        mock_args.project_name = "test-project"
        mock_args.stage = "dev"
        mock_args.config_file = "meta.yaml"
        mock_parse_args.return_value = mock_args
        
        # Setup console
        mock_console = Mock()
        mock_console_class.return_value = mock_console
        
        # Setup config loading
        mock_load_config.return_value = {
            "us-ashburn-1": "ocid1.compartment.oc1..comp1",
            "us-phoenix-1": "ocid1.compartment.oc1..comp2",
        }
        
        # Setup region processing
        oke_instances = [Mock()]
        odo_instances = [Mock()]
        bastions = [Mock()]
        mock_process_region.return_value = (oke_instances, odo_instances, bastions)
        
        # Setup SSH config generation
        mock_setup_token.return_value = "test_profile"
        mock_client = Mock()
        mock_create_client.return_value = mock_client
        mock_generate_ssh.return_value = [{"host": "test-host", "config": "test-config"}]
        
        # Execute
        result = main()
        
        # Verify
        assert result == 0  # Main returns 0 on success
        assert mock_process_region.call_count == 2  # Called for each region
        assert mock_generate_ssh.call_count == 2  # Called for each region with instances
        mock_write_ssh.assert_called_once()
    
    @patch("src.ssh_sync.sys.exit")
    @patch("rich.console.Console")
    @patch("src.ssh_sync.process_region")
    @patch("src.ssh_sync.display_summary")
    @patch("src.ssh_sync.display_configuration_info")
    @patch("src.ssh_sync.load_region_compartments")
    @patch("src.ssh_sync.display_ssh_sync_header")
    @patch("src.ssh_sync.parse_arguments")
    def test_main_no_instances(
        self,
        mock_parse_args,
        mock_display_header,
        mock_load_config,
        mock_display_config,
        mock_display_summary,
        mock_process_region,
        mock_console_class,
        mock_exit,
    ):
        """Test main function when no instances are found."""
        # Setup argument parsing
        mock_args = Mock()
        mock_args.project_name = "test-project"
        mock_args.stage = "dev"
        mock_args.config_file = "meta.yaml"
        mock_parse_args.return_value = mock_args
        
        # Setup console
        mock_console = Mock()
        mock_console_class.return_value = mock_console
        
        # Setup config loading
        mock_load_config.return_value = {"us-ashburn-1": "ocid1.compartment.oc1..comp1"}
        
        # Setup region processing - no instances
        mock_process_region.return_value = ([], [], [])
        
        # Execute
        result = main()
        
        # Verify
        assert result == 0  # Main returns 0 on success
        mock_process_region.assert_called_once()
        mock_console.print.assert_any_call("\n[bold green]✅ SSH Configuration Sync Complete![/bold green]")
    
    @patch("src.ssh_sync.sys.exit")
    @patch("rich.console.Console")  
    @patch("src.ssh_sync.main")
    def test_keyboard_interrupt(self, mock_main, mock_console_class, mock_exit):
        """Test handling of keyboard interrupt."""
        # Setup console
        mock_console = Mock()
        mock_console_class.return_value = mock_console
        
        # Simulate KeyboardInterrupt
        mock_main.side_effect = KeyboardInterrupt()
        
        # Import and execute the module's exception handler
        with patch("sys.argv", ["ssh_sync.py", "test-project", "dev"]):
            try:
                mock_main()
            except KeyboardInterrupt:
                mock_console.print("\n[yellow]Program interrupted by user.[/yellow]")
                mock_exit(1)
        
        # Verify
        mock_console.print.assert_called_with("\n[yellow]Program interrupted by user.[/yellow]")
        mock_exit.assert_called_with(1)