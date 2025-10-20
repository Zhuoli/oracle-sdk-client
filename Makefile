# Makefile for OCI SSH Sync
# SSH Configuration Generator for Oracle Deployment Orchestrator
# Provides convenient commands for development and SSH config generation

CMD_COLOR=\033[1;36m
DESC_COLOR=\033[0;37m
TITLE_COLOR=\033[1;33m
RESET=\033[0m

.PHONY: help install test ssh-sync clean format lint type-check dev-setup ssh-sync-remote-observer-dev ssh-sync-remote-observer-staging ssh-sync-remote-observer-prod ssh-sync-today-all-dev ssh-sync-today-all-staging ssh-sync-today-all-prod ssh-help test-coverage check setup-example quickstart image-updates recycle-node-pools delete-bucket delete-oke-cluster oke-version-report oke-upgrade oke-upgrade-node-pools

# Default target
help:
	@printf "$(TITLE_COLOR)🔧 OCI SSH Sync - Available Commands$(RESET)\n\n"
	@printf "$(TITLE_COLOR)Setup Commands:$(RESET)\n"
	@printf "  $(CMD_COLOR)install$(RESET)       $(DESC_COLOR)Install dependencies using Poetry$(RESET)\n"
	@printf "  $(CMD_COLOR)dev-setup$(RESET)     $(DESC_COLOR)Complete development setup (install + pre-commit hooks)$(RESET)\n\n"
	@printf "$(TITLE_COLOR)SSH Sync Commands:$(RESET)\n"
	@printf "  $(CMD_COLOR)ssh-sync$(RESET)      $(DESC_COLOR)Generate SSH config for OCI instances$(RESET)\n"
	@printf "  $(CMD_COLOR)oke-version-report$(RESET) $(DESC_COLOR)Generate HTML report of OKE cluster and node pool versions$(RESET)\n"
	@printf "  $(CMD_COLOR)oke-upgrade$(RESET)   $(DESC_COLOR)Trigger OKE cluster upgrades using a report file$(RESET)\n"
	@printf "  $(CMD_COLOR)oke-upgrade-node-pools$(RESET) $(DESC_COLOR)Cascade node pool upgrades after the control plane$(RESET)\n"
	@printf "  $(CMD_COLOR)ssh-help$(RESET)      $(DESC_COLOR)Show SSH sync configuration help$(RESET)\n"
	@printf "  $(CMD_COLOR)image-updates$(RESET) $(DESC_COLOR)Check for newer images for compute instances (by project/stage)$(RESET)\n"
	@printf "  $(CMD_COLOR)recycle-node-pools$(RESET) $(DESC_COLOR)CSV=<file> [DRY_RUN=1] [CONFIG=~/.oci/config] [POLL_SECONDS=$(POLL_SECONDS)]$(RESET)\n"
	@printf "  $(CMD_COLOR)delete-bucket$(RESET) $(DESC_COLOR)PROJECT=<name> STAGE=<env> REGION=<id> BUCKET=<bucket> [NAMESPACE=<override>]$(RESET)\n"
	@printf "  $(CMD_COLOR)delete-oke-cluster$(RESET) $(DESC_COLOR)PROJECT=<name> STAGE=<env> REGION=<id> CLUSTER_ID=<ocid> [SKIP_NODE_POOLS=1]$(RESET)\n\n"
	@printf "$(TITLE_COLOR)Development Commands:$(RESET)\n"
	@printf "  $(CMD_COLOR)test$(RESET)          $(DESC_COLOR)Run all tests$(RESET)\n"
	@printf "  $(CMD_COLOR)test-verbose$(RESET)  $(DESC_COLOR)Run tests with verbose output$(RESET)\n"
	@printf "  $(CMD_COLOR)format$(RESET)        $(DESC_COLOR)Format code with black and isort$(RESET)\n"
	@printf "  $(CMD_COLOR)lint$(RESET)          $(DESC_COLOR)Run linting with flake8$(RESET)\n"
	@printf "  $(CMD_COLOR)type-check$(RESET)    $(DESC_COLOR)Run type checking with mypy$(RESET)\n"
	@printf "  $(CMD_COLOR)clean$(RESET)         $(DESC_COLOR)Clean up temporary files and caches$(RESET)\n\n"
	@printf "$(TITLE_COLOR)SSH Sync Configuration:$(RESET)\n"
	@printf "  $(DESC_COLOR)Uses meta.yaml configuration file for project/stage/region mapping$(RESET)\n\n"
	@printf "$(TITLE_COLOR)Example:$(RESET)\n"
	@printf "  $(DESC_COLOR)make ssh-sync PROJECT=remote-observer STAGE=dev$(RESET)\n\n"
	@printf "$(TITLE_COLOR)For detailed configuration help:$(RESET)\n"
	@printf "  $(DESC_COLOR)make ssh-help$(RESET)\n"

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

oke-version-report:
	@echo "📄 Generating OKE version HTML report..."
	@if [ -z "$(PROJECT)" ] || [ -z "$(STAGE)" ]; then \
		echo "❌ Error: PROJECT and STAGE parameters are required"; \
		echo "Usage: make oke-version-report PROJECT=<project_name> STAGE=<stage> [META=tools/meta.yaml] [OUTPUT_DIR=reports]"; \
		exit 1; \
	fi
	@META_FLAG=""; \
	if [ -n "$(META)" ]; then \
		case "$(META)" in \
			/*) META_FLAG="--config-file $(META)";; \
			*) META_FLAG="--config-file ../$(META)";; \
		esac; \
	fi; \
	OUTPUT_FLAG=""; \
	if [ -n "$(OUTPUT_DIR)" ]; then \
		case "$(OUTPUT_DIR)" in \
			/*) OUTPUT_FLAG="--output-dir $(OUTPUT_DIR)";; \
			*) OUTPUT_FLAG="--output-dir ../$(OUTPUT_DIR)";; \
		esac; \
	fi; \
	cd tools && poetry run python src/oke_version_report.py $(PROJECT) $(STAGE) $$META_FLAG $$OUTPUT_FLAG

oke-upgrade:
	@echo "🚀 Triggering OKE cluster upgrades..."
	@if [ -z "$(REPORT)" ]; then \
		echo "❌ Error: REPORT=<path_to_report.html> is required"; \
		echo "Usage: make oke-upgrade REPORT=reports/oke_versions_project_stage.html [TARGET_VERSION=1.34.1] [PROJECT=<name>] [STAGE=<env>] [REGION=<id>] [CLUSTER=<ocid_or_name>] [DRY_RUN=1] [VERBOSE=1]"; \
		exit 1; \
	fi
	@REPORT_ARG=""; \
	case "$(REPORT)" in \
		/*) REPORT_ARG="$(REPORT)";; \
		*) REPORT_ARG="../$(REPORT)";; \
	esac; \
	TARGET_FLAG=""; \
	if [ -n "$(TARGET_VERSION)" ]; then \
		TARGET_FLAG="--target-version $(TARGET_VERSION)"; \
	fi; \
	PROJECT_FLAG=""; \
	if [ -n "$(PROJECT)" ]; then \
		PROJECT_FLAG="--project $(PROJECT)"; \
	fi; \
	STAGE_FLAG=""; \
	if [ -n "$(STAGE)" ]; then \
		STAGE_FLAG="--stage $(STAGE)"; \
	fi; \
	REGION_FLAG=""; \
	if [ -n "$(REGION)" ]; then \
		REGION_FLAG="--region $(REGION)"; \
	fi; \
	CLUSTER_FLAG=""; \
	if [ -n "$(CLUSTER)" ]; then \
		CLUSTER_FLAG="--cluster $(CLUSTER)"; \
	fi; \
	DRY_RUN_FLAG=""; \
	if [ "$(DRY_RUN)" = "1" ] || [ "$(DRY_RUN)" = "true" ] || [ "$(DRY_RUN)" = "TRUE" ] || [ "$(DRY_RUN)" = "yes" ] || [ "$(DRY_RUN)" = "YES" ]; then \
		DRY_RUN_FLAG="--dry-run"; \
	fi; \
	VERBOSE_FLAG=""; \
	if [ "$(VERBOSE)" = "1" ] || [ "$(VERBOSE)" = "true" ] || [ "$(VERBOSE)" = "TRUE" ] || [ "$(VERBOSE)" = "yes" ] || [ "$(VERBOSE)" = "YES" ]; then \
		VERBOSE_FLAG="--verbose"; \
	fi; \
	cd tools && poetry run python src/oke_upgrade.py $$REPORT_ARG $$TARGET_FLAG $$PROJECT_FLAG $$STAGE_FLAG $$REGION_FLAG $$CLUSTER_FLAG $$DRY_RUN_FLAG $$VERBOSE_FLAG

oke-upgrade-node-pools:
	@echo "🌊 Triggering OKE node pool upgrades..."
	@if [ -z "$(REPORT)" ]; then \
		echo "❌ Error: REPORT=<path_to_report.html> is required"; \
		echo "Usage: make oke-upgrade-node-pools REPORT=reports/oke_versions_project_stage.html [TARGET_VERSION=1.34.1] [PROJECT=<name>] [STAGE=<env>] [REGION=<id>] [CLUSTER=<ocid_or_name>] [NODE_POOL=<id_or_name>] [DRY_RUN=1] [VERBOSE=1]"; \
		exit 1; \
	fi
	@REPORT_ARG=""; \
	case "$(REPORT)" in \
		/*) REPORT_ARG="$(REPORT)";; \
		*) REPORT_ARG="../$(REPORT)";; \
	esac; \
	TARGET_FLAG=""; \
	if [ -n "$(TARGET_VERSION)" ]; then \
		TARGET_FLAG="--target-version $(TARGET_VERSION)"; \
	fi; \
	PROJECT_FLAG=""; \
	if [ -n "$(PROJECT)" ]; then \
		PROJECT_FLAG="--project $(PROJECT)"; \
	fi; \
	STAGE_FLAG=""; \
	if [ -n "$(STAGE)" ]; then \
		STAGE_FLAG="--stage $(STAGE)"; \
	fi; \
	REGION_FLAG=""; \
	if [ -n "$(REGION)" ]; then \
		REGION_FLAG="--region $(REGION)"; \
	fi; \
	CLUSTER_FLAG=""; \
	if [ -n "$(CLUSTER)" ]; then \
		CLUSTER_FLAG="--cluster $(CLUSTER)"; \
	fi; \
	NODE_POOL_FLAG=""; \
	if [ -n "$(NODE_POOL)" ]; then \
		for NP in $(NODE_POOL); do \
			NODE_POOL_FLAG="$$NODE_POOL_FLAG --node-pool $$NP"; \
		done; \
	fi; \
	DRY_RUN_FLAG=""; \
	if [ "$(DRY_RUN)" = "1" ] || [ "$(DRY_RUN)" = "true" ] || [ "$(DRY_RUN)" = "TRUE" ] || [ "$(DRY_RUN)" = "yes" ] || [ "$(DRY_RUN)" = "YES" ]; then \
		DRY_RUN_FLAG="--dry-run"; \
	fi; \
	VERBOSE_FLAG=""; \
	if [ "$(VERBOSE)" = "1" ] || [ "$(VERBOSE)" = "true" ] || [ "$(VERBOSE)" = "TRUE" ] || [ "$(VERBOSE)" = "yes" ] || [ "$(VERBOSE)" = "YES" ]; then \
		VERBOSE_FLAG="--verbose"; \
	fi; \
	cd tools && poetry run python src/oke_node_pool_upgrade.py $$REPORT_ARG $$TARGET_FLAG $$PROJECT_FLAG $$STAGE_FLAG $$REGION_FLAG $$CLUSTER_FLAG $$NODE_POOL_FLAG $$DRY_RUN_FLAG $$VERBOSE_FLAG

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

delete-bucket:
	@if [ -z "$(PROJECT)" ] || [ -z "$(STAGE)" ] || [ -z "$(REGION)" ] || [ -z "$(BUCKET)" ]; then \
		echo "❌ Error: PROJECT, STAGE, REGION, and BUCKET parameters are required"; \
		echo "Usage: make delete-bucket PROJECT=<project> STAGE=<stage> REGION=<region> BUCKET=<bucket> [NAMESPACE=<namespace>]"; \
		exit 1; \
	fi
	@echo "🗑️  Deleting bucket '$(BUCKET)' from namespace $${NAMESPACE:-<tenancy default>}..."
	cd tools && poetry run python src/delete_resources.py \
		--project "$(PROJECT)" \
		--stage "$(STAGE)" \
		--region "$(REGION)" \
		bucket \
		--bucket-name "$(BUCKET)" \
		$$( [ -n "$(NAMESPACE)" ] && printf -- "--namespace %s" "$(NAMESPACE)" )

delete-oke-cluster:
	@if [ -z "$(PROJECT)" ] || [ -z "$(STAGE)" ] || [ -z "$(REGION)" ] || [ -z "$(CLUSTER_ID)" ]; then \
		echo "❌ Error: PROJECT, STAGE, REGION, and CLUSTER_ID parameters are required"; \
		echo "Usage: make delete-oke-cluster PROJECT=<project> STAGE=<stage> REGION=<region> CLUSTER_ID=<ocid> [SKIP_NODE_POOLS=1]"; \
		exit 1; \
	fi
	@echo "🗑️  Deleting OKE cluster '$(CLUSTER_ID)'..."
	cd tools && poetry run python src/delete_resources.py \
		--project "$(PROJECT)" \
		--stage "$(STAGE)" \
		--region "$(REGION)" \
		oke-cluster \
		--cluster-id "$(CLUSTER_ID)" \
		$$( [ "$(SKIP_NODE_POOLS)" = "1" ] || [ "$(SKIP_NODE_POOLS)" = "true" ] || [ "$(SKIP_NODE_POOLS)" = "TRUE" ] || [ "$(SKIP_NODE_POOLS)" = "yes" ] || [ "$(SKIP_NODE_POOLS)" = "YES" ] && printf -- "--skip-node-pools" )

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
