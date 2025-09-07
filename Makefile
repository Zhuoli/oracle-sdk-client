# Makefile for OCI Python Client
# Provides convenient commands for development and demonstration

.PHONY: help install test demo clean format lint type-check dev-setup

# Default target
help:
	@echo "🌟 OCI Python Client - Available Commands"
	@echo ""
	@echo "Setup Commands:"
	@echo "  install       Install dependencies using Poetry"
	@echo "  dev-setup     Complete development setup (install + pre-commit hooks)"
	@echo ""
	@echo "Demo Commands:"
	@echo "  demo          Run the main demo (OKE & ODO instances listing)"
	@echo "  demo-help     Show demo configuration help"
	@echo ""
	@echo "Development Commands:"
	@echo "  test          Run all tests"
	@echo "  test-verbose  Run tests with verbose output"
	@echo "  format        Format code with black and isort"
	@echo "  lint          Run linting with flake8"
	@echo "  type-check    Run type checking with mypy"
	@echo "  clean         Clean up temporary files and caches"
	@echo ""
	@echo "Environment Variables for demo:"
	@echo "  OCI_REGION           OCI region (default: us-phoenix-1)"
	@echo "  OCI_PROFILE          OCI profile name (default: DEFAULT)"
	@echo "  OCI_COMPARTMENT_ID   OCI compartment ID (required)"
	@echo "  OCI_CONFIG_FILE      Custom OCI config file path (optional)"
	@echo ""
	@echo "Example:"
	@echo "  export OCI_COMPARTMENT_ID=ocid1.compartment.oc1..your-id"
	@echo "  make demo"

# Installation and setup
install:
	@echo "📦 Installing dependencies..."
	poetry install

dev-setup: install
	@echo "🛠️  Setting up development environment..."
	poetry run pre-commit install
	@echo "✅ Development environment ready!"

# Demo commands
demo:
	@echo "🚀 Running OCI Client Demo (OKE & ODO instances)..."
	@echo "Make sure you have set the required environment variables:"
	@echo "  export OCI_COMPARTMENT_ID=ocid1.compartment.oc1..your-compartment-id"
	@echo ""
	poetry run python src/main.py

demo-help:
	@echo "🔧 Demo Configuration Help"
	@echo ""
	@echo "Required Environment Variables:"
	@echo "  OCI_COMPARTMENT_ID   - Your OCI compartment OCID"
	@echo ""
	@echo "Optional Environment Variables:"
	@echo "  OCI_REGION          - OCI region (default: us-phoenix-1)"
	@echo "  OCI_PROFILE         - OCI profile name (default: DEFAULT)"
	@echo "  OCI_CONFIG_FILE     - Custom OCI config file path"
	@echo ""
	@echo "Setup Example:"
	@echo "  # Set required compartment ID"
	@echo "  export OCI_COMPARTMENT_ID=ocid1.compartment.oc1..aaaaaaaaxxxxxxxyyyyyyy"
	@echo ""
	@echo "  # Optional: Set custom region and profile"
	@echo "  export OCI_REGION=us-ashburn-1"
	@echo "  export OCI_PROFILE=my_profile"
	@echo ""
	@echo "  # Optional: Use custom config file"
	@echo "  export OCI_CONFIG_FILE=/path/to/custom/oci/config"
	@echo ""
	@echo "Then run:"
	@echo "  make demo"
	@echo ""
	@echo "Authentication Setup:"
	@echo "  # For session token (recommended):"
	@echo "  oci session authenticate --profile-name DEFAULT --region us-phoenix-1"
	@echo ""
	@echo "  # For API key: Set up ~/.oci/config with your API key details"

# Testing
test:
	@echo "🧪 Running tests..."
	poetry run pytest

test-verbose:
	@echo "🧪 Running tests with verbose output..."
	poetry run pytest -v

test-coverage:
	@echo "🧪 Running tests with coverage..."
	poetry run pytest --cov=src/oci_client --cov-report=term-missing

# Code quality
format:
	@echo "🎨 Formatting code..."
	poetry run black src/ tests/
	poetry run isort src/ tests/

lint:
	@echo "🔍 Running linting..."
	poetry run flake8 src/ tests/

type-check:
	@echo "🔬 Running type checking..."
	poetry run mypy src/

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
	@echo "# Then run the demo:"
	@echo "make demo"

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
	@echo "4. Run the demo:"
	@echo "   make demo"
	@echo ""
	@echo "For more help: make demo-help"