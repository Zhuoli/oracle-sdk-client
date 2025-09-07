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
	@echo "Demo Configuration:"
	@echo "  The demo uses hardcoded values - update src/main.py as needed"
	@echo ""
	@echo "Example:"
	@echo "  make demo"
	@echo ""
	@echo "For detailed configuration help:"
	@echo "  make demo-help"

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
	@echo "Using hardcoded configuration values in main.py"
	@echo "Note: Update the compartment_id in src/main.py with your actual compartment OCID"
	@echo ""
	poetry run python src/main.py

demo-help:
	@echo "🔧 Demo Configuration Help"
	@echo ""
	@echo "Configuration:"
	@echo "  The demo uses hardcoded values in src/main.py:"
	@echo "  • Region: us-phoenix-1"
	@echo "  • Profile: demo_profile (created automatically)"
	@echo "  • Compartment ID: Update this in src/main.py with your actual OCID"
	@echo ""
	@echo "What the demo does:"
	@echo "  1. Creates a session token for 'demo_profile' using create_session_token()"
	@echo "  2. Lists OKE cluster instances"
	@echo "  3. Lists ODO instances"
	@echo "  4. Shows session token management examples"
	@echo ""
	@echo "Prerequisites:"
	@echo "  • OCI CLI installed: pip install oci-cli"
	@echo "  • Valid Oracle Cloud tenancy access"
	@echo "  • At least one existing OCI profile (DEFAULT) for session token creation"
	@echo ""
	@echo "Authentication Setup:"
	@echo "  # Create an initial profile for session token creation:"
	@echo "  oci session authenticate --profile-name DEFAULT --region us-phoenix-1"
	@echo ""
	@echo "Run the demo:"
	@echo "  make demo"

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