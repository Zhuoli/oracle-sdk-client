# Makefile for OCI SSH Sync
# SSH Configuration Generator for Oracle Deployment Orchestrator
# Provides convenient commands for development and SSH config generation

.PHONY: help install test ssh-sync clean format lint type-check dev-setup ssh-sync-remote-observer-dev ssh-sync-remote-observer-staging ssh-sync-remote-observer-prod ssh-sync-today-all-dev ssh-sync-today-all-staging ssh-sync-today-all-prod ssh-help test-coverage check setup-example quickstart image-updates recycle-node-pools

# Default target
help:
	@echo "🔧 OCI SSH Sync - Available Commands"
	@echo ""
	@echo "Setup Commands:"
	@echo "  install       Install dependencies using Poetry"
	@echo "  dev-setup     Complete development setup (install + pre-commit hooks)"
	@echo ""
	@echo "SSH Sync Commands:"
	@echo "  ssh-sync      Generate SSH config for OCI instances"
	@echo "  ssh-help      Show SSH sync configuration help"
	@echo "  image-updates Check for newer images for compute instances (by project/stage)"
	@echo "  recycle-node-pools CSV=<file> [DRY_RUN=1] [CONFIG=~/.oci/config] [POLL_SECONDS=$(POLL_SECONDS)]"
	@echo ""
	@echo "Development Commands:"
	@echo "  test          Run all tests"
	@echo "  test-verbose  Run tests with verbose output"
	@echo "  format        Format code with black and isort"
	@echo "  lint          Run linting with flake8"
	@echo "  type-check    Run type checking with mypy"
	@echo "  clean         Clean up temporary files and caches"
	@echo ""
	@echo "SSH Sync Configuration:"
	@echo "  Uses meta.yaml configuration file for project/stage/region mapping"
	@echo ""
	@echo "Example:"
	@echo "  make ssh-sync PROJECT=remote-observer STAGE=dev"
	@echo ""
	@echo "For detailed configuration help:"
	@echo "  make ssh-help"

# Installation and setup
install:
	@echo "📦 Installing dependencies..."
	cd tools && poetry install

dev-setup: install
	@echo "🛠️  Setting up development environment..."
	cd tools && poetry run pre-commit install
	@echo "✅ Development environment ready!"

# SSH Sync commands
ssh-sync:
	@echo "🔧 Running OCI SSH Sync (Generate SSH config)..."
	@echo "Usage: make ssh-sync PROJECT=<project_name> STAGE=<stage>"
	@echo "Example: make ssh-sync PROJECT=remote-observer STAGE=dev"
	@echo ""
	@if [ -z "$(PROJECT)" ] || [ -z "$(STAGE)" ]; then \
		echo "❌ Error: PROJECT and STAGE parameters are required"; \
		echo ""; \
		echo "Available projects and stages from meta.yaml:"; \
		echo "  remote-observer: dev, staging, prod"; \
		echo "  today-all: dev, staging, prod"; \
		echo ""; \
		echo "Examples:"; \
		echo "  make ssh-sync PROJECT=remote-observer STAGE=dev"; \
		echo "  make ssh-sync PROJECT=today-all STAGE=staging"; \
		echo "  make ssh-sync PROJECT=remote-observer STAGE=prod"; \
		exit 1; \
	fi
	cd tools && poetry run python src/ssh_sync.py $(PROJECT) $(STAGE)

# Alternative ssh-sync targets for convenience
ssh-sync-remote-observer-dev:
	@echo "🔧 Generating SSH config for remote-observer dev environment..."
	cd tools && poetry run python src/ssh_sync.py remote-observer dev

ssh-sync-remote-observer-staging:
	@echo "🔧 Generating SSH config for remote-observer staging environment..."
	cd tools && poetry run python src/ssh_sync.py remote-observer staging

ssh-sync-remote-observer-prod:
	@echo "🔧 Generating SSH config for remote-observer prod environment..."
	cd tools && poetry run python src/ssh_sync.py remote-observer prod

ssh-sync-today-all-dev:
	@echo "🔧 Generating SSH config for today-all dev environment..."
	cd tools && poetry run python src/ssh_sync.py today-all dev

ssh-sync-today-all-staging:
	@echo "🔧 Generating SSH config for today-all staging environment..."
	cd tools && poetry run python src/ssh_sync.py today-all staging

ssh-sync-today-all-prod:
	@echo "🔧 Generating SSH config for today-all prod environment..."
	cd tools && poetry run python src/ssh_sync.py today-all prod

ssh-help:
	@echo "🔧 SSH Sync Configuration Help"
	@echo ""
	@echo "Configuration:"
	@echo "  SSH Sync uses YAML configuration from meta.yaml file"
	@echo "  • Supports multiple projects: remote-observer, today-all"
	@echo "  • Supports multiple stages: dev, staging, prod"
	@echo "  • Automatically creates session tokens for each region"
	@echo ""
	@echo "What SSH Sync does:"
	@echo "  1. Parses meta.yaml to get region:compartment_id pairs"
	@echo "  2. Creates session tokens for each region using create_session_token()"
	@echo "  3. Lists OKE cluster instances and ODO instances across all regions"
	@echo "  4. Finds appropriate bastions for each instance"
	@echo "  5. Generates SSH config entries with ProxyCommand for bastion access"
	@echo "  6. Writes SSH configuration to ssh_config_<project>_<stage>.txt"
	@echo ""
	@echo "Available Commands:"
	@echo "  make ssh-sync PROJECT=<project> STAGE=<stage>  # Generic command"
	@echo "  make ssh-sync-remote-observer-dev              # Specific shortcuts"
	@echo "  make ssh-sync-remote-observer-staging"
	@echo "  make ssh-sync-remote-observer-prod"
	@echo "  make ssh-sync-today-all-dev"
	@echo "  make ssh-sync-today-all-staging"
	@echo "  make ssh-sync-today-all-prod"
	@echo ""
	@echo "Prerequisites:"
	@echo "  • OCI CLI installed: pip install oci-cli"
	@echo "  • Valid Oracle Cloud tenancy access"
	@echo "  • At least one existing OCI profile (DEFAULT) for session token creation"
	@echo "  • PyYAML package installed (included in dependencies)"
	@echo "  • ossh command available for ProxyCommand (Oracle internal tool)"
	@echo ""
	@echo "Authentication Setup:"
	@echo "  # Create an initial profile for session token creation:"
	@echo "  oci session authenticate --profile-name DEFAULT --region us-phoenix-1"
	@echo ""
	@echo "Examples:"
	@echo "  make ssh-sync PROJECT=remote-observer STAGE=dev"
	@echo "  make ssh-sync-today-all-staging"
	@echo ""
	@echo "Output:"
	@echo "  SSH config file: ssh_config_<project>_<stage>.txt"

# New command: check for newer images per instance
image-updates:
	@echo "🔎 Checking for newer images for compute instances..."
	@echo "Usage: make image-updates PROJECT=<project_name> STAGE=<stage>"
	@echo "Example: make image-updates PROJECT=remote-observer STAGE=dev"
	@echo ""
	@if [ -z "$(PROJECT)" ] || [ -z "$(STAGE)" ]; then \
		echo "❌ Error: PROJECT and STAGE parameters are required"; \
		echo ""; \
		echo "Examples:"; \
		echo "  make image-updates PROJECT=remote-observer STAGE=dev"; \
		echo "  make image-updates PROJECT=today-all STAGE=staging"; \
		exit 1; \
	fi
	cd tools && poetry run python src/check_image_updates.py $(PROJECT) $(STAGE)


# OKE node pool recycling
recycle-node-pools:
	@if [ -z "$(CSV)" ]; then \
		echo "❌ Error: CSV=<file> is required"; \
			echo "Usage: make recycle-node-pools CSV=oke_nodes.csv [DRY_RUN=1] [META=tools/meta.yaml] [CONFIG=~/.oci/config]"; \
		exit 1; \
	fi
	@echo "♻️  Recycling OKE node pools from $(CSV)"
	@DRY_RUN_FLAG=""; \
	if [ "$(DRY_RUN)" = "1" ] || [ "$(DRY_RUN)" = "true" ] || [ "$(DRY_RUN)" = "TRUE" ] || [ "$(DRY_RUN)" = "yes" ] || [ "$(DRY_RUN)" = "YES" ]; then \
		DRY_RUN_FLAG="--dry-run"; \
	fi; \
	CONFIG_FLAG=""; \
	if [ -n "$(CONFIG)" ]; then \
		CONFIG_FLAG="--config-file ../$(CONFIG)"; \
	fi; \
	POLL_FLAG=""; \
	if [ -n "$(POLL_SECONDS)" ]; then \
		POLL_FLAG="--poll-seconds $(POLL_SECONDS)"; \
	fi; \
	META_FLAG=""; \
	if [ -n "$(META)" ]; then \
		META_FLAG="--meta-file ../$(META)"; \
	fi; \
	cd tools && poetry run python src/recycle_node_pools.py --csv-path "../$(CSV)" $$POLL_FLAG $$CONFIG_FLAG $$META_FLAG $$DRY_RUN_FLAG

# Testing
test:
	@echo "🧪 Running tests..."
	cd tools && poetry run pytest

test-verbose:
	@echo "🧪 Running tests with verbose output..."
	cd tools && poetry run pytest -v

test-coverage:
	@echo "🧪 Running tests with coverage..."
	cd tools && poetry run pytest --cov=src/oci_client --cov-report=term-missing

# Code quality
format:
	@echo "🎨 Formatting code..."
	cd tools && poetry run black src/ tests/
	cd tools && poetry run isort src/ tests/

lint:
	@echo "🔍 Running linting..."
	cd tools && poetry run flake8 src/ tests/

type-check:
	@echo "🔬 Running type checking..."
	cd tools && poetry run mypy src/

# Development workflow
check: format lint type-check test
	@echo "✅ All checks passed!"

# Cleanup
clean:
	@echo "🧹 Cleaning up..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name "*.pyo" -delete 2>/dev/null || true
	find . -name "*~" -delete 2>/dev/null || true
	find . -name ".coverage" -delete 2>/dev/null || true
	@echo "✅ Cleanup completed!"

# Example environment setup (for documentation)
setup-example:
	@echo "📋 Example environment setup:"
	@echo ""
	@echo "# Copy and paste these commands, replacing with your actual values:"
	@echo "export OCI_COMPARTMENT_ID=ocid1.compartment.oc1..aaaaaaaaxxxxxxxyyyyyyy"
	@echo "export OCI_REGION=us-phoenix-1"
	@echo "export OCI_PROFILE=DEFAULT"
	@echo ""
	@echo "# Then run the SSH sync:"
	@echo "make ssh-sync PROJECT=remote-observer STAGE=dev"

# Quick start for new users
quickstart:
	@echo "🚀 Quick Start Guide"
	@echo ""
	@echo "1. Install dependencies:"
	@echo "   make install"
	@echo ""
	@echo "2. Set up your OCI authentication:"
	@echo "   oci session authenticate --profile-name DEFAULT --region us-phoenix-1"
	@echo ""
	@echo "3. Set your compartment ID:"
	@echo "   export OCI_COMPARTMENT_ID=your-compartment-ocid-here"
	@echo ""
	@echo "4. Run SSH sync:"
	@echo "   make ssh-sync PROJECT=remote-observer STAGE=dev"
	@echo ""
	@echo "For more help: make ssh-help"
