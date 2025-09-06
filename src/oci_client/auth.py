"""Authentication module for OCI client."""

import os
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
import logging

import oci
from oci.auth.signers import SecurityTokenSigner
from oci.signer import Signer
from rich.console import Console

from .models import AuthType, OCIConfig

logger = logging.getLogger(__name__)
console = Console()


class OCIAuthenticator:
    """Handle OCI authentication with multiple auth methods."""
    
    def __init__(self, config: OCIConfig):
        """Initialize authenticator with configuration."""
        self.config = config
        self.oci_config: Optional[Dict[str, Any]] = None
        self.signer: Optional[Any] = None
        
    def authenticate(self) -> Tuple[Dict[str, Any], Any]:
        """
        Authenticate with OCI and return config and signer.
        
        Returns:
            Tuple of (config_dict, signer_object)
            
        Raises:
            RuntimeError: If authentication fails
        """
        try:
            # Load OCI config from file
            self.oci_config = self._load_config()
            
            # Determine auth type and create signer
            auth_type = self._determine_auth_type()
            self.signer = self._create_signer(auth_type)
            
            # Validate authentication
            if self._validate_auth():
                console.print(
                    f"[green]✓[/green] Successfully authenticated using {auth_type.value} "
                    f"for profile '{self.config.profile_name}'"
                )
                return self.oci_config, self.signer
            else:
                raise RuntimeError("Authentication validation failed")
                
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            self._print_auth_help()
            raise RuntimeError(f"Failed to authenticate with OCI: {e}")
    
    def _load_config(self) -> Dict[str, Any]:
        """Load OCI configuration from file."""
        try:
            config_file = Path.home() / ".oci" / "config"
            if not config_file.exists():
                raise FileNotFoundError(f"OCI config file not found: {config_file}")
            
            # Load config for specified profile
            oci_config = oci.config.from_file(
                file_location=str(config_file),
                profile_name=self.config.profile_name
            )
            
            # Override region if specified
            if self.config.region:
                oci_config["region"] = self.config.region
            
            # Update config model with loaded values
            self.config.tenancy = oci_config.get("tenancy")
            self.config.user = oci_config.get("user")
            self.config.fingerprint = oci_config.get("fingerprint")
            self.config.key_file = oci_config.get("key_file")
            self.config.security_token_file = oci_config.get("security_token_file")
            self.config.pass_phrase = oci_config.get("pass_phrase")
            
            return oci_config
            
        except Exception as e:
            logger.error(f"Failed to load OCI config: {e}")
            raise
    
    def _determine_auth_type(self) -> AuthType:
        """Determine the authentication type from config."""
        if self.config.security_token_file:
            # Check if token file exists and is valid
            token_file = Path(self.config.security_token_file)
            if not token_file.exists():
                raise FileNotFoundError(
                    f"Security token file not found: {token_file}\n"
                    f"Please run: oci session authenticate --profile-name {self.config.profile_name}"
                )
            
            # Check token file age (tokens expire after 1 hour)
            token_age_hours = (
                (Path.ctime(Path.cwd()) - token_file.stat().st_mtime) / 3600
            )
            if token_age_hours > 1:
                console.print(
                    f"[yellow]⚠[/yellow] Security token may be expired "
                    f"(created {token_age_hours:.1f} hours ago)"
                )
            
            return AuthType.SESSION_TOKEN
            
        elif self.config.key_file and self.config.fingerprint:
            # Check if key file exists
            key_file = Path(self.config.key_file)
            if not key_file.exists():
                raise FileNotFoundError(f"Private key file not found: {key_file}")
            return AuthType.API_KEY
            
        else:
            raise ValueError(
                f"Unable to determine auth type for profile '{self.config.profile_name}'. "
                f"Config must have either security_token_file or (key_file + fingerprint)."
            )
    
    def _create_signer(self, auth_type: AuthType) -> Any:
        """Create appropriate signer based on auth type."""
        try:
            if auth_type == AuthType.SESSION_TOKEN:
                return self._create_session_token_signer()
            elif auth_type == AuthType.API_KEY:
                return self._create_api_key_signer()
            else:
                raise ValueError(f"Unsupported auth type: {auth_type}")
                
        except Exception as e:
            logger.error(f"Failed to create signer: {e}")
            raise
    
    def _create_session_token_signer(self) -> SecurityTokenSigner:
        """Create a session token signer."""
        # Read the session token
        with open(self.config.security_token_file, 'r') as f:
            token = f.read().strip()
        
        # Load the private key
        private_key = oci.signer.load_private_key_from_file(
            self.config.key_file,
            pass_phrase=self.config.pass_phrase
        )
        
        # Create and return the signer
        return SecurityTokenSigner(token, private_key)
    
    def _create_api_key_signer(self) -> Signer:
        """Create an API key signer."""
        return oci.signer.Signer(
            tenancy=self.config.tenancy,
            user=self.config.user,
            fingerprint=self.config.fingerprint,
            private_key_file_location=self.config.key_file,
            pass_phrase=self.config.pass_phrase
        )
    
    def _validate_auth(self) -> bool:
        """Validate authentication by making a test API call."""
        try:
            identity_client = oci.identity.IdentityClient(
                self.oci_config, 
                signer=self.signer
            )
            
            # Try to list regions as a simple test
            regions = identity_client.list_regions()
            logger.info(f"Authentication validated. Found {len(regions.data)} regions.")
            return True
            
        except oci.exceptions.ServiceError as e:
            if e.status == 401:
                logger.error("Authentication failed: Invalid credentials or expired token")
            else:
                logger.error(f"Service error during validation: {e}")
            return False
            
        except Exception as e:
            logger.error(f"Validation failed with unexpected error: {e}")
            return False
    
    def _print_auth_help(self) -> None:
        """Print helpful authentication instructions."""
        console.print("\n[red]Authentication Setup Instructions:[/red]")
        console.print(
            f"\n1. For session token authentication (recommended):\n"
            f"   [cyan]oci session authenticate --profile-name {self.config.profile_name} "
            f"--region {self.config.region}[/cyan]\n"
        )
        console.print(
            f"2. For API key authentication:\n"
            f"   - Generate API key pair\n"
            f"   - Upload public key to OCI Console\n"
            f"   - Update ~/.oci/config with:\n"
            f"     [cyan][{self.config.profile_name}]\n"
            f"     user=<your-user-ocid>\n"
            f"     fingerprint=<your-key-fingerprint>\n"
            f"     tenancy=<your-tenancy-ocid>\n"
            f"     region={self.config.region}\n"
            f"     key_file=<path-to-private-key>[/cyan]\n"
        )
    
    def refresh_token(self) -> bool:
        """
        Refresh session token if expired.
        
        Returns:
            True if token was refreshed successfully
        """
        if self.config.auth_type != AuthType.SESSION_TOKEN:
            return True  # No refresh needed for non-token auth
        
        try:
            console.print("[yellow]Refreshing session token...[/yellow]")
            
            # Run OCI CLI to refresh token
            import subprocess
            result = subprocess.run(
                [
                    "oci", "session", "refresh",
                    "--profile", self.config.profile_name
                ],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                # Re-authenticate with new token
                self.authenticate()
                console.print("[green]✓[/green] Token refreshed successfully")
                return True
            else:
                console.print(f"[red]✗[/red] Token refresh failed: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to refresh token: {e}")
            return False
