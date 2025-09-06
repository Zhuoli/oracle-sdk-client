# OCI Python Client

A modern Python client for Oracle Cloud Infrastructure (OCI) with optimized session token authentication support.

## Features

- ✅ **Session Token Authentication** - Full support for OCI session tokens (recommended)
- ✅ **API Key Authentication** - Traditional API key authentication
- ✅ **Automatic Token Refresh** - Refresh expired session tokens automatically
- ✅ **Lazy Loading** - Service clients are initialized only when needed
- ✅ **Retry Logic** - Built-in retry mechanisms for transient failures
- ✅ **Rich Console Output** - Beautiful terminal output with progress indicators
- ✅ **Type Hints** - Full type hints for better IDE support
- ✅ **Pydantic Models** - Data validation and serialization

## Installation

### Using Poetry (Recommended)

```bash
# Clone the repository
git clone <your-repo-url>
cd oci-python-client

# Install dependencies
poetry install

# Activate virtual environment
poetry shell
```

### Using pip

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install package
pip install -e .
```

## Configuration

### 1. Setup OCI CLI Configuration

First, ensure you have the OCI CLI installed:

```bash
pip install oci-cli
```

### 2. Create Session Token Authentication (Recommended)

Session tokens are more secure and easier to manage than API keys:

```bash
# Create a new profile with session token
oci session authenticate \
  --profile-name ssh_builder_odo \
  --region us-ashburn-1

# This will open a browser for authentication
# The token and keys will be saved in ~/.oci/sessions/
```

Your `~/.oci/config` file will look like this:

```ini
[ssh_builder_odo]
fingerprint = b5:57:e2:3c:fc:b5:ca:7b:fb:76:10:3c:92:4f:0e:80
key_file = /Users/yourname/.oci/sessions/ssh_builder_odo/oci_api_key.pem
tenancy = ocid1.tenancy.oc1..aaaaaaaagkbzgg6lpzrf47xzy4rjoxg4de6ncfiq2rncmjiujvywfdsfsdf
region = us-ashburn-1
security_token_file = /Users/yourname/.oci/sessions/ssh_builder_odo/token
```

### 3. Refresh Session Token

Session tokens expire after 1 hour. To refresh:

```bash
oci session refresh --profile ssh_builder_odo
```

Or programmatically:

```python
client.refresh_auth()
```

## Usage

### Basic Usage

```python
from oci_client.client import OCIClient
from oci_client.models import LifecycleState

# Initialize client with session token auth
client = OCIClient(
    region="us-ashburn-1",
    profile_name="ssh_builder_odo"
)

# Test connection
if client.test_connection():
    print("Connected to OCI!")

# List running instances
instances = client.list_instances(
    compartment_id="ocid1.compartment.oc1..xxxxx",
    lifecycle_state=LifecycleState.RUNNING
)

for instance in instances:
    print(f"{instance.display_name}: {instance.private_ip}")
```

### Context Manager

```python
with OCIClient(region="us-phoenix-1", profile_name="my_profile") as client:
    instances = client.list_instances(compartment_id="...")
    # Client cleanup happens automatically
```

### List OKE Cluster Instances

```python
# List all OKE instances
oke_instances = client.list_oke_instances(
    compartment_id="ocid1.compartment.oc1..xxxxx"
)

# Filter by specific cluster
oke_instances = client.list_oke_instances(
    compartment_id="ocid1.compartment.oc1..xxxxx",
    cluster_name="my-k8s-cluster"
)
```

### List ODO Instances

```python
odo_instances = client.list_odo_instances(
    compartment_id="ocid1.compartment.oc1..xxxxx"
)

for instance in odo_instances:
    print(f"ODO Instance: {instance.display_name}")
```

### Work with Bastions

```python
from oci_client.models import BastionType

# List bastions
bastions = client.list_bastions(
    compartment_id="ocid1.compartment.oc1..xxxxx",
    bastion_type=BastionType.INTERNAL
)

# Find bastion for a specific subnet
if instances:
    bastion = client.find_bastion_for_subnet(
        bastions,
        instances[0].subnet_id
    )
    
    if bastion:
        print(f"Found bastion: {bastion.bastion_name}")
```

### Create Bastion Session

```python
# Create a session to connect to an instance
session = client.create_bastion_session(
    bastion_id=bastion.bastion_id,
    target_resource_id=instance.instance_id,
    target_private_ip=instance.private_ip,
    session_ttl=10800  # 3 hours
)

print(f"SSH Command: {session.ssh_metadata.get('command')}")
```

## Advanced Features

### Custom Retry Strategy

```python
from oci.retry import RetryStrategyBuilder

# Create custom retry strategy
retry_strategy = RetryStrategyBuilder(
    max_attempts=5,
    service_error_retry_config={
        429: {'base_sleep_time': 2, 'exponential_growth_factor': 2}
    }
).get_retry_strategy()

client = OCIClient(
    region="us-phoenix-1",
    profile_name="my_profile",
    retry_strategy=retry_strategy
)
```

### Service-Specific Operations

```python
# Access underlying OCI SDK clients directly
compute_client = client.compute_client
identity_client = client.identity_client
bastion_client = client.bastion_client
network_client = client.network_client

# Use native OCI SDK operations
regions = identity_client.list_regions()
```

## Development

### Run Tests

```bash
# Run all tests
poetry run pytest

# Run with coverage
poetry run pytest --cov=src/oci_client

# Run specific test file
poetry run pytest tests/test_client.py
```

### Code Formatting

```bash
# Format code with black
poetry run black src/ tests/

# Sort imports
poetry run isort src/ tests/

# Type checking
poetry run mypy src/
```

### Pre-commit Hooks

```bash
# Install pre-commit hooks
poetry run pre-commit install

# Run manually
poetry run pre-commit run --all-files
```

## Project Structure

```
oci-python-client/
├── src/
│   └── oci_client/
│       ├── __init__.py       # Package initialization
│       ├── auth.py           # Authentication handling
│       ├── client.py         # Main client class
│       ├── models.py         # Data models
│       ├── services/         # Service-specific modules
│       └── utils/            # Utility functions
├── tests/                    # Test files
├── examples/                 # Usage examples
├── pyproject.toml           # Poetry configuration
└── README.md                # This file
```

## Common Issues

### Session Token Expired

**Error**: `401 Unauthorized`

**Solution**: Refresh your session token:
```bash
oci session refresh --profile ssh_builder_odo
```

### Profile Not Found

**Error**: `Config profile 'ssh_builder_odo' not found`

**Solution**: Create the profile:
```bash
oci session authenticate --profile-name ssh_builder_odo --region us-ashburn-1
```

### Missing Dependencies

**Error**: `ModuleNotFoundError: No module named 'oci'`

**Solution**: Install dependencies:
```bash
poetry install
# or
pip install oci requests pydantic rich tenacity
```

## Security Best Practices

1. **Use Session Tokens**: Prefer session tokens over API keys
2. **Rotate Tokens**: Refresh tokens regularly (they expire after 1 hour)
3. **Secure Storage**: Keep your `~/.oci` directory with restrictive permissions (`chmod 700 ~/.oci`)
4. **Environment Variables**: Use environment variables for sensitive data in production
5. **Audit Logs**: Monitor OCI audit logs for unusual activity

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For issues, questions, or contributions, please open an issue on GitHub.