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
	@echo "Usage: make demo PROJECT=<project_name> STAGE=<stage>"
	@echo "Example: make demo PROJECT=remote-observer STAGE=dev"
	@echo ""
	@if [ -z "$(PROJECT)" ] || [ -z "$(STAGE)" ]; then \
		echo "❌ Error: PROJECT and STAGE parameters are required"; \
		echo ""; \
		echo "Available projects and stages from meta.yaml:"; \
		echo "  remote-observer: dev, staging, prod"; \
		echo "  today-all: dev, staging, prod"; \
		echo ""; \
		echo "Examples:"; \
		echo "  make demo PROJECT=remote-observer STAGE=dev"; \
		echo "  make demo PROJECT=today-all STAGE=staging"; \
		echo "  make demo PROJECT=remote-observer STAGE=prod"; \
		exit 1; \
	fi
	poetry run python src/main.py $(PROJECT) $(STAGE)

# Alternative demo targets for convenience
demo-remote-observer-dev:
	@echo "🚀 Running demo for remote-observer dev environment..."
	poetry run python src/main.py remote-observer dev

demo-remote-observer-staging:
	@echo "🚀 Running demo for remote-observer staging environment..."
	poetry run python src/main.py remote-observer staging

demo-remote-observer-prod:
	@echo "🚀 Running demo for remote-observer prod environment..."
	poetry run python src/main.py remote-observer prod

demo-today-all-dev:
	@echo "🚀 Running demo for today-all dev environment..."
	poetry run python src/main.py today-all dev

demo-today-all-staging:
	@echo "🚀 Running demo for today-all staging environment..."
	poetry run python src/main.py today-all staging

demo-today-all-prod:
	@echo "🚀 Running demo for today-all prod environment..."
	poetry run python src/main.py today-all prod

demo-help:
	@echo "🔧 Demo Configuration Help"
	@echo ""
	@echo "Configuration:"
	@echo "  The demo uses YAML configuration from meta.yaml file"
	@echo "  • Supports multiple projects: remote-observer, today-all"
	@echo "  • Supports multiple stages: dev, staging, prod"
	@echo "  • Automatically creates session tokens for each region"
	@echo ""
	@echo "What the demo does:"
	@echo "  1. Parses meta.yaml to get region:compartment_id pairs"
	@echo "  2. Creates session tokens for each region using create_session_token()"
	@echo "  3. Lists OKE cluster instances across all regions"
	@echo "  4. Lists ODO instances across all regions"
	@echo "  5. Shows session token management examples"
	@echo ""
	@echo "Available Commands:"
	@echo "  make demo PROJECT=<project> STAGE=<stage>  # Generic demo"
	@echo "  make demo-remote-observer-dev              # Specific shortcuts"
	@echo "  make demo-remote-observer-staging"
	@echo "  make demo-remote-observer-prod"
	@echo "  make demo-today-all-dev"
	@echo "  make demo-today-all-staging"
	@echo "  make demo-today-all-prod"
	@echo ""
	@echo "Prerequisites:"
	@echo "  • OCI CLI installed: pip install oci-cli"
	@echo "  • Valid Oracle Cloud tenancy access"
	@echo "  • At least one existing OCI profile (DEFAULT) for session token creation"
	@echo "  • PyYAML package installed (included in dependencies)"
	@echo ""
	@echo "Authentication Setup:"
	@echo "  # Create an initial profile for session token creation:"
	@echo "  oci session authenticate --profile-name DEFAULT --region us-phoenix-1"
	@echo ""
	@echo "Examples:"
	@echo "  make demo PROJECT=remote-observer STAGE=dev"
	@echo "  make demo-today-all-staging"

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