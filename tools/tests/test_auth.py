"""Tests for authentication module."""

from unittest.mock import MagicMock, Mock, mock_open, patch

import pytest

from src.oci_client.auth import OCIAuthenticator
from src.oci_client.models import AuthType, OCIConfig


class TestOCIAuthenticator:
    """Test OCI Authenticator."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        return OCIConfig(region="us-ashburn-1", profile_name="test_profile")

    @pytest.fixture
    def mock_oci_config_dict(self):
        """Mock OCI config dictionary."""
        return {
            "user": "ocid1.user.oc1..xxxxx",
            "fingerprint": "aa:bb:cc:dd:ee:ff",
            "tenancy": "ocid1.tenancy.oc1..xxxxx",
            "region": "us-ashburn-1",
            "key_file": "/home/user/.oci/sessions/test/oci_api_key.pem",
            "security_token_file": "/home/user/.oci/sessions/test/token",
        }

    @patch("src.oci_client.auth.Path")
    @patch("src.oci_client.auth.oci.config.from_file")
    def test_load_config_session_token(
        self, mock_from_file, mock_path, mock_config, mock_oci_config_dict
    ):
        """Test loading config with session token."""
        # Setup mock Path behavior
        mock_home = MagicMock()
        mock_oci_path = MagicMock()
        mock_config_file = MagicMock()

        mock_path.home.return_value = mock_home
        mock_home.__truediv__.return_value = mock_oci_path
        mock_oci_path.__truediv__.return_value = mock_config_file
        mock_config_file.exists.return_value = True

        mock_from_file.return_value = mock_oci_config_dict

        auth = OCIAuthenticator(mock_config)
        config = auth._load_config()

        assert config["security_token_file"] == "/home/user/.oci/sessions/test/token"
        assert auth.config.security_token_file == "/home/user/.oci/sessions/test/token"
        assert auth.config.key_file == "/home/user/.oci/sessions/test/oci_api_key.pem"

    @patch("src.oci_client.auth.Path")
    @patch("src.oci_client.auth.oci.config.from_file")
    def test_load_config_api_key(self, mock_from_file, mock_path, mock_config):
        """Test loading config with API key."""
        # Setup config without session token
        api_key_config = {
            "user": "ocid1.user.oc1..xxxxx",
            "fingerprint": "aa:bb:cc:dd:ee:ff",
            "tenancy": "ocid1.tenancy.oc1..xxxxx",
            "region": "us-ashburn-1",
            "key_file": "/home/user/.oci/api_key.pem",
        }

        # Setup mock Path behavior
        mock_home = MagicMock()
        mock_oci_path = MagicMock()
        mock_config_file = MagicMock()

        mock_path.home.return_value = mock_home
        mock_home.__truediv__.return_value = mock_oci_path
        mock_oci_path.__truediv__.return_value = mock_config_file
        mock_config_file.exists.return_value = True
        mock_from_file.return_value = api_key_config

        auth = OCIAuthenticator(mock_config)
        config = auth._load_config()

        assert config.get("security_token_file") is None
        assert config["key_file"] == "/home/user/.oci/api_key.pem"
        assert auth.config.key_file == "/home/user/.oci/api_key.pem"

    def test_determine_auth_type_session_token(self, mock_config):
        """Test determining session token auth type."""
        mock_config.security_token_file = "/path/to/token"
        mock_config.key_file = "/path/to/key.pem"

        with (
            patch("src.oci_client.auth.Path") as mock_path,
            patch("src.oci_client.auth.time.time", return_value=1234567900),
        ):
            mock_path_instance = MagicMock()
            mock_path.return_value = mock_path_instance
            mock_path_instance.stat.return_value.st_mtime = 1234567890
            mock_path_instance.exists.return_value = True

            auth = OCIAuthenticator(mock_config)
            auth_type = auth._determine_auth_type()

            assert auth_type == AuthType.SESSION_TOKEN

    def test_determine_auth_type_api_key(self, mock_config):
        """Test determining API key auth type."""
        mock_config.key_file = "/path/to/key.pem"
        mock_config.fingerprint = "aa:bb:cc:dd:ee:ff"
        mock_config.security_token_file = None

        with patch("src.oci_client.auth.Path") as mock_path:
            mock_path.return_value.exists.return_value = True

            auth = OCIAuthenticator(mock_config)
            auth_type = auth._determine_auth_type()

            assert auth_type == AuthType.API_KEY

    def test_determine_auth_type_missing_token_file(self, mock_config):
        """Test error when token file is missing."""
        mock_config.security_token_file = "/path/to/missing/token"

        with patch("src.oci_client.auth.Path") as mock_path:
            mock_path.return_value.exists.return_value = False

            auth = OCIAuthenticator(mock_config)

            with pytest.raises(FileNotFoundError) as exc_info:
                auth._determine_auth_type()

            assert "Security token file not found" in str(exc_info.value)

    @patch("src.oci_client.auth.oci.signer.load_private_key_from_file")
    @patch("src.oci_client.auth.SecurityTokenSigner")
    @patch("builtins.open", new_callable=mock_open, read_data="test_token_content")
    def test_create_session_token_signer(self, mock_file, mock_signer, mock_load_key, mock_config):
        """Test creating session token signer."""
        mock_config.security_token_file = "/path/to/token"
        mock_config.key_file = "/path/to/key.pem"
        mock_config.pass_phrase = None

        mock_private_key = Mock()
        mock_load_key.return_value = mock_private_key

        auth = OCIAuthenticator(mock_config)
        auth._create_session_token_signer()

        mock_file.assert_called_once_with("/path/to/token", "r")
        mock_load_key.assert_called_once_with("/path/to/key.pem", pass_phrase=None)
        mock_signer.assert_called_once_with("test_token_content", mock_private_key)

    @patch("src.oci_client.auth.oci.signer.Signer")
    def test_create_api_key_signer(self, mock_signer, mock_config):
        """Test creating API key signer."""
        mock_config.tenancy = "ocid1.tenancy.oc1..xxxxx"
        mock_config.user = "ocid1.user.oc1..xxxxx"
        mock_config.fingerprint = "aa:bb:cc:dd:ee:ff"
        mock_config.key_file = "/path/to/key.pem"
        mock_config.pass_phrase = "test_pass"

        auth = OCIAuthenticator(mock_config)
        auth._create_api_key_signer()

        mock_signer.assert_called_once_with(
            tenancy="ocid1.tenancy.oc1..xxxxx",
            user="ocid1.user.oc1..xxxxx",
            fingerprint="aa:bb:cc:dd:ee:ff",
            private_key_file_location="/path/to/key.pem",
            pass_phrase="test_pass",
        )

    @patch("src.oci_client.auth.oci.identity.IdentityClient")
    def test_validate_auth_success(self, mock_identity_client, mock_config, mock_oci_config_dict):
        """Test successful authentication validation."""
        mock_client_instance = Mock()
        mock_identity_client.return_value = mock_client_instance

        mock_regions = Mock()
        mock_regions.data = [Mock(), Mock()]  # Two regions
        mock_client_instance.list_regions.return_value = mock_regions

        auth = OCIAuthenticator(mock_config)
        auth.oci_config = mock_oci_config_dict
        auth.signer = Mock()

        result = auth._validate_auth()

        assert result is True
        mock_client_instance.list_regions.assert_called_once()

    @patch("src.oci_client.auth.oci.identity.IdentityClient")
    def test_validate_auth_failure_401(
        self, mock_identity_client, mock_config, mock_oci_config_dict
    ):
        """Test authentication validation failure with 401 error."""
        import oci.exceptions

        mock_client_instance = Mock()
        mock_identity_client.return_value = mock_client_instance

        mock_error = oci.exceptions.ServiceError(
            status=401, code="Unauthorized", headers={}, message="Invalid credentials"
        )
        mock_client_instance.list_regions.side_effect = mock_error

        auth = OCIAuthenticator(mock_config)
        auth.oci_config = mock_oci_config_dict
        auth.signer = Mock()

        result = auth._validate_auth()

        assert result is False

    @patch("subprocess.run")
    @patch("src.oci_client.auth.console")
    def test_refresh_token_success(self, mock_console, mock_subprocess, mock_config):
        """Test successful token refresh."""
        mock_config.auth_type = AuthType.SESSION_TOKEN
        mock_config.profile_name = "test_profile"

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_subprocess.return_value = mock_result

        auth = OCIAuthenticator(mock_config)

        with patch.object(auth, "authenticate") as mock_authenticate:
            result = auth.refresh_token()

            assert result is True
            mock_subprocess.assert_called_once_with(
                ["oci", "session", "refresh", "--profile", "test_profile"],
                capture_output=True,
                text=True,
            )
            mock_authenticate.assert_called_once()

    @patch("subprocess.run")
    @patch("src.oci_client.auth.console")
    def test_refresh_token_failure(self, mock_console, mock_subprocess, mock_config):
        """Test failed token refresh."""
        mock_config.auth_type = AuthType.SESSION_TOKEN
        mock_config.profile_name = "test_profile"

        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stderr = "Authentication failed"
        mock_subprocess.return_value = mock_result

        auth = OCIAuthenticator(mock_config)
        result = auth.refresh_token()

        assert result is False
        mock_subprocess.assert_called_once()

    def test_refresh_token_not_needed_for_api_key(self, mock_config):
        """Test that refresh returns True for API key auth."""
        mock_config.auth_type = AuthType.API_KEY

        auth = OCIAuthenticator(mock_config)
        result = auth.refresh_token()

        assert result is True

    @patch("src.oci_client.auth.console")
    def test_print_auth_help(self, mock_console, mock_config):
        """Test printing authentication help."""
        mock_config.profile_name = "test_profile"
        mock_config.region = "us-ashburn-1"

        auth = OCIAuthenticator(mock_config)
        auth._print_auth_help()

        # Verify console.print was called with help text
        assert mock_console.print.called
        calls = mock_console.print.call_args_list

        # Check that help text contains expected content
        help_text = str(calls)
        assert "test_profile" in help_text
        assert "us-ashburn-1" in help_text
        assert "oci session authenticate" in help_text
