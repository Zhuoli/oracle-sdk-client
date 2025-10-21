"""OKE node pool image bump utility.

Reads a CSV file describing compute hosts slated for operating system patching,
identifies their backing pools (OKE node pools or compute instance pools) using
the same meta.yaml mapping as ``ssh_sync``, and bumps their images when a newer
version is available. The workflow mirrors the production change records
operators expect while avoiding unnecessary churn when already up to date.

Supports:
- OKE Node Pools: Automatically switch to the target node image and rely on OCI cycling
- Instance Pools: Update instance configuration + rolling detach/attach
"""

from __future__ import annotations

import argparse
import csv
import getpass
import logging
import sys
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import oci
from oci import exceptions as oci_exceptions
from oci.container_engine import ContainerEngineClient
from oci.container_engine.models import (
    NodeEvictionNodePoolSettings,
    NodePoolCyclingDetails,
    UpdateNodePoolDetails,
)
from oci.core import ComputeManagementClient
import re
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax
import json

try:  # OCI SDK < 2.110.0 does not expose UpdateNodeSourceViaImageDetails
    from oci.container_engine.models import UpdateNodeSourceViaImageDetails as _UpdateNodeSourceViaImageDetails
except ImportError:  # pragma: no cover - defensive fallback for older SDKs
    _UpdateNodeSourceViaImageDetails = None
    try:
        from oci.container_engine.models import NodeSourceViaImageDetails as _NodeSourceViaImageDetails
    except ImportError:  # pragma: no cover - very old SDKs fallback to dict payloads
        _NodeSourceViaImageDetails = None
else:  # pragma: no cover - keep name defined for downstream logic
    _NodeSourceViaImageDetails = None

UpdateNodeSourceViaImageDetails = _UpdateNodeSourceViaImageDetails  # type: ignore[assignment]
NodeSourceViaImageDetails = _NodeSourceViaImageDetails  # type: ignore[assignment]
from oci.pagination import list_call_get_all_results
import yaml

from oci_client.client import OCIClient
from oci_client.utils.session import create_oci_client, setup_session_token

LOGGER_NAME = "oci_node_pool_image_bump"
DEFAULT_POLL_SECONDS = 30
TERMINAL_WORK_REQUEST_STATES = {"SUCCEEDED", "FAILED", "CANCELED"}
ACTIVE_INSTANCE_STATES = {
    "PROVISIONING",
    "STARTING",
    "RUNNING",
    "STOPPING",
    "STOPPED",
}


@dataclass
class CsvInstruction:
    host_name: str
    compartment_id: str
    current_image: str
    new_image_name: str


@dataclass
class NodeImageUpdatePlan:
    host_name: str
    compartment_id: str
    instance_id: str
    node_pool_id: str
    current_image: str
    resolved_image_name: Optional[str]
    new_image_name: str
    context: "CompartmentContext"


@dataclass
class NodePoolUpdateAction:
    node_pool_id: str
    new_image_name: str
    nodes: List[NodeImageUpdatePlan]
    context: "CompartmentContext"


@dataclass
class InstanceImageUpdatePlan:
    host_name: str
    compartment_id: str
    instance_id: str
    instance_pool_id: str
    current_image: str
    resolved_image_name: Optional[str]
    new_image_name: str
    context: "CompartmentContext"


@dataclass
class InstancePoolUpdateAction:
    instance_pool_id: str
    new_image_name: str
    instances: List[InstanceImageUpdatePlan]
    context: "CompartmentContext"


@dataclass
class WorkRequestResult:
    description: str
    status: str
    work_request_id: Optional[str] = None
    accepted_time: Optional[datetime] = None
    finished_time: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    errors: List[str] = field(default_factory=list)


@dataclass
class NodePoolSummary:
    node_pool_id: str
    target_image: str
    context: "CompartmentContext"
    compartment_id: Optional[str] = None
    original_image_name: Optional[str] = None
    original_image_id: Optional[str] = None
    target_image_id: Optional[str] = None
    update_result: Optional[WorkRequestResult] = None
    node_results: List[WorkRequestResult] = field(default_factory=list)
    post_state: Optional[str] = None
    post_image_name: Optional[str] = None
    post_node_states: List[Tuple[str, str]] = field(default_factory=list)
    # Configuration change tracking
    update_initiated_at: Optional[datetime] = None
    cycling_config: Optional[Dict[str, Any]] = field(default_factory=dict)
    eviction_config: Optional[Dict[str, Any]] = field(default_factory=dict)


@dataclass
class InstancePoolSummary:
    instance_pool_id: str
    target_image: str
    context: "CompartmentContext"
    compartment_id: Optional[str] = None
    original_image_name: Optional[str] = None
    original_image_id: Optional[str] = None
    original_instance_config_id: Optional[str] = None
    new_instance_config_id: Optional[str] = None
    target_image_id: Optional[str] = None
    update_result: Optional[WorkRequestResult] = None
    instance_results: List[WorkRequestResult] = field(default_factory=list)
    post_state: Optional[str] = None
    post_instance_count: int = 0
    detached_count: int = 0
    # Configuration change tracking
    update_initiated_at: Optional[datetime] = None
    config_created_at: Optional[datetime] = None


@dataclass(frozen=True)
class CompartmentContext:
    project: str
    stage: str
    region: str


class NodePoolImageUpdater:
    def __init__(
        self,
        csv_path: Path,
        config_file: Optional[Path],
        dry_run: bool,
        poll_seconds: int = DEFAULT_POLL_SECONDS,
        log_dir: Optional[Path] = None,
        meta_file: Optional[Path] = None,
        verbose: bool = False,
    ) -> None:
        self.csv_path = csv_path
        self.config_file = config_file
        self.dry_run = dry_run
        self.poll_seconds = poll_seconds
        self.verbose = verbose
        self._log_level = logging.DEBUG if verbose else logging.INFO
        self.logger = logging.getLogger(LOGGER_NAME)
        self.logger.setLevel(self._log_level)
        self.console = Console()

        self.log_dir = log_dir if log_dir else determine_default_log_dir()

        self.meta_file = meta_file if meta_file else self._default_meta_path()

        # Region-scoped caches keep remote lookups to a minimum while iterating across many hosts.
        self._instance_cache: Dict[
            Tuple[str, str, str, str], Sequence[oci.core.models.Instance]
        ] = {}
        self._image_cache: Dict[str, Optional[str]] = {}
        self._node_pool_cache: Dict[
            Tuple[str, str, str, str], Optional[oci.container_engine.models.NodePool]
        ] = {}
        self._errors: List[str] = []
        self._summaries: List[NodePoolSummary] = []
        self._instance_pool_summaries: List[InstancePoolSummary] = []
        self._timestamp_label: Optional[str] = None
        self._log_path: Optional[Path] = None
        self._report_path: Optional[Path] = None
        # Reuse ssh_sync session helpers so production auth flows remain consistent.
        self._session_clients: Dict[Tuple[str, str, str], "OCIClient"] = {}
        self._ce_clients: Dict[Tuple[str, str, str], ContainerEngineClient] = {}
        self._cm_clients: Dict[Tuple[str, str, str], ComputeManagementClient] = {}
        self._used_contexts: Set[Tuple[str, str, str]] = set()

        self._configure_logging()
        self._compartment_lookup: Dict[str, CompartmentContext] = self._load_compartment_lookup()
        self._total_rows: int = 0
        self._resolved_rows: int = 0
        self._missing_hosts: List[Tuple[str, str, str]] = []

    # ------------------------------------------------------------------
    # Public execution entrypoint
    # ------------------------------------------------------------------
    def run(self) -> int:
        """Main entry point for the node pool image bump workflow."""
        instructions = self._load_instructions()
        if not instructions:
            if self._errors:
                self.logger.error("No actionable rows found in %s", self.csv_path)
                return 1
            self.logger.info(
                "No actionable rows found in %s; nothing to bump.",
                self.csv_path,
            )
            self._generate_report()
            return 0

        node_pool_plans, instance_pool_plans = self._build_plans(instructions)
        if not node_pool_plans and not instance_pool_plans:
            self.logger.error("Unable to resolve any pools from provided CSV")
            return 1

        # End-to-end execution: act, wait for OCI to settle, then emit human-readable artifacts.
        self._execute(node_pool_plans, instance_pool_plans)
        self._generate_report()

        if self._errors:
            self.logger.error("Encountered %d issues during processing", len(self._errors))
            for issue in self._errors:
                self.logger.error(issue)
            return 1
        self.logger.info("All requested node pool image bump operations completed successfully")
        return 0

    # ------------------------------------------------------------------
    # Configuration & logging
    # ------------------------------------------------------------------
    def _default_meta_path(self) -> Path:
        """Return the default path to meta.yaml (shared with ssh_sync)."""
        return Path(__file__).resolve().parents[1] / "meta.yaml"

    def _context_key(self, context: CompartmentContext) -> Tuple[str, str, str]:
        """Build a unique cache key for the given project/stage/region context."""
        return (context.project, context.stage, context.region)

    def _load_compartment_lookup(self) -> Dict[str, CompartmentContext]:
        """Parse meta.yaml and map compartment OCIDs to project/stage/region tuples."""
        lookup: Dict[str, CompartmentContext] = {}
        if not self.meta_file.exists():
            self.logger.error("Meta file not found: %s", self.meta_file)
            return lookup

        try:
            with self.meta_file.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
        except Exception as exc:
            self.logger.error("Failed to parse meta file %s: %s", self.meta_file, exc)
            return lookup

        projects = data.get("projects", {}) if isinstance(data, dict) else {}
        for project_name, stages in projects.items():
            if not isinstance(stages, dict):
                continue
            for stage_name, realms in stages.items():
                if not isinstance(realms, dict):
                    continue
                for regions in realms.values():
                    if not isinstance(regions, dict):
                        continue
                    for region_name, details in regions.items():
                        if not isinstance(details, dict):
                            continue
                        compartment_id = details.get("compartment_id")
                        if not compartment_id:
                            continue
                        lookup[compartment_id] = CompartmentContext(
                            project=project_name,
                            stage=stage_name,
                            region=region_name,
                        )

        self.logger.info(
            "Loaded %d compartment mapping(s) from %s",
            len(lookup),
            self.meta_file,
        )
        return lookup

    def _configure_logging(self) -> None:
        """Initialize stream & file logging for this run."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_path = self.log_dir / f"node_pool_image_bump_{timestamp}.log"
        report_path = self.log_dir / f"node_pool_image_bump_{timestamp}.html"
        self._timestamp_label = timestamp
        self._log_path = log_path
        self._report_path = report_path

        # Share a single timestamped set of artifacts (log + markdown report) for every execution.
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(self._log_level)
        file_handler.setFormatter(formatter)

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(self._log_level)
        stream_handler.setFormatter(formatter)

        self.logger.handlers.clear()
        self.logger.addHandler(file_handler)
        self.logger.addHandler(stream_handler)

        self.logger.info("Logging initialized. Log file: %s", log_path)

    # ------------------------------------------------------------------
    # Client management
    # ------------------------------------------------------------------
    def _get_client(self, context: CompartmentContext) -> Optional[OCIClient]:
        """Create or reuse an authenticated OCIClient for a specific project/stage/region."""
        key = self._context_key(context)
        if key in self._session_clients:
            return self._session_clients[key]

        # Leverage ssh_sync's session-token workflow so operators authenticate the same way here.
        profile_name = setup_session_token(context.project, context.stage, context.region)

        client = create_oci_client(context.region, profile_name)
        if not client:
            message = "Failed to initialize OCI client for region {region} (project={project}, stage={stage})".format(
                region=context.region,
                project=context.project,
                stage=context.stage,
            )
            self.logger.error(message)
            self._errors.append(message)
            return None

        self._session_clients[key] = client
        self.logger.info(
            "Initialized OCI client for %s/%s in %s using profile '%s'",
            context.project,
            context.stage,
            context.region,
            profile_name,
        )
        return client

    def _get_ce_client(self, context: CompartmentContext) -> Optional[ContainerEngineClient]:
        """Create or reuse an OCI Container Engine client for the supplied context."""
        key = self._context_key(context)
        if key in self._ce_clients:
            return self._ce_clients[key]

        client = self._get_client(context)
        if not client:
            return None

        ce_client = ContainerEngineClient(
            client.oci_config,
            signer=client.signer,
            retry_strategy=client.retry_strategy,
        )
        self._ce_clients[key] = ce_client
        return ce_client

    def _get_cm_client(self, context: CompartmentContext) -> Optional[ComputeManagementClient]:
        """Create or reuse an OCI Compute Management client for the supplied context."""
        key = self._context_key(context)
        if key in self._cm_clients:
            return self._cm_clients[key]

        client = self._get_client(context)
        if not client:
            return None

        cm_client = ComputeManagementClient(
            client.oci_config,
            signer=client.signer,
            retry_strategy=client.retry_strategy,
        )
        self._cm_clients[key] = cm_client
        return cm_client

    # ------------------------------------------------------------------
    # CSV ingestion and plan building
    # ------------------------------------------------------------------
    def _load_instructions(self) -> List[CsvInstruction]:
        """Read the CSV and normalize required columns into CsvInstruction objects."""
        if not self.csv_path.exists():
            self._errors.append(f"CSV file not found: {self.csv_path}")
            return []

        with self.csv_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                self._errors.append("CSV file missing header row")
                return []

            column_map = self._build_column_map(reader.fieldnames)
            missing = {
                "compute instance host name",
                "compartment id",
                "current image",
                "new image name",
            } - set(column_map)
            if missing:
                self._errors.append(
                    "CSV header missing required columns: " + ", ".join(sorted(missing))
                )
                return []
            rows: List[CsvInstruction] = []
            for raw_row in reader:
                if not raw_row:
                    continue
                host = (raw_row.get(column_map["compute instance host name"], "") or "").strip()
                compartment = (raw_row.get(column_map["compartment id"], "") or "").strip()
                current_image = (raw_row.get(column_map["current image"], "") or "").strip()
                new_image = (raw_row.get(column_map["new image name"], "") or "").strip()

                # Skip rows where 'Newer Available Image' is empty or '-' or '—' (already using latest image)
                # Note: CSV may contain em-dash (—) or regular hyphen (-)
                if not new_image or new_image in ("-", "—", "–"):
                    self.logger.debug(
                        "Skipping row for host=%r - already using latest image (new_image=%r)",
                        host,
                        new_image,
                    )
                    continue

                if not (host and compartment):
                    self.logger.warning(
                        "Skipping row with missing required data: host=%r compartment=%r new_image=%r",
                        host,
                        compartment,
                        new_image,
                    )
                    continue

                rows.append(
                    CsvInstruction(
                        host_name=host,
                        compartment_id=compartment,
                        current_image=current_image,
                        new_image_name=new_image,
                    )
                )
                self._total_rows += 1

        self.logger.info("Loaded %d instruction(s) from %s", len(rows), self.csv_path)
        return rows

    @staticmethod
    def _normalize_header(value: str) -> str:
        return " ".join(value.strip().lower().split())

    def _build_column_map(self, headers: Sequence[str]) -> Dict[str, str]:
        """Map the exact expected headers from the CSV to canonical column keys."""

        normalized = {self._normalize_header(name): name for name in headers}
        expected = {
            "host name": "compute instance host name",
            "compartment id": "compartment id",
            "current image": "current image",
            "newer available image": "new image name",
        }

        mapping: Dict[str, str] = {}
        for header_key, canonical in expected.items():
            original = normalized.get(header_key)
            if original:
                mapping[canonical] = original

        missing = {
            "compute instance host name",
            "compartment id",
            "current image",
            "new image name",
        } - set(mapping)

        if missing:
            self.logger.error(
                "CSV header missing required columns: %s",
                ", ".join(sorted(missing)),
            )

        return mapping

    def _build_plans(
        self, instructions: Iterable[CsvInstruction]
    ) -> Tuple[List[NodePoolUpdateAction], List[InstancePoolUpdateAction]]:
        """Group CSV instructions by pool type (OKE node pool or instance pool).

        Returns:
            Tuple of (node_pool_actions, instance_pool_actions)
        """
        node_pool_plans: Dict[Tuple[str, str, str, str], NodePoolUpdateAction] = {}
        instance_pool_plans: Dict[Tuple[str, str, str, str], InstancePoolUpdateAction] = {}

        for instruction in instructions:
            context = self._compartment_lookup.get(instruction.compartment_id)
            if not context:
                self._errors.append(
                    "Compartment {compartment} not found in meta configuration".format(
                        compartment=instruction.compartment_id
                    )
                )
                continue

            instance = self._find_instance(
                instruction.host_name, instruction.compartment_id, context
            )

            # If instance not found by hostname, check if hostname is actually an instance pool name
            if not instance:
                self.logger.debug(
                    "Instance not found by hostname '%s', checking if it's an instance pool name",
                    instruction.host_name
                )
                instance_pool = self._find_instance_pool_by_name(
                    instruction.host_name, instruction.compartment_id, context
                )
                if instance_pool:
                    self.logger.info(
                        "Found instance pool '%s' (id=%s), getting instances from pool",
                        instance_pool.display_name,
                        instance_pool.id[-12:]
                    )
                    # Get any instance from the pool to extract pool ID
                    pool_instances = self._get_instance_pool_instances(
                        instance_pool.id, instruction.compartment_id, context
                    )
                    if pool_instances:
                        instance = pool_instances[0]
                        self.logger.info(
                            "Using instance '%s' from pool to process cycling",
                            getattr(instance, "display_name", instance.id[-12:])
                        )

            if not instance:
                self._missing_hosts.append(
                    (
                        instruction.host_name,
                        instruction.compartment_id,
                        "No active compute instance or instance pool found",
                    )
                )
                self.logger.warning(
                    "Skipping host '%s' (compartment %s) because no active compute instance or instance pool was found",
                    instruction.host_name,
                    instruction.compartment_id,
                )
                continue

            # Check if instance belongs to OKE node pool
            node_pool_id = self._extract_node_pool_id(instance)
            if node_pool_id:
                self._process_node_pool_instance(
                    instruction, instance, node_pool_id, context, node_pool_plans
                )
                continue

            # Check if instance belongs to compute instance pool
            instance_pool_id = self._extract_instance_pool_id(instance)
            if instance_pool_id:
                self._process_instance_pool_instance(
                    instruction, instance, instance_pool_id, context, instance_pool_plans
                )
                continue

            # Instance doesn't belong to any pool
            self._missing_hosts.append(
                (
                    instruction.host_name,
                    instruction.compartment_id,
                    "Not part of OKE node pool or instance pool",
                )
            )
            self.logger.warning(
                "Skipping host '%s' because it's not part of any pool",
                instruction.host_name,
            )

        node_pool_list = [action for action in node_pool_plans.values() if action.nodes]
        instance_pool_list = [action for action in instance_pool_plans.values() if action.instances]

        self.logger.info(
            "Prepared image bump plan: %d OKE node pool(s) with %d node(s), "
            "%d instance pool(s) with %d instance(s)",
            len(node_pool_list),
            sum(len(action.nodes) for action in node_pool_list),
            len(instance_pool_list),
            sum(len(action.instances) for action in instance_pool_list),
        )
        return node_pool_list, instance_pool_list

    def _process_node_pool_instance(
        self,
        instruction: CsvInstruction,
        instance: oci.core.models.Instance,
        node_pool_id: str,
        context: CompartmentContext,
        plans: Dict[Tuple[str, str, str, str], NodePoolUpdateAction],
    ) -> None:
        """Process an instance that belongs to an OKE node pool."""
        resolved_image = self._resolve_image_name(context, instance)
        if (
            instruction.current_image
            and resolved_image
            and instruction.current_image.strip().lower() != resolved_image.strip().lower()
        ):
            # Flag drift early so the runbook reflects what is actually running before the bump.
            self.logger.warning(
                "Image mismatch for host %s: CSV=%s actual=%s",
                instruction.host_name,
                instruction.current_image,
                resolved_image,
            )

        plan_entry = NodeImageUpdatePlan(
            host_name=instruction.host_name,
            compartment_id=instruction.compartment_id,
            instance_id=instance.id,
            node_pool_id=node_pool_id,
            current_image=instruction.current_image,
            resolved_image_name=resolved_image,
            new_image_name=instruction.new_image_name,
            context=context,
        )

        key = (*self._context_key(context), node_pool_id)
        if key not in plans:
            plans[key] = NodePoolUpdateAction(
                node_pool_id=node_pool_id,
                new_image_name=instruction.new_image_name,
                nodes=[plan_entry],
                context=context,
            )
            self._used_contexts.add(self._context_key(context))
        else:
            action = plans[key]
            if action.context != context:
                self._errors.append(
                    "Conflicting compartment context detected for node pool {node_pool}".format(
                        node_pool=node_pool_id
                    )
                )
                return
            if (
                action.new_image_name.strip().lower()
                != instruction.new_image_name.strip().lower()
            ):
                # Mixing target images inside the same pool is a misconfiguration we cannot recover from.
                self._errors.append(
                    "Conflicting target images for node pool {node_pool}: {existing} vs {incoming}".format(
                        node_pool=node_pool_id,
                        existing=action.new_image_name,
                        incoming=instruction.new_image_name,
                    )
                )
                return
            action.nodes.append(plan_entry)

        self._resolved_rows += 1

    def _process_instance_pool_instance(
        self,
        instruction: CsvInstruction,
        instance: oci.core.models.Instance,
        instance_pool_id: str,
        context: CompartmentContext,
        plans: Dict[Tuple[str, str, str, str], InstancePoolUpdateAction],
    ) -> None:
        """Process an instance that belongs to a compute instance pool."""
        resolved_image = self._resolve_image_name(context, instance)
        if (
            instruction.current_image
            and resolved_image
            and instruction.current_image.strip().lower() != resolved_image.strip().lower()
        ):
            self.logger.warning(
                "Image mismatch for host %s: CSV=%s actual=%s",
                instruction.host_name,
                instruction.current_image,
                resolved_image,
            )

        plan_entry = InstanceImageUpdatePlan(
            host_name=instruction.host_name,
            compartment_id=instruction.compartment_id,
            instance_id=instance.id,
            instance_pool_id=instance_pool_id,
            current_image=instruction.current_image,
            resolved_image_name=resolved_image,
            new_image_name=instruction.new_image_name,
            context=context,
        )

        key = (*self._context_key(context), instance_pool_id)
        if key not in plans:
            plans[key] = InstancePoolUpdateAction(
                instance_pool_id=instance_pool_id,
                new_image_name=instruction.new_image_name,
                instances=[plan_entry],
                context=context,
            )
            self._used_contexts.add(self._context_key(context))
        else:
            action = plans[key]
            if action.context != context:
                self._errors.append(
                    "Conflicting compartment context detected for instance pool {pool}".format(
                        pool=instance_pool_id
                    )
                )
                return
            if (
                action.new_image_name.strip().lower()
                != instruction.new_image_name.strip().lower()
            ):
                self._errors.append(
                    "Conflicting target images for instance pool {pool}: {existing} vs {incoming}".format(
                        pool=instance_pool_id,
                        existing=action.new_image_name,
                        incoming=instruction.new_image_name,
                    )
                )
                return
            action.instances.append(plan_entry)

        self._resolved_rows += 1

    # ------------------------------------------------------------------
    # Instance/node pool resolution helpers
    # ------------------------------------------------------------------
    def _find_instance(
        self, host_name: str, compartment_id: str, context: CompartmentContext
    ) -> Optional[oci.core.models.Instance]:
        """Locate a single active compute instance for the given host within the context."""
        host_key = host_name.lower()
        base_host_key = host_key.split(".")[0]

        self.logger.debug(
            "Looking for instance with host_name='%s' (base='%s') in compartment %s, region %s",
            host_name, base_host_key, compartment_id, context.region
        )

        matches: List[oci.core.models.Instance] = []
        instances = self._instances_for_compartment(context, compartment_id)

        self.logger.debug("Found %d total instances in compartment", len(instances))

        active_count = 0
        inactive_states = []
        all_active_names = []

        for instance in instances:
            if instance.lifecycle_state not in ACTIVE_INSTANCE_STATES:
                inactive_states.append((
                    getattr(instance, "display_name", "unknown"),
                    instance.lifecycle_state,
                    instance.id
                ))
                continue

            active_count += 1
            instance_names = self._candidate_names(instance)
            display_name = getattr(instance, "display_name", "N/A")
            all_active_names.append(display_name)

            # Detailed logging for each active instance
            self.logger.debug(
                "Instance %d: display_name='%s', id='%s', state='%s', candidate_names=%s",
                active_count,
                display_name,
                instance.id[-12:],
                instance.lifecycle_state,
                instance_names
            )

            if host_key in instance_names or base_host_key in instance_names:
                matches.append(instance)
                self.logger.debug(
                    "✓ MATCH FOUND: Instance '%s' matches search key",
                    getattr(instance, "display_name", instance.id)
                )

        # Log summary
        self.logger.info(
            "Instance search for '%s': %d active instances checked, %d matches found",
            host_name, active_count, len(matches)
        )

        if inactive_states:
            self.logger.debug(
                "Skipped %d inactive instances: %s",
                len(inactive_states),
                ", ".join(f"{name}({state})" for name, state, _ in inactive_states[:5])
            )

        if not matches:
            self.logger.warning(
                "No matching compute instance for host '%s' in compartment %s (searched %d active instances)",
                host_name,
                compartment_id,
                active_count
            )
            # Log what we were looking for vs what we found
            if active_count > 0:
                self.logger.warning(
                    "Search keys were: '%s' or '%s'. Try checking if hostname in CSV matches instance display_name/hostname in OCI.",
                    host_key, base_host_key
                )
                # Show actual instance names found (at INFO level so it's always visible)
                if all_active_names:
                    self.logger.info(
                        "Active instances found in compartment: %s",
                        ", ".join(f"'{name}'" for name in all_active_names[:10])
                    )
            return None

        if len(matches) > 1:
            self.logger.warning(
                "Multiple compute instances matched host '%s' in compartment %s; skipping. Matched: %s",
                host_name,
                compartment_id,
                ", ".join(getattr(m, "display_name", m.id) for m in matches)
            )
            return None

        return matches[0]

    def _instances_for_compartment(
        self, context: CompartmentContext, compartment_id: str
    ) -> Sequence[oci.core.models.Instance]:
        """List compute instances for a compartment, cached per context."""
        cache_key = (*self._context_key(context), compartment_id)
        if cache_key not in self._instance_cache:
            client = self._get_client(context)
            if not client:
                self._instance_cache[cache_key] = []
                return self._instance_cache[cache_key]

            compute_client = client.compute_client
            response = list_call_get_all_results(
                compute_client.list_instances,
                compartment_id,
            )
            self._instance_cache[cache_key] = response.data
            self.logger.info(
                "Fetched %d instance(s) for compartment %s in %s",
                len(response.data),
                compartment_id,
                context.region,
            )
        return self._instance_cache[cache_key]

    def _candidate_names(self, instance: oci.core.models.Instance) -> List[str]:
        """Extract all possible name variations from an instance for matching."""
        names: List[str] = []

        display_name = getattr(instance, "display_name", None)
        if display_name:
            names.append(str(display_name).lower())

        hostname_label = getattr(instance, "hostname_label", None)
        if hostname_label:
            names.append(str(hostname_label).lower())

        metadata = getattr(instance, "metadata", None)
        if isinstance(metadata, dict):
            hostname = instance.metadata.get("hostname") or instance.metadata.get("HostName")
            if hostname:
                names.append(str(hostname).lower())

        fqdn = getattr(instance, "fqdn", None)
        if fqdn:
            names.append(str(fqdn).lower())

        return names

    def _extract_node_pool_id(self, instance: oci.core.models.Instance) -> Optional[str]:
        """Extract OKE node pool ID from instance metadata/tags."""
        metadata_sources: List[Dict[str, str]] = []
        if instance.metadata:
            metadata_sources.append(instance.metadata)
        if instance.extended_metadata:
            metadata_sources.append(instance.extended_metadata)

        for source in metadata_sources:
            for key, value in source.items():
                if not isinstance(value, str):
                    continue
                lowered = value.lower()
                if "nodepool" in lowered and "ocid" in lowered:
                    return value

        tag_sources: List[Dict[str, str]] = []
        if instance.freeform_tags:
            tag_sources.append(instance.freeform_tags)
        if instance.defined_tags:
            for namespace in instance.defined_tags.values():
                if isinstance(namespace, dict):
                    tag_sources.append(namespace)

        for source in tag_sources:
            for value in source.values():
                if isinstance(value, str) and "nodepool" in value.lower():
                    return value

        return None

    def _extract_instance_pool_id(self, instance: oci.core.models.Instance) -> Optional[str]:
        """Extract compute instance pool ID from instance metadata/tags."""
        # Check metadata sources
        metadata_sources: List[Dict[str, str]] = []
        if instance.metadata:
            metadata_sources.append(instance.metadata)
        if instance.extended_metadata:
            metadata_sources.append(instance.extended_metadata)

        for source in metadata_sources:
            for key, value in source.items():
                if not isinstance(value, str):
                    continue
                lowered = value.lower()
                # Look for instance pool OCID pattern
                if "instancepool" in lowered and "ocid1.instancepool" in lowered:
                    return value

        # Check tags
        tag_sources: List[Dict[str, str]] = []
        if instance.freeform_tags:
            tag_sources.append(instance.freeform_tags)
        if instance.defined_tags:
            for namespace in instance.defined_tags.values():
                if isinstance(namespace, dict):
                    tag_sources.append(namespace)

        for source in tag_sources:
            for value in source.values():
                if isinstance(value, str) and "ocid1.instancepool" in value.lower():
                    return value

        return None

    def _find_instance_pool_by_name(
        self, pool_name: str, compartment_id: str, context: CompartmentContext
    ) -> Optional[Any]:
        """Find an instance pool by display name in the given compartment."""
        compute_mgmt_client = self._get_cm_client(context)
        if not compute_mgmt_client:
            return None

        try:
            pool_name_lower = pool_name.lower()

            # List all instance pools in the compartment
            response = compute_mgmt_client.list_instance_pools(compartment_id=compartment_id)
            pools = response.data

            # Find pool by display name (case-insensitive)
            for pool in pools:
                if pool.display_name and pool.display_name.lower() == pool_name_lower:
                    self.logger.debug(
                        "Found instance pool: display_name='%s', id='%s', state='%s'",
                        pool.display_name,
                        pool.id[-12:],
                        pool.lifecycle_state
                    )
                    return pool

            self.logger.debug(
                "No instance pool found with name '%s' in compartment %s",
                pool_name,
                compartment_id
            )
            return None
        except Exception as e:
            self.logger.warning(
                "Error searching for instance pool '%s': %s",
                pool_name,
                str(e)
            )
            return None

    def _get_instance_pool_instances(
        self, pool_id: str, compartment_id: str, context: CompartmentContext
    ) -> List[oci.core.models.Instance]:
        """Get all instances belonging to an instance pool."""
        compute_mgmt_client = self._get_cm_client(context)
        if not compute_mgmt_client:
            return []

        client = self._get_client(context)
        if not client:
            return []

        try:
            # Get instance pool instances
            response = compute_mgmt_client.list_instance_pool_instances(
                compartment_id=compartment_id,
                instance_pool_id=pool_id
            )

            # Extract instance IDs
            instance_ids = [inst.id for inst in response.data if inst.id]

            if not instance_ids:
                self.logger.debug("No instances found in pool %s", pool_id[-12:])
                return []

            # Fetch full instance details
            compute_client = client.compute_client
            instances = []
            for instance_id in instance_ids:
                try:
                    inst_response = compute_client.get_instance(instance_id)
                    instance = inst_response.data
                    if instance.lifecycle_state in ACTIVE_INSTANCE_STATES:
                        instances.append(instance)
                except Exception as e:
                    self.logger.warning(
                        "Error fetching instance %s: %s",
                        instance_id[-12:],
                        str(e)
                    )

            self.logger.debug(
                "Found %d active instances in pool %s",
                len(instances),
                pool_id[-12:]
            )
            return instances
        except Exception as e:
            self.logger.warning(
                "Error getting instances for pool %s: %s",
                pool_id[-12:] if pool_id else "unknown",
                str(e)
            )
            return []

    @staticmethod
    def _extract_node_pool_image_id(
        node_pool: oci.container_engine.models.NodePool,
    ) -> Optional[str]:
        node_config = getattr(node_pool, "node_config_details", None)
        if node_config:
            source_details = getattr(node_config, "node_source_details", None)
            if source_details:
                image_id = getattr(source_details, "image_id", None)
                if image_id:
                    return image_id
        source_details = getattr(node_pool, "node_source_details", None)
        if source_details:
            return getattr(source_details, "image_id", None)
        return None

    @staticmethod
    def _build_node_source_details(image_id: str) -> Any:
        """Return an OCI-compliant payload for updating node source image."""

        if UpdateNodeSourceViaImageDetails is not None:
            return UpdateNodeSourceViaImageDetails(image_id=image_id)

        if NodeSourceViaImageDetails is not None:
            return NodeSourceViaImageDetails(image_id=image_id)  # type: ignore[call-arg]

        return {
            "model_type": "NODE_SOURCE_VIA_IMAGE_DETAILS",
            "source_type": "IMAGE",
            "image_id": image_id,
        }

    @staticmethod
    def _to_camel_case(value: str) -> str:
        parts = value.split("_")
        if not parts:
            return value
        return parts[0] + "".join(part.title() for part in parts[1:])

    @classmethod
    def _instantiate_model(cls, model_cls: Any, field_name: str, field_value: Any) -> Optional[Any]:
        for key in (field_name, cls._to_camel_case(field_name)):
            try:
                return model_cls(**{key: field_value})
            except TypeError:
                continue
        return None

    @classmethod
    def _build_update_node_pool_details(
        cls, image_id: str, max_surge: str = "4", max_unavailable: str = "0"
    ) -> Any:
        """Build node pool update details with image and node cycling configuration.

        Args:
            image_id: The OCID of the new image
            max_surge: Maximum additional nodes during cycling (default: "4", can be number or percentage like "20%")
            max_unavailable: Maximum unavailable nodes during cycling (default: "0", can be number or percentage)
        """
        # Build node source details with the new image
        node_source_details = cls._build_node_source_details(image_id)

        # Add node cycling configuration to enable automatic cycling
        # This is the KEY parameter that triggers node cycling
        cycling_details = None
        try:
            cycling_details = NodePoolCyclingDetails(
                is_node_cycling_enabled=True,
                maximum_surge=max_surge,
                maximum_unavailable=max_unavailable,
            )
        except (TypeError, AttributeError):
            # Fallback for older SDK versions
            cycling_details = {
                "isNodeCyclingEnabled": True,
                "maximumSurge": max_surge,
                "maximumUnavailable": max_unavailable,
            }

        # Add node eviction settings for graceful draining
        eviction_settings = None
        try:
            eviction_settings = NodeEvictionNodePoolSettings(
                eviction_grace_duration="PT30M",  # 30 minutes grace period for workload migration
                is_force_delete_after_grace_duration=False,
            )
        except (TypeError, AttributeError):
            # Fallback for older SDK versions
            eviction_settings = {
                "evictionGraceDuration": "PT30M",
                "isForceDeleteAfterGraceDuration": False,
            }

        # Build the complete update details
        # IMPORTANT: node_source_details goes directly in UpdateNodePoolDetails,
        # NOT inside node_config_details!
        details = None
        try:
            details = UpdateNodePoolDetails(
                node_source_details=node_source_details,
                node_pool_cycling_details=cycling_details,
                node_eviction_node_pool_settings=eviction_settings,
            )
        except (TypeError, AttributeError):
            # Fallback to dict for older SDK versions
            details = {
                cls._to_camel_case("node_source_details"): node_source_details,
                cls._to_camel_case("node_pool_cycling_details"): cycling_details,
                cls._to_camel_case("node_eviction_node_pool_settings"): eviction_settings,
            }

        return details

    # --- Image metadata helpers -------------------------------------------------

    @staticmethod
    def _safe_get_defined_tag(resource: Any, namespace: str, key: str) -> Optional[str]:
        tags = getattr(resource, "defined_tags", None)
        if not isinstance(tags, dict):
            return None
        ns = tags.get(namespace)
        if not isinstance(ns, dict):
            return None
        value = ns.get(key)
        if isinstance(value, str) and value:
            return value
        return None

    @classmethod
    def _get_image_type(cls, resource: Any) -> Optional[str]:
        for namespace in ("ics_images", "icm_images"):
            image_type = cls._safe_get_defined_tag(resource, namespace, "type")
            if image_type:
                return image_type
        return None

    @classmethod
    def _get_image_release(cls, resource: Any) -> Optional[str]:
        for namespace in ("ics_images", "icm_images"):
            release = cls._safe_get_defined_tag(resource, namespace, "release")
            if release:
                return release
        return None

    @staticmethod
    def _extract_release_hint(identifier: str) -> Optional[str]:
        match = re.search(r"(20\d{6})", identifier)
        if match:
            return match.group(1)
        return None

    @classmethod
    def _find_latest_image_with_same_type(
        cls,
        compute_client: Any,
        compartment_id: str,
        target_type: str,
    ) -> Optional[Any]:
        try:
            images = list_call_get_all_results(
                compute_client.list_images,
                compartment_id,
                sort_by="TIMECREATED",
                sort_order="DESC",
            ).data
        except oci_exceptions.ServiceError as exc:
            logging.getLogger(LOGGER_NAME).warning(
                "Unable to list images for type %s in compartment %s: %s",
                target_type,
                compartment_id,
                exc.message,
            )
            return None

        for image in images:
            image_type = cls._get_image_type(image)
            if not image_type or image_type.lower() != target_type.lower():
                continue
            release = cls._get_image_release(image)
            if release and release.upper() == "LATEST":
                return image
        return None

    @classmethod
    def _find_image_by_type_and_release(
        cls,
        compute_client: Any,
        compartment_id: str,
        target_type: Optional[str],
        target_release: str,
    ) -> Optional[Any]:
        try:
            images = list_call_get_all_results(
                compute_client.list_images,
                compartment_id,
                sort_by="TIMECREATED",
                sort_order="DESC",
            ).data
        except oci_exceptions.ServiceError as exc:
            logging.getLogger(LOGGER_NAME).warning(
                "Unable to list images while searching for release %s in compartment %s: %s",
                target_release,
                compartment_id,
                exc.message,
            )
            return None

        for image in images:
            if target_type:
                image_type = cls._get_image_type(image)
                if not image_type or image_type.lower() != target_type.lower():
                    continue
            release = cls._get_image_release(image)
            if release and release.lower() == target_release.lower():
                return image
        return None

    def _resolve_image_name(
        self, context: CompartmentContext, instance: oci.core.models.Instance
    ) -> Optional[str]:
        """Resolve the display name of the image backing the instance."""
        image_id = getattr(instance, "image_id", None)
        if not image_id and getattr(instance, "source_details", None):
            image_id = getattr(instance.source_details, "image_id", None)
        if not image_id:
            return None

        if image_id in self._image_cache:
            # Keep repeated lookups cheap when several nodes share the same image.
            return self._image_cache[image_id]

        try:
            client = self._get_client(context)
            if not client:
                raise RuntimeError(
                    "No compute client available for region {region}".format(region=context.region)
                )
            response = client.compute_client.get_image(image_id)
        except oci_exceptions.ServiceError as exc:
            self.logger.warning(
                "Unable to resolve image name for %s (%s)", instance.id, exc.message
            )
            self._image_cache[image_id] = None
            return None
        except RuntimeError as exc:
            self.logger.warning(str(exc))
            self._image_cache[image_id] = None
            return None

        image = response.data
        name = image.display_name or image_id
        self._image_cache[image_id] = name
        return name

    def _resolve_target_image_id(
        self,
        context: CompartmentContext,
        compartment_id: Optional[str],
        image_identifier: str,
        current_image_id: Optional[str],
    ) -> Optional[str]:
        """Resolve a target image identifier (name or OCID) to an image OCID."""

        if not image_identifier:
            return None
        if image_identifier.startswith("ocid1.image"):
            return image_identifier

        client = self._get_client(context)
        if not client:
            return None

        compute_client = client.compute_client
        normalized_name = image_identifier.strip()
        normalized_name_ci = normalized_name.lower()

        image_compartment_id: Optional[str] = None
        image_type: Optional[str] = None
        if current_image_id:
            try:
                current_image = compute_client.get_image(current_image_id).data
                image_compartment_id = getattr(current_image, "compartment_id", None)
                image_type = self._get_image_type(current_image)
            except oci_exceptions.ServiceError as exc:
                self.logger.warning(
                    "Unable to fetch current image %s metadata: %s",
                    current_image_id,
                    exc.message,
                )

        search_compartments: List[str] = []
        if image_compartment_id:
            search_compartments.append(image_compartment_id)
        if compartment_id and compartment_id not in search_compartments:
            search_compartments.append(compartment_id)

        for cid in search_compartments:
            try:
                images = list_call_get_all_results(
                    compute_client.list_images,
                    cid,
                    display_name=normalized_name,
                    sort_by="TIMECREATED",
                    sort_order="DESC",
                ).data
            except oci_exceptions.ServiceError as exc:
                self.logger.warning(
                    "Failed to list images named '%s' in compartment %s: %s",
                    normalized_name,
                    cid,
                    exc.message,
                )
                continue

            for image in images:
                display_name = getattr(image, "display_name", None)
                if isinstance(display_name, str) and display_name.strip().lower() == normalized_name_ci:
                    image_id = getattr(image, "id", None)
                    if isinstance(image_id, str):
                        return image_id

        if image_compartment_id and image_type:
            latest_image = self._find_latest_image_with_same_type(
                compute_client,
                image_compartment_id,
                image_type,
            )
            if latest_image:
                candidate_name = (
                    getattr(latest_image, "display_name", "")
                    or getattr(latest_image, "id", "")
                )
                if candidate_name.strip().lower() == normalized_name_ci:
                    image_id = getattr(latest_image, "id", None)
                    if isinstance(image_id, str):
                        return image_id
                else:
                    latest_release = self._get_image_release(latest_image)
                    name_release = self._extract_release_hint(normalized_name)
                    if (
                        latest_release
                        and name_release
                        and latest_release.lower() == name_release.lower()
                    ):
                        image_id = getattr(latest_image, "id", None)
                        if isinstance(image_id, str):
                            return image_id

        if image_compartment_id:
            release_hint = self._extract_release_hint(normalized_name)
            if release_hint:
                candidate = self._find_image_by_type_and_release(
                    compute_client,
                    image_compartment_id,
                    image_type,
                    release_hint,
                )
                if candidate:
                    image_id = getattr(candidate, "id", None)
                    if isinstance(image_id, str):
                        return image_id

        message = (
            "Unable to resolve image ID for identifier '{name}' in compartments {compartments}".format(
                name=image_identifier,
                compartments=", ".join(search_compartments) if search_compartments else "(none)",
            )
        )
        self.logger.error(message)
        self._errors.append(message)
        return None

    def _get_node_pool(
        self, context: CompartmentContext, node_pool_id: str
    ) -> Optional[oci.container_engine.models.NodePool]:
        """Fetch a node pool in the specified context, caching the response."""
        cache_key = (*self._context_key(context), node_pool_id)
        if cache_key not in self._node_pool_cache:
            ce_client = self._get_ce_client(context)
            if not ce_client:
                self._errors.append(
                    f"No Container Engine client available for region {context.region}"
                )
                self._node_pool_cache[cache_key] = None
                return None
            try:
                response = ce_client.get_node_pool(node_pool_id)
            except oci_exceptions.ServiceError as exc:
                self.logger.error(
                    "Failed to fetch node pool %s in %s: %s",
                    node_pool_id,
                    context.region,
                    exc.message,
                )
                self._errors.append(
                    f"Failed to fetch node pool {node_pool_id} in {context.region}: {exc.message}"
                )
                self._node_pool_cache[cache_key] = None
                return None
            self._node_pool_cache[cache_key] = response.data
        return self._node_pool_cache[cache_key]

    def _capture_node_pool_health(
        self, context: CompartmentContext, node_pool_id: str
    ) -> Tuple[Optional[str], Optional[str], List[Tuple[str, str]]]:
        """Return lifecycle state, image, and per-node states for the pool."""
        ce_client = self._get_ce_client(context)
        if not ce_client:
            message = f"No Container Engine client available for region {context.region}"
            self.logger.error(message)
            self._errors.append(message)
            return None, None, []

        try:
            response = ce_client.get_node_pool(node_pool_id)
        except oci_exceptions.ServiceError as exc:
            message = f"Failed to refresh node pool {node_pool_id} health in {context.region}: {exc.message}"
            self.logger.error(message)
            self._errors.append(message)
            return None, None, []

        node_pool = response.data
        lifecycle_state = getattr(node_pool, "lifecycle_state", None)
        image_name = getattr(node_pool, "node_image_name", None)
        node_states: List[Tuple[str, str]] = []

        nodes = getattr(node_pool, "nodes", None) or []
        for node in nodes:
            name = getattr(node, "name", None) or getattr(node, "id", "")
            state = getattr(node, "lifecycle_state", None) or "UNKNOWN"
            node_states.append((name, state))

        return lifecycle_state, image_name, node_states

    # ------------------------------------------------------------------
    # Formatting and display helpers
    # ------------------------------------------------------------------
    def _oci_model_to_dict(self, obj: Any) -> Any:
        """Recursively convert OCI SDK model objects to dictionaries."""
        if obj is None:
            return None
        elif hasattr(obj, 'swagger_types'):
            # OCI SDK model object - convert recursively
            result = {}
            for attr in obj.swagger_types.keys():
                value = getattr(obj, attr, None)
                if value is not None:
                    result[attr] = self._oci_model_to_dict(value)
            return result
        elif isinstance(obj, list):
            return [self._oci_model_to_dict(item) for item in obj]
        elif isinstance(obj, dict):
            return {k: self._oci_model_to_dict(v) for k, v in obj.items()}
        else:
            return obj

    def _format_update_details(self, details: Any) -> str:
        """Format UpdateNodePoolDetails object as JSON string for logging."""
        try:
            detail_dict = self._oci_model_to_dict(details)
            return json.dumps(detail_dict, indent=2, default=str)
        except Exception as exc:
            self.logger.warning("Failed to format update details: %s", exc)
            return str(details)

    def _print_api_request_panel(
        self, node_pool_id: str, target_image_name: str, details: Any
    ) -> None:
        """Print a formatted panel showing the API request details."""
        details_json = self._format_update_details(details)

        panel_content = f"[bold cyan]Node Pool:[/bold cyan] {node_pool_id}\n"
        panel_content += f"[bold cyan]Target Image:[/bold cyan] {target_image_name}\n\n"
        panel_content += "[bold yellow]Update Details (JSON):[/bold yellow]\n"

        self.console.print(Panel(
            panel_content,
            title="[bold magenta]API Request Details[/bold magenta]",
            border_style="cyan"
        ))

        # Print JSON with syntax highlighting
        syntax = Syntax(details_json, "json", theme="monokai", line_numbers=True)
        self.console.print(syntax)

    def _print_work_request_table(
        self,
        work_request_id: str,
        status: str,
        description: str,
        accepted_time: Optional[datetime] = None,
        finished_time: Optional[datetime] = None,
        duration_seconds: Optional[float] = None,
        errors: Optional[List[str]] = None,
    ) -> None:
        """Print a formatted table showing work request status with colors."""
        table = Table(title="Work Request Status", show_header=True, header_style="bold magenta")
        table.add_column("Field", style="cyan", width=20)
        table.add_column("Value", style="white")

        # Color-code status
        if status == "SUCCEEDED":
            status_colored = f"[bold green]{status} ✓[/bold green]"
            id_colored = f"[green]{work_request_id}[/green]"
        elif status in ("FAILED", "CANCELED", "ERROR"):
            status_colored = f"[bold red]{status} ✗[/bold red]"
            id_colored = f"[red]{work_request_id}[/red]"
        else:
            status_colored = f"[bold yellow]{status}[/bold yellow]"
            id_colored = f"[yellow]{work_request_id}[/yellow]"

        table.add_row("Work Request ID", id_colored)
        table.add_row("Description", description)
        table.add_row("Status", status_colored)

        if accepted_time:
            table.add_row("Accepted", accepted_time.strftime("%Y-%m-%d %H:%M:%S %Z"))
        if finished_time:
            table.add_row("Finished", finished_time.strftime("%Y-%m-%d %H:%M:%S %Z"))
        if duration_seconds is not None:
            table.add_row("Duration", f"{duration_seconds:.1f}s")

        if errors:
            error_text = "\n".join(f"• {err}" for err in errors)
            table.add_row("Errors", f"[red]{error_text}[/red]")

        self.console.print(table)

    # ------------------------------------------------------------------
    # Execution helpers
    # ------------------------------------------------------------------
    def _execute(
        self,
        node_pool_plans: Iterable[NodePoolUpdateAction],
        instance_pool_plans: Iterable[InstancePoolUpdateAction]
    ) -> None:
        """Execute the planned image upgrades and recycling operations for both pool types."""

        # Process OKE node pools (existing logic - DO NOT MODIFY)
        for action in node_pool_plans:
            # Refresh before acting so we log the pre-change state alongside every work request.
            node_pool = self._get_node_pool(action.context, action.node_pool_id)
            if not node_pool:
                continue

            current_image_name = getattr(node_pool, "node_image_name", None)
            current_image_id = self._extract_node_pool_image_id(node_pool)
            if current_image_name:
                self.logger.info(
                    "Node pool %s currently using image '%s'",
                    action.node_pool_id,
                    current_image_name,
                )
            summary_compartment = action.nodes[0].compartment_id if action.nodes else None

            summary = NodePoolSummary(
                node_pool_id=action.node_pool_id,
                target_image=action.new_image_name,
                context=action.context,
                compartment_id=summary_compartment,
                original_image_name=current_image_name,
                original_image_id=current_image_id,
            )

            if self.dry_run:
                description = f"Update node pool {action.node_pool_id} with automatic node cycling"
                node_count = len(action.nodes)
                self.logger.info(
                    "[DRY RUN] Would update node pool %s to image '%s' (%d node%s will be cycled automatically)",
                    action.node_pool_id,
                    action.new_image_name,
                    node_count,
                    "s" if node_count != 1 else "",
                )
                summary.update_result = WorkRequestResult(
                    description=description,
                    status="DRY_RUN",
                )
                # Log affected nodes for dry-run visibility
                for node in action.nodes:
                    self.logger.info(
                        "[DRY RUN]   - Node %s (%s) will be cycled by OCI",
                        node.host_name,
                        node.instance_id,
                    )
            else:
                target_image_id = self._resolve_target_image_id(
                    action.context,
                    summary_compartment,
                    action.new_image_name,
                    current_image_id,
                )
                summary.target_image_id = target_image_id
                if not target_image_id:
                    summary.update_result = WorkRequestResult(
                        description=f"Update node pool {action.node_pool_id} with automatic node cycling",
                        status="FAILED",
                        errors=[
                            "Unable to resolve target image identifier"
                            f" '{action.new_image_name}'"
                        ],
                    )
                    self._summaries.append(summary)
                    continue

                node_count = len(action.nodes)
                self.logger.info(
                    "Updating node pool %s with automatic node cycling (%d node%s will be cycled)",
                    action.node_pool_id,
                    node_count,
                    "s" if node_count != 1 else "",
                )

                # Capture configuration that will be sent
                summary.cycling_config = {
                    "is_node_cycling_enabled": True,
                    "maximum_surge": "4",
                    "maximum_unavailable": "0",
                }
                summary.eviction_config = {
                    "eviction_grace_duration": "PT30M",
                    "is_force_delete_after_grace_duration": False,
                }

                # Capture timestamp before sending update
                summary.update_initiated_at = datetime.now(timezone.utc)

                summary.update_result = self._update_node_pool_image(
                    action.context,
                    action.node_pool_id,
                    target_image_id,
                    action.new_image_name,
                )

                # Log that OCI will handle the cycling automatically
                if summary.update_result.status == "SUCCEEDED":
                    self.logger.info(
                        "Node pool %s update initiated successfully. "
                        "OCI will automatically cycle nodes with new image.",
                        action.node_pool_id,
                    )

            post_state, post_image, post_nodes = self._capture_node_pool_health(
                action.context, action.node_pool_id
            )
            # Capture the observed state after the image bump so the report reflects real OCI health.
            summary.post_state = post_state
            summary.post_image_name = post_image
            summary.post_node_states = post_nodes
            self._summaries.append(summary)

        # Process compute instance pools (NEW logic for instance pool cycling)
        for action in instance_pool_plans:
            self.logger.info(
                "Processing instance pool %s with %d instance(s)",
                action.instance_pool_id,
                len(action.instances)
            )

            summary_compartment = action.instances[0].compartment_id if action.instances else None
            summary = InstancePoolSummary(
                instance_pool_id=action.instance_pool_id,
                target_image=action.new_image_name,
                context=action.context,
                compartment_id=summary_compartment,
            )

            if self.dry_run:
                instance_count = len(action.instances)
                self.logger.info(
                    "[DRY RUN] Would update instance pool %s to image '%s' and cycle %d instance%s",
                    action.instance_pool_id,
                    action.new_image_name,
                    instance_count,
                    "s" if instance_count != 1 else "",
                )
                summary.update_result = WorkRequestResult(
                    description=f"Update instance pool {action.instance_pool_id}",
                    status="DRY_RUN",
                )
            else:
                # Get current instance pool and configuration
                cm_client = self._get_cm_client(action.context)
                if not cm_client:
                    summary.update_result = WorkRequestResult(
                        description=f"Update instance pool {action.instance_pool_id}",
                        status="FAILED",
                        errors=["No Compute Management client available"],
                    )
                    self._instance_pool_summaries.append(summary)
                    continue

                # Capture timestamp before initiating update
                summary.update_initiated_at = datetime.now(timezone.utc)

                # Cycle the instance pool
                cycle_result = self._cycle_instance_pool(
                    action.context,
                    action.instance_pool_id,
                    action.new_image_name,
                    action.instances,
                    summary,  # Pass summary to capture config_created_at
                )
                summary.update_result = cycle_result.get("pool_update")
                summary.instance_results = cycle_result.get("instance_results", [])
                summary.new_instance_config_id = cycle_result.get("new_config_id")
                summary.detached_count = cycle_result.get("detached_count", 0)

            # Capture post-state
            try:
                cm_client = self._get_cm_client(action.context)
                if cm_client:
                    pool = cm_client.get_instance_pool(action.instance_pool_id).data
                    summary.post_state = pool.lifecycle_state
                    summary.post_instance_count = pool.size
            except Exception as exc:
                self.logger.warning("Failed to capture instance pool post-state: %s", exc)

            self._instance_pool_summaries.append(summary)

    def _cycle_instance_pool(
        self,
        context: CompartmentContext,
        instance_pool_id: str,
        new_image_name: str,
        instances: List[InstanceImageUpdatePlan],
        summary: InstancePoolSummary,
    ) -> Dict[str, Any]:
        """Cycle an instance pool by updating config and detaching old instances.

        Returns dict with:
            - pool_update: WorkRequestResult for pool update
            - instance_results: List of detach results
            - new_config_id: New instance configuration OCID
            - detached_count: Number of instances detached
        """
        result: Dict[str, Any] = {
            "pool_update": None,
            "instance_results": [],
            "new_config_id": None,
            "detached_count": 0,
        }

        cm_client = self._get_cm_client(context)
        if not cm_client:
            result["pool_update"] = WorkRequestResult(
                description=f"Cycle instance pool {instance_pool_id}",
                status="FAILED",
                errors=["No Compute Management client available"],
            )
            return result

        try:
            # Get current instance pool
            pool = cm_client.get_instance_pool(instance_pool_id).data
            current_config_id = pool.instance_configuration_id

            # Get current instance configuration
            current_config = cm_client.get_instance_configuration(current_config_id).data

            # Resolve target image ID - extract current image from instance configuration
            current_image_id = None
            if hasattr(current_config, 'instance_details'):
                instance_details = current_config.instance_details
                # Try different paths where image_id might be stored
                if hasattr(instance_details, 'launch_details'):
                    launch_details = instance_details.launch_details
                    current_image_id = getattr(launch_details, 'image_id', None)
                elif hasattr(instance_details, 'source_details'):
                    source_details = instance_details.source_details
                    current_image_id = getattr(source_details, 'image_id', None)

            # Log what we found
            if current_image_id:
                self.logger.debug(
                    "Extracted current image ID from instance config: %s",
                    current_image_id[-12:] if current_image_id else "None"
                )
            else:
                self.logger.warning(
                    "Could not extract current image ID from instance configuration %s",
                    current_config_id[-12:]
                )
                # Try to get image ID from one of the actual instances
                if instances:
                    client = self._get_client(context)
                    if client:
                        try:
                            inst_response = client.compute_client.get_instance(instances[0].instance_id)
                            instance = inst_response.data
                            source_details = getattr(instance, 'source_details', None)
                            if source_details:
                                current_image_id = getattr(source_details, 'image_id', None)
                                if current_image_id:
                                    self.logger.info(
                                        "Extracted current image ID from instance %s: %s",
                                        instances[0].instance_id[-12:],
                                        current_image_id[-12:]
                                    )
                        except Exception as e:
                            self.logger.debug("Could not get image from instance: %s", str(e))

            target_image_id = self._resolve_target_image_id(
                context,
                instances[0].compartment_id if instances else None,
                new_image_name,
                current_image_id,
            )

            if not target_image_id:
                result["pool_update"] = WorkRequestResult(
                    description=f"Cycle instance pool {instance_pool_id}",
                    status="FAILED",
                    errors=[f"Unable to resolve image '{new_image_name}'"],
                )
                return result

            # Create new instance configuration with updated image
            new_config_id = self._create_instance_configuration(
                context, current_config, target_image_id, new_image_name, summary
            )

            if not new_config_id:
                result["pool_update"] = WorkRequestResult(
                    description=f"Cycle instance pool {instance_pool_id}",
                    status="FAILED",
                    errors=["Failed to create new instance configuration"],
                )
                return result

            result["new_config_id"] = new_config_id

            # Update instance pool to use new configuration
            update_result = self._update_instance_pool_config(
                context, instance_pool_id, new_config_id
            )
            result["pool_update"] = update_result

            if update_result.status != "SUCCEEDED":
                return result

            # Detach old instances gradually (pool will auto-create new ones)
            max_surge = 4  # Match OKE node pool settings
            for i, instance_plan in enumerate(instances):
                if i >= max_surge:
                    # Wait for replacements before detaching more
                    self.logger.info(
                        "Detached %d instances, waiting for pool to create replacements...",
                        i
                    )
                    time.sleep(30)  # Give pool time to create new instances

                detach_result = self._detach_instance_from_pool(
                    context, instance_pool_id, instance_plan
                )
                result["instance_results"].append(detach_result)

                if detach_result.status == "SUCCEEDED":
                    result["detached_count"] += 1

        except oci_exceptions.ServiceError as exc:
            result["pool_update"] = WorkRequestResult(
                description=f"Cycle instance pool {instance_pool_id}",
                status="FAILED",
                errors=[exc.message],
            )

        return result

    def _create_instance_configuration(
        self,
        context: CompartmentContext,
        current_config: Any,
        new_image_id: str,
        new_image_name: str,
        summary: InstancePoolSummary,
    ) -> Optional[str]:
        """Create a new instance configuration based on current config with updated image."""
        cm_client = self._get_cm_client(context)
        if not cm_client:
            return None

        try:
            # Build new configuration based on current one
            from oci.core.models import CreateInstanceConfigurationDetails, ComputeInstanceDetails

            # Clone the instance details and update the image
            instance_details = current_config.instance_details
            if hasattr(instance_details, 'launch_details'):
                launch_details = instance_details.launch_details
                # Update image ID
                launch_details.image_id = new_image_id

            new_config_name = f"{current_config.display_name or 'config'}-{new_image_name}-{int(time.time())}"

            create_details = CreateInstanceConfigurationDetails(
                compartment_id=current_config.compartment_id,
                display_name=new_config_name[:255],  # OCI limit
                instance_details=instance_details,
                freeform_tags=current_config.freeform_tags,
                defined_tags=current_config.defined_tags,
            )

            response = cm_client.create_instance_configuration(create_details)
            new_config_id = response.data.id

            # Capture when the new configuration was created
            summary.config_created_at = datetime.now(timezone.utc)

            self.logger.info(
                "Created new instance configuration %s with image %s",
                new_config_id,
                new_image_name
            )
            return new_config_id

        except Exception as exc:
            self.logger.error("Failed to create instance configuration: %s", exc)
            self._errors.append(f"Failed to create instance configuration: {exc}")
            return None

    def _update_instance_pool_config(
        self,
        context: CompartmentContext,
        instance_pool_id: str,
        new_config_id: str,
    ) -> WorkRequestResult:
        """Update instance pool to use new instance configuration."""
        cm_client = self._get_cm_client(context)
        if not cm_client:
            return WorkRequestResult(
                description=f"Update instance pool {instance_pool_id}",
                status="FAILED",
                errors=["No Compute Management client available"],
            )

        try:
            from oci.core.models import UpdateInstancePoolDetails

            update_details = UpdateInstancePoolDetails(
                instance_configuration_id=new_config_id
            )

            self.logger.info(
                "Updating instance pool %s to use configuration %s",
                instance_pool_id,
                new_config_id
            )

            response = cm_client.update_instance_pool(instance_pool_id, update_details)

            return WorkRequestResult(
                description=f"Update instance pool {instance_pool_id}",
                status="SUCCEEDED",
            )

        except oci_exceptions.ServiceError as exc:
            self.logger.error("Failed to update instance pool: %s", exc.message)
            return WorkRequestResult(
                description=f"Update instance pool {instance_pool_id}",
                status="FAILED",
                errors=[exc.message],
            )

    def _detach_instance_from_pool(
        self,
        context: CompartmentContext,
        instance_pool_id: str,
        instance_plan: InstanceImageUpdatePlan,
    ) -> WorkRequestResult:
        """Detach an instance from the pool (pool will create replacement)."""
        cm_client = self._get_cm_client(context)
        if not cm_client:
            return WorkRequestResult(
                description=f"Detach instance {instance_plan.host_name}",
                status="FAILED",
                errors=["No Compute Management client available"],
            )

        try:
            from oci.core.models import DetachInstancePoolInstanceDetails

            self.logger.info(
                "Detaching instance %s (%s) from pool %s",
                instance_plan.host_name,
                instance_plan.instance_id,
                instance_pool_id
            )

            detach_details = DetachInstancePoolInstanceDetails(
                instance_id=instance_plan.instance_id,
                is_decrement_size=False,  # Pool will create replacement
                is_auto_terminate=True,  # Terminate the detached instance
            )

            response = cm_client.detach_instance_pool_instance(
                instance_pool_id=instance_pool_id,
                detach_instance_pool_instance_details=detach_details
            )

            return WorkRequestResult(
                description=f"Detach instance {instance_plan.host_name}",
                status="SUCCEEDED",
            )

        except oci_exceptions.ServiceError as exc:
            self.logger.error(
                "Failed to detach instance %s: %s",
                instance_plan.host_name,
                exc.message
            )
            return WorkRequestResult(
                description=f"Detach instance {instance_plan.host_name}",
                status="FAILED",
                errors=[exc.message],
            )

    def _update_node_pool_image(
        self,
        context: CompartmentContext,
        node_pool_id: str,
        target_image_id: str,
        target_image_name: str,
    ) -> WorkRequestResult:
        """Update the node pool with new image and automatic node cycling configuration.

        This triggers OCI's automatic node cycling which will:
        1. Create new nodes with the updated image
        2. Drain and move workloads to new nodes
        3. Terminate old nodes

        The process is controlled by eviction settings configured in the update.
        """
        self.logger.info(
            "Updating node pool %s with new node image '%s' (automatic cycling enabled)",
            node_pool_id,
            target_image_name,
        )

        # Build update details
        details = self._build_update_node_pool_details(target_image_id)

        # Print detailed API request information with colors
        self.console.print("\n")
        self._print_api_request_panel(node_pool_id, target_image_name, details)
        self.console.print("\n")

        ce_client = self._get_ce_client(context)
        if not ce_client:
            message = f"No Container Engine client available for region {context.region}"
            self.logger.error(message)
            self._errors.append(message)
            return WorkRequestResult(
                description=f"Update node pool {node_pool_id}",
                status="FAILED",
                errors=[message],
            )

        # Highlight the API call execution
        self.console.print(
            f"[bold yellow]>>> Executing API Call:[/bold yellow] "
            f"[bold white]ce_client.update_node_pool[/bold white]"
            f"([cyan]{node_pool_id}[/cyan], details)",
            style="on blue"
        )
        self.console.print("\n")

        try:
            response = ce_client.update_node_pool(node_pool_id, details)
        except oci_exceptions.ServiceError as exc:
            self.logger.error("Failed to update node pool %s: %s", node_pool_id, exc.message)
            self._errors.append(f"Failed to update node pool {node_pool_id}: {exc.message}")
            self.console.print(
                f"[bold red]✗ API call failed: {exc.message}[/bold red]\n"
            )
            return WorkRequestResult(
                description=f"Update node pool {node_pool_id}",
                status="FAILED",
                errors=[exc.message],
            )

        self.console.print("[bold green]✓ API call succeeded[/bold green]\n")

        work_request_id = response.headers.get("opc-work-request-id")
        if work_request_id:
            result = self._wait_for_work_request(
                context, work_request_id, f"Update node pool {node_pool_id}"
            )

            # Print work request result in a colored table
            self._print_work_request_table(
                work_request_id=work_request_id,
                status=result.status,
                description=result.description,
                accepted_time=result.accepted_time,
                finished_time=result.finished_time,
                duration_seconds=result.duration_seconds,
                errors=result.errors if result.errors else None,
            )
            self.console.print("\n")

            if result.status != "SUCCEEDED":
                self._errors.append(
                    f"Node pool update for {node_pool_id} ended with status {result.status}"
                )
            return result

        message = f"Update node pool {node_pool_id} did not return a work request ID"
        self.logger.warning(message)
        self._errors.append(message)
        self.console.print(f"[bold yellow]⚠ {message}[/bold yellow]\n")
        return WorkRequestResult(
            description=f"Update node pool {node_pool_id}",
            status="UNKNOWN",
            errors=[message],
        )


    def _wait_for_work_request(
        self, context: CompartmentContext, work_request_id: str, description: str
    ) -> WorkRequestResult:
        """Poll the Container Engine work request until it completes."""
        self.logger.info("Waiting on work request %s for %s", work_request_id, description)
        ce_client = self._get_ce_client(context)
        if not ce_client:
            message = f"No Container Engine client available for region {context.region}"
            self.logger.error(message)
            self._errors.append(message)
            return WorkRequestResult(
                description=description,
                status="FAILED",
                work_request_id=work_request_id,
                errors=[message],
            )
        # Poll until the regional work request settles so operators have clear timing in the report.
        while True:
            try:
                response = ce_client.get_work_request(work_request_id)
            except oci_exceptions.ServiceError as exc:
                self.logger.error(
                    "Error querying work request %s: %s", work_request_id, exc.message
                )
                error_message = f"Work request {work_request_id} for {description} failed to query: {exc.message}"
                self._errors.append(error_message)
                return WorkRequestResult(
                    description=description,
                    status="ERROR",
                    work_request_id=work_request_id,
                    errors=[exc.message],
                )

            work_request = response.data
            status = getattr(work_request, "status", "UNKNOWN")
            operation = getattr(work_request, "operation_type", "UNKNOWN")
            percent = getattr(work_request, "percent_complete", None)
            self.logger.info(
                "Work request %s status=%s operation=%s percent=%s",
                work_request_id,
                status,
                operation,
                percent,
            )

            if status in TERMINAL_WORK_REQUEST_STATES:
                accepted = getattr(work_request, "time_accepted", None)
                finished = getattr(work_request, "time_finished", None)
                duration = None
                if accepted and finished:
                    duration = (finished - accepted).total_seconds()

                if status == "SUCCEEDED":
                    self.logger.info(
                        "Work request %s for %s completed in %.1f seconds",
                        work_request_id,
                        description,
                        duration if duration is not None else 0.0,
                    )
                    return WorkRequestResult(
                        description=description,
                        status=status,
                        work_request_id=work_request_id,
                        accepted_time=accepted,
                        finished_time=finished,
                        duration_seconds=duration,
                    )

                self.logger.error(
                    "Work request %s for %s ended with status %s",
                    work_request_id,
                    description,
                    status,
                )
                errors = self._collect_work_request_errors(context, work_request_id)
                for error_message in errors:
                    self._errors.append(
                        f"Work request {work_request_id} ({description}) error: {error_message}"
                    )
                return WorkRequestResult(
                    description=description,
                    status=status,
                    work_request_id=work_request_id,
                    accepted_time=accepted,
                    finished_time=finished,
                    duration_seconds=duration,
                    errors=errors,
                )

            time.sleep(self.poll_seconds)

    def _collect_work_request_errors(
        self, context: CompartmentContext, work_request_id: str
    ) -> List[str]:
        """Collect any error messages attached to a work request."""
        errors: List[str] = []
        ce_client = self._get_ce_client(context)
        if not ce_client:
            message = f"No Container Engine client available for region {context.region}"
            self.logger.error(message)
            self._errors.append(message)
            return errors
        try:
            response = list_call_get_all_results(
                ce_client.list_work_request_errors,
                work_request_id,
            )
        except oci_exceptions.ServiceError as exc:
            self.logger.error(
                "Failed to fetch errors for work request %s in %s: %s",
                work_request_id,
                context.region,
                exc.message,
            )
            return errors

        for item in response.data:
            message = getattr(item, "message", None)
            timestamp = getattr(item, "timestamp", None)
            formatted = f"{timestamp}: {message}" if timestamp else (message or "Unknown error")
            errors.append(formatted)
            self.logger.error("Work request %s error: %s", work_request_id, formatted)
        return errors

    def _open_report_in_browser(self) -> None:
        if not self._report_path:
            return

        report_path = self._report_path.resolve()
        try:
            report_url = report_path.as_uri()
        except ValueError:
            report_url = str(report_path)

        try:
            opened = webbrowser.open_new_tab(report_url)
        except Exception as exc:  # pragma: no cover - best effort for local operator convenience
            self.logger.debug("Unable to open report in browser: %s", exc)
            return

        if opened:
            self.logger.info("Opened report in default browser: %s", report_path)

    def _generate_report(self) -> None:
        "Emit an HTML report summarizing the node pool image bump operation."
        if not self._report_path:
            return

        generated_at_dt = datetime.now().astimezone()
        generated_at_display = generated_at_dt.strftime("%Y-%m-%d %H:%M %Z")
        used_regions = sorted({context[2] for context in self._used_contexts})
        region_value = ", ".join(used_regions) if used_regions else "unknown"
        operator_name = getpass.getuser()

        def html_escape(value: Optional[str]) -> str:
            if value is None:
                return ""
            return (
                str(value)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )

        def format_datetime_local(value: Optional[datetime]) -> str:
            if value is None:
                return "—"
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone().strftime("%Y-%m-%d %H:%M %Z")

        html: List[str] = []
        html.append("<!DOCTYPE html>")
        html.append('<html lang="en">')
        html.append("<head>")
        html.append('<meta charset="utf-8"/>')
        html.append(
            f"<title>OKE Node Pool Image Bump Report - {html_escape(self._timestamp_label or generated_at_display)}</title>"
        )
        html.append(
            "<style>"
            "body{font-family:Arial,Helvetica,sans-serif;background:#f7f7f9;color:#1d1d1f;margin:24px;}"
            "h1{color:#0b5394;}"
            "section{margin-bottom:32px;background:#fff;padding:20px;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.1);}"
            "table{width:100%;border-collapse:collapse;margin-top:12px;font-size:13px;}"
            "th,td{padding:8px 12px;border:1px solid #d9d9e0;text-align:left;font-size:13px;}"
            "th{background:#0b5394;color:#fff;font-size:12px;}"
            "tr:nth-child(even){background:#f2f5f9;}"
            ".status-SUCCEEDED{color:#0b8a00;font-weight:600;}"
            ".status-FAILED{color:#d4351c;font-weight:600;}"
            ".status-DRY_RUN{color:#946200;font-weight:600;}"
            ".status-UNKNOWN{color:#6c757d;font-weight:600;}"
            "code{background:#f0f0f5;padding:2px 4px;border-radius:4px;font-size:11px;font-family:monospace;}"
            "small{font-size:11px;color:#666;}"
            ".nodes-table th{background:#2f5496;}"
            ".skipped{background:#fffbe6;}"
            "details{cursor:pointer;}"
            "details summary{padding:4px 8px;background:#e8f0fe;border-radius:4px;user-select:none;font-weight:500;}"
            "details summary:hover{background:#d2e3fc;}"
            "details[open] summary{background:#d2e3fc;border-bottom:1px solid #ccc;border-radius:4px 4px 0 0;}"
            "details div{font-size:12px;line-height:1.6;}"
            "</style>"
        )
        html.append("</head>")
        html.append("<body>")
        html.append("<h1>OKE Node Pool Image Bump Report</h1>")

        html.append("<section>")
        html.append("<h2>Run Summary</h2>")
        html.append("<ul>")
        html.append(f"<li><strong>Operator:</strong> {html_escape(operator_name)}</li>")
        html.append(f"<li><strong>Generated:</strong> {html_escape(generated_at_display)}</li>")
        if self._timestamp_label:
            html.append(
                f"<li><strong>Run ID:</strong> {html_escape(self._timestamp_label)}</li>"
            )
        html.append(f"<li><strong>CSV Source:</strong> {html_escape(str(self.csv_path))}</li>")
        html.append(f"<li><strong>Meta Source:</strong> {html_escape(str(self.meta_file))}</li>")
        config_display = self.config_file if self.config_file else "~/.oci/config (default)"
        html.append(
            f"<li><strong>Config File:</strong> {html_escape(str(config_display))}</li>"
        )
        html.append(
            f"<li><strong>Regions Evaluated:</strong> {html_escape(region_value)}</li>"
        )
        html.append(
            f"<li><strong>Dry Run:</strong> {'Yes' if self.dry_run else 'No'}</li>"
        )
        html.append(
            f"<li><strong>Rows Processed:</strong> {self._total_rows} (resolved {self._resolved_rows}, skipped {len(self._missing_hosts)})</li>"
        )
        if self._log_path:
            html.append(
                f"<li><strong>Log File:</strong> {html_escape(str(self._log_path))}</li>"
            )
        html.append("</ul>")
        html.append("</section>")

        html.append("<section>")
        html.append("<h2>OKE Node Pool Operations</h2>")
        html.append(
            '<table class="summary-table"><thead><tr>'
            '<th>Node Pool</th><th>Compartment</th><th>Project</th><th>Environment</th>'
            '<th>Region</th><th>Image (Before)</th><th>Image (After)</th>'
            '<th>Update Initiated</th><th>Work Request ID</th><th>Status</th>'
            '<th>Duration (s)</th><th>Healthy/Total</th><th>Details</th>'
            '</tr></thead><tbody>'
        )

        if not self._summaries:
            html.append('<tr><td colspan="13">No node pools were processed.</td></tr>')
        else:
            for idx, summary in enumerate(self._summaries):
                update_result = summary.update_result
                status = update_result.status if update_result else "N/A"
                status_class = f"status-{status}" if update_result else ""
                duration = (
                    f"{update_result.duration_seconds:.1f}"
                    if update_result and update_result.duration_seconds is not None
                    else "—"
                )

                # Timestamp when update was initiated
                initiated_at = format_datetime_local(summary.update_initiated_at) if summary.update_initiated_at else "—"

                # Work request ID with colored status
                work_request_id = update_result.work_request_id if update_result else None
                if work_request_id:
                    wr_short = work_request_id.split(".")[-1][:12] if "." in work_request_id else work_request_id[:12]
                    work_request_html = f'<code class="{status_class}" title="{html_escape(work_request_id)}">{html_escape(wr_short)}...</code>'
                else:
                    work_request_html = "—"

                post_state = summary.post_state or "Unknown"
                healthy = sum(
                    1
                    for _, state in summary.post_node_states
                    if state and state.upper() in {"ACTIVE", "RUNNING", "HEALTHY"}
                )
                total = len(summary.post_node_states)
                healthy_display = f"{healthy}/{total}" if total else "0/0"
                before_html = (
                    f"{html_escape(summary.original_image_name) or 'Unknown'}"
                    f"<br/><small><code>{html_escape(summary.original_image_id[-16:] if summary.original_image_id else '—')}</code></small>"
                )
                after_html = (
                    f"{html_escape(summary.target_image) or 'Unknown'}"
                    f"<br/><small><code>{html_escape(summary.target_image_id[-16:] if summary.target_image_id else '—')}</code></small>"
                )

                # Build configuration details for expandable section
                config_details = []
                if summary.cycling_config:
                    config_details.append("<strong>Node Cycling Config:</strong>")
                    for key, val in summary.cycling_config.items():
                        config_details.append(f"&nbsp;&nbsp;• {key}: <code>{html_escape(str(val))}</code>")
                if summary.eviction_config:
                    config_details.append("<strong>Eviction Settings:</strong>")
                    for key, val in summary.eviction_config.items():
                        config_details.append(f"&nbsp;&nbsp;• {key}: <code>{html_escape(str(val))}</code>")

                details_html = f'<details><summary>Show Config</summary><div style="padding:8px;background:#f5f5f5;margin-top:4px;">{"<br/>".join(config_details) if config_details else "No config details"}</div></details>'

                html.append("<tr>")
                html.append(f"<td><code>{html_escape(summary.node_pool_id)}</code></td>")
                html.append(f"<td>{html_escape(summary.compartment_id) or 'Unknown'}</td>")
                html.append(f"<td>{html_escape(summary.context.project)}</td>")
                html.append(f"<td>{html_escape(summary.context.stage)}</td>")
                html.append(f"<td>{html_escape(summary.context.region)}</td>")
                html.append(f"<td>{before_html}</td>")
                html.append(f"<td>{after_html}</td>")
                html.append(f"<td>{initiated_at}</td>")
                html.append(f"<td>{work_request_html}</td>")
                html.append(f"<td class='{status_class}'>{html_escape(status)}</td>")
                html.append(f"<td>{duration}</td>")
                html.append(f"<td>{healthy_display}</td>")
                html.append(f"<td>{details_html}</td>")
                html.append("</tr>")
        html.append("</tbody></table>")
        html.append("</section>")

        # Instance Pool section
        if self._instance_pool_summaries:
            html.append("<section>")
            html.append("<h2>Instance Pool Operations</h2>")
            html.append(
                "<table><thead><tr>"
                "<th>Instance Pool ID</th>"
                "<th>Compartment</th>"
                "<th>Image (Before)</th>"
                "<th>Image (After)</th>"
                "<th>Update Initiated</th>"
                "<th>Config Created</th>"
                "<th>New Config ID</th>"
                "<th>Status</th>"
                "<th>Instances Detached</th>"
                "<th>Post State</th>"
                "<th>Post Count</th>"
                "<th>Details</th>"
                "</tr></thead><tbody>"
            )

            for summary in self._instance_pool_summaries:
                status = summary.update_result.status if summary.update_result else "UNKNOWN"
                status_class = f"status-{status}"

                # Timestamps
                initiated_at = format_datetime_local(summary.update_initiated_at) if summary.update_initiated_at else "—"
                config_created_at = format_datetime_local(summary.config_created_at) if summary.config_created_at else "—"

                # New config ID (shortened)
                new_config_short = ""
                if summary.new_instance_config_id:
                    parts = summary.new_instance_config_id.split(".")
                    new_config_short = f"...{parts[-1][:12]}" if parts else summary.new_instance_config_id[:15]

                # Image details
                before_html = (
                    f"{html_escape(summary.original_image_name) or 'Unknown'}"
                    f"<br/><small><code>{html_escape(summary.original_image_id[-16:] if summary.original_image_id else '—')}</code></small>"
                )
                after_html = (
                    f"{html_escape(summary.target_image) or 'Unknown'}"
                    f"<br/><small><code>{html_escape(summary.target_image_id[-16:] if summary.target_image_id else '—')}</code></small>"
                )

                # Configuration details
                config_details = []
                config_details.append(f"<strong>Original Config ID:</strong><br/><code>{html_escape(summary.original_instance_config_id or 'N/A')}</code>")
                config_details.append(f"<strong>New Config ID:</strong><br/><code>{html_escape(summary.new_instance_config_id or 'N/A')}</code>")
                config_details.append(f"<strong>Max Surge:</strong> 4 instances")
                config_details.append(f"<strong>is_decrement_size:</strong> False (maintains capacity)")
                config_details.append(f"<strong>is_auto_terminate:</strong> True (auto-cleanup)")

                details_html = f'<details><summary>Show Config</summary><div style="padding:8px;background:#f5f5f5;margin-top:4px;">{"<br/>".join(config_details)}</div></details>'

                html.append("<tr>")
                html.append(f"<td><code>{html_escape(summary.instance_pool_id)}</code></td>")
                html.append(f"<td>{html_escape(summary.compartment_id) or 'Unknown'}</td>")
                html.append(f"<td>{before_html}</td>")
                html.append(f"<td>{after_html}</td>")
                html.append(f"<td>{initiated_at}</td>")
                html.append(f"<td>{config_created_at}</td>")
                html.append(f"<td><code title='{html_escape(summary.new_instance_config_id or '')}'>{html_escape(new_config_short) or '—'}</code></td>")
                html.append(f"<td class='{status_class}'>{html_escape(status)}</td>")
                html.append(f"<td>{summary.detached_count} / {len(summary.instance_results)}</td>")
                html.append(f"<td>{html_escape(summary.post_state or '—')}</td>")
                html.append(f"<td>{summary.post_instance_count}</td>")
                html.append(f"<td>{details_html}</td>")
                html.append("</tr>")

            html.append("</tbody></table>")
            html.append("</section>")

        if self._missing_hosts:
            html.append('<section class="skipped">')
            html.append("<h2>Skipped Hosts</h2>")
            html.append(
                "<table><thead><tr><th>Host</th><th>Compartment</th><th>Reason</th></tr></thead><tbody>"
            )
            for host, compartment, reason in self._missing_hosts:
                html.append(
                    f"<tr><td><code>{html_escape(host)}</code></td><td><code>{html_escape(compartment)}</code></td><td>{html_escape(reason)}</td></tr>"
                )
            html.append("</tbody></table>")
            html.append("</section>")

        html.append("</body></html>")

        self._report_path.parent.mkdir(parents=True, exist_ok=True)
        with self._report_path.open("w", encoding="utf-8") as handle:
            handle.write("\n".join(html))

        self.logger.info("Operation report written to %s", self._report_path)

        self._open_report_in_browser()

        self.logger.info(
            "Image bump summary: %d total row(s), %d resolved, %d skipped",
            self._total_rows,
            self._resolved_rows,
            len(self._missing_hosts),
        )


# Backwards compatibility for legacy imports.
NodePoolRecycler = NodePoolImageUpdater


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bump OKE node pool images based on CSV input.")
    parser.add_argument(
        "--csv-path",
        required=True,
        type=Path,
        help="Path to the CSV file containing host metadata.",
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        help="Optional path to the OCI config file (defaults to ~/.oci/config).",
    )
    parser.add_argument(
        "--meta-file",
        type=Path,
        help="Optional path to meta.yaml (defaults to tools/meta.yaml).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan actions without invoking OCI operations.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=DEFAULT_POLL_SECONDS,
        help="Polling interval in seconds while waiting on work requests (default: %(default)s)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging for the image bump workflow.",
    )
    return parser.parse_args(argv)


def determine_default_log_dir(log_dir_arg: Optional[Path] = None) -> Path:
    if log_dir_arg:
        return log_dir_arg
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "logs"


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    csv_path = args.csv_path.expanduser().resolve()
    config_file = args.config_file.expanduser().resolve() if args.config_file else None
    meta_file = args.meta_file.expanduser().resolve() if args.meta_file else None

    updater = NodePoolImageUpdater(
        csv_path=csv_path,
        config_file=config_file,
        dry_run=args.dry_run,
        poll_seconds=args.poll_seconds,
        meta_file=meta_file,
        verbose=args.verbose,
    )
    return updater.run()


if __name__ == "__main__":
    sys.exit(main())
