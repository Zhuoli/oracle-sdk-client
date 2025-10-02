"""OCI OKE node pool recycling utility.

Reads a CSV file describing compute hosts slated for operating system patching,
identifies their backing node pools using the same meta.yaml mapping as
``ssh_sync``, and performs an automated recycle with logging suitable for
production change records.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import oci
from oci import exceptions as oci_exceptions
from oci.container_engine import ContainerEngineClient
from oci.container_engine.models import UpdateNodePoolDetails
from oci.pagination import list_call_get_all_results
import yaml

from oci_client.client import OCIClient
from oci_client.utils.session import create_oci_client, setup_session_token

LOGGER_NAME = "oci_node_pool_recycler"
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
class NodeRecyclePlan:
    host_name: str
    compartment_id: str
    instance_id: str
    node_pool_id: str
    current_image: str
    resolved_image_name: Optional[str]
    new_image_name: str
    context: "CompartmentContext"


@dataclass
class NodePoolAction:
    node_pool_id: str
    new_image_name: str
    nodes: List[NodeRecyclePlan]
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
    update_result: Optional[WorkRequestResult] = None
    node_results: List[WorkRequestResult] = field(default_factory=list)
    post_state: Optional[str] = None
    post_image_name: Optional[str] = None
    post_node_states: List[Tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class CompartmentContext:
    project: str
    stage: str
    region: str


class NodePoolRecycler:
    def __init__(
        self,
        csv_path: Path,
        config_file: Optional[Path],
        dry_run: bool,
        poll_seconds: int = DEFAULT_POLL_SECONDS,
        log_dir: Optional[Path] = None,
        meta_file: Optional[Path] = None,
    ) -> None:
        self.csv_path = csv_path
        self.config_file = config_file
        self.dry_run = dry_run
        self.poll_seconds = poll_seconds
        self.logger = logging.getLogger(LOGGER_NAME)
        self.logger.setLevel(logging.INFO)

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
        self._timestamp_label: Optional[str] = None
        self._log_path: Optional[Path] = None
        self._report_path: Optional[Path] = None
        # Reuse ssh_sync session helpers so production auth flows remain consistent.
        self._session_clients: Dict[Tuple[str, str, str], "OCIClient"] = {}
        self._ce_clients: Dict[Tuple[str, str, str], ContainerEngineClient] = {}
        self._used_contexts: Set[Tuple[str, str, str]] = set()

        self._configure_logging()
        self._compartment_lookup: Dict[str, CompartmentContext] = self._load_compartment_lookup()
        self._total_rows: int = 0
        self._resolved_rows: int = 0
        self._missing_hosts: List[Tuple[str, str]] = []

    # ------------------------------------------------------------------
    # Public execution entrypoint
    # ------------------------------------------------------------------
    def run(self) -> int:
        """Main entry point for the recycler workflow."""
        instructions = self._load_instructions()
        if not instructions:
            self.logger.error("No actionable rows found in %s", self.csv_path)
            return 1

        plans = self._build_plans(instructions)
        if not plans:
            self.logger.error("Unable to resolve any node pools from provided CSV")
            return 1

        # End-to-end execution: act, wait for OCI to settle, then emit human-readable artifacts.
        self._execute(plans)
        self._generate_report()

        if self._errors:
            self.logger.error("Encountered %d issues during processing", len(self._errors))
            for issue in self._errors:
                self.logger.error(issue)
            return 1

        self.logger.info("All requested node pool recycle operations completed successfully")
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
        log_path = self.log_dir / f"node_pool_recycle_{timestamp}.log"
        report_path = self.log_dir / f"node_pool_recycle_{timestamp}.md"
        self._timestamp_label = timestamp
        self._log_path = log_path
        self._report_path = report_path

        # Share a single timestamped set of artifacts (log + markdown report) for every execution.
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(logging.INFO)
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

            if not (host and compartment and new_image):
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

    def _build_plans(self, instructions: Iterable[CsvInstruction]) -> List[NodePoolAction]:
        """Group CSV instructions by node pool after resolving their compartment context."""
        plans: Dict[Tuple[str, str, str, str], NodePoolAction] = {}
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
            if not instance:
                self._missing_hosts.append((instruction.host_name, instruction.compartment_id))
                self.logger.warning(
                    "Skipping host '%s' (compartment %s) because no active compute instance was found",
                    instruction.host_name,
                    instruction.compartment_id,
                )
                continue

            node_pool_id = self._extract_node_pool_id(instance)
            if not node_pool_id:
                self._errors.append(
                    f"Instance '{instruction.host_name}' missing OKE node pool metadata"
                )
                continue

            resolved_image = self._resolve_image_name(context, instance)
            if (
                instruction.current_image
                and resolved_image
                and instruction.current_image.strip().lower() != resolved_image.strip().lower()
            ):
                # Flag drift early so the runbook reflects what is actually running before recycle.
                self.logger.warning(
                    "Image mismatch for host %s: CSV=%s actual=%s",
                    instruction.host_name,
                    instruction.current_image,
                    resolved_image,
                )

            plan_entry = NodeRecyclePlan(
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
                plans[key] = NodePoolAction(
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
                    continue
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
                    continue
                action.nodes.append(plan_entry)

            self._resolved_rows += 1

        filtered = [action for action in plans.values() if action.nodes]
        self.logger.info(
            "Prepared recycle plan covering %d node pool(s) and %d node(s)",
            len(filtered),
            sum(len(action.nodes) for action in filtered),
        )
        return filtered

    # ------------------------------------------------------------------
    # Instance/node pool resolution helpers
    # ------------------------------------------------------------------
    def _find_instance(
        self, host_name: str, compartment_id: str, context: CompartmentContext
    ) -> Optional[oci.core.models.Instance]:
        """Locate a single active compute instance for the given host within the context."""
        host_key = host_name.lower()
        base_host_key = host_key.split(".")[0]

        matches: List[oci.core.models.Instance] = []
        instances = self._instances_for_compartment(context, compartment_id)
        for instance in instances:
            if instance.lifecycle_state not in ACTIVE_INSTANCE_STATES:
                continue

            instance_names = self._candidate_names(instance)
            if host_key in instance_names or base_host_key in instance_names:
                matches.append(instance)

        if not matches:
            self.logger.error(
                "No matching compute instance for host '%s' in compartment %s",
                host_name,
                compartment_id,
            )
            return None
        if len(matches) > 1:
            self.logger.error(
                "Multiple compute instances matched host '%s' in compartment %s",
                host_name,
                compartment_id,
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

    @staticmethod
    def _candidate_names(instance: oci.core.models.Instance) -> List[str]:
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
    # Execution helpers
    # ------------------------------------------------------------------
    def _execute(self, plans: Iterable[NodePoolAction]) -> None:
        """Execute the planned image upgrades and node recycling operations."""
        for action in plans:
            # Refresh before acting so we log the pre-change state alongside every work request.
            node_pool = self._get_node_pool(action.context, action.node_pool_id)
            if not node_pool:
                continue

            current_image_name = getattr(node_pool, "node_image_name", None)
            if current_image_name:
                self.logger.info(
                    "Node pool %s currently using image '%s'",
                    action.node_pool_id,
                    current_image_name,
                )

            summary = NodePoolSummary(
                node_pool_id=action.node_pool_id,
                target_image=action.new_image_name,
                context=action.context,
            )

            if self.dry_run:
                description = f"Update node pool {action.node_pool_id}"
                self.logger.info(
                    "[DRY RUN] Would update node pool %s to image '%s'",
                    action.node_pool_id,
                    action.new_image_name,
                )
                summary.update_result = WorkRequestResult(
                    description=description,
                    status="DRY_RUN",
                )
            else:
                summary.update_result = self._update_node_pool_image(
                    action.context, action.node_pool_id, action.new_image_name
                )

            for node in action.nodes:
                if self.dry_run:
                    self.logger.info(
                        "[DRY RUN] Would recycle node %s (%s) in node pool %s",
                        node.host_name,
                        node.instance_id,
                        action.node_pool_id,
                    )
                    summary.node_results.append(
                        WorkRequestResult(
                            description=(f"Recycle node {node.host_name} ({node.instance_id})"),
                            status="DRY_RUN",
                        )
                    )
                    continue
                recycle_result = self._recycle_node(action.context, action.node_pool_id, node)
                summary.node_results.append(recycle_result)

            post_state, post_image, post_nodes = self._capture_node_pool_health(
                action.context, action.node_pool_id
            )
            # Capture the observed state after recycle so the report reflects real OCI health.
            summary.post_state = post_state
            summary.post_image_name = post_image
            summary.post_node_states = post_nodes
            self._summaries.append(summary)

    def _update_node_pool_image(
        self, context: CompartmentContext, node_pool_id: str, new_image_name: str
    ) -> WorkRequestResult:
        """Update the node pool to the new image and wait for the work request."""
        self.logger.info(
            "Updating node pool %s with new node image '%s'",
            node_pool_id,
            new_image_name,
        )
        details = UpdateNodePoolDetails(
            node_config_details=UpdateNodePoolNodeConfigDetails(
                node_source_details=UpdateNodeSourceViaImageDetails(image_id=new_image_name)
            )
        )
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
        try:
            response = ce_client.update_node_pool(node_pool_id, details)
        except oci_exceptions.ServiceError as exc:
            self.logger.error("Failed to update node pool %s: %s", node_pool_id, exc.message)
            self._errors.append(f"Failed to update node pool {node_pool_id}: {exc.message}")
            return WorkRequestResult(
                description=f"Update node pool {node_pool_id}",
                status="FAILED",
                errors=[exc.message],
            )

        work_request_id = response.headers.get("opc-work-request-id")
        if work_request_id:
            result = self._wait_for_work_request(
                context, work_request_id, f"Update node pool {node_pool_id}"
            )
            if result.status != "SUCCEEDED":
                self._errors.append(
                    f"Node pool update for {node_pool_id} ended with status {result.status}"
                )
            return result

        message = f"Update node pool {node_pool_id} did not return a work request ID"
        self.logger.warning(message)
        self._errors.append(message)
        return WorkRequestResult(
            description=f"Update node pool {node_pool_id}",
            status="UNKNOWN",
            errors=[message],
        )

    def _recycle_node(
        self, context: CompartmentContext, node_pool_id: str, plan: NodeRecyclePlan
    ) -> WorkRequestResult:
        """Delete a specific node from the pool so OKE can recreate it with the new image."""
        self.logger.info(
            "Recycling node %s (%s) from pool %s",
            plan.host_name,
            plan.instance_id,
            node_pool_id,
        )

        ce_client = self._get_ce_client(context)
        if not ce_client:
            message = f"No Container Engine client available for region {context.region}"
            self.logger.error(message)
            self._errors.append(message)
            return WorkRequestResult(
                description=f"Recycle node {plan.host_name} ({plan.instance_id})",
                status="FAILED",
                errors=[message],
            )

        try:
            response = ce_client.delete_node(
                node_pool_id=node_pool_id,
                node_id=plan.instance_id,
                is_decrement_size=False,
            )
        except oci_exceptions.ServiceError as exc:
            self.logger.error(
                "Failed to recycle node %s (%s): %s",
                plan.host_name,
                plan.instance_id,
                exc.message,
            )
            self._errors.append(
                f"Failed to recycle node {plan.host_name} ({plan.instance_id}): {exc.message}"
            )
            return WorkRequestResult(
                description=f"Recycle node {plan.host_name} ({plan.instance_id})",
                status="FAILED",
                errors=[exc.message],
            )

        work_request_id = response.headers.get("opc-work-request-id")
        if work_request_id:
            result = self._wait_for_work_request(
                context,
                work_request_id,
                f"Recycle node {plan.host_name} ({plan.instance_id})",
            )
            if result.status != "SUCCEEDED":
                self._errors.append(
                    "Node recycle for {host} ({instance}) ended with status {status}".format(
                        host=plan.host_name,
                        instance=plan.instance_id,
                        status=result.status,
                    )
                )
            return result

        message = f"Node recycle for {plan.host_name} ({plan.instance_id}) returned no work request"
        self.logger.warning(message)
        self._errors.append(message)
        return WorkRequestResult(
            description=f"Recycle node {plan.host_name} ({plan.instance_id})",
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
            status = work_request.status
            operation = work_request.operation_type
            percent = work_request.percent_complete
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

    def _generate_report(self) -> None:
        """Emit a Markdown report summarizing the recycle operation."""
        if not self._report_path:
            return

        report_lines: List[str] = []
        generated_at = datetime.now(timezone.utc).isoformat()
        used_regions = sorted({context[2] for context in self._used_contexts})
        region_value = ", ".join(used_regions) if used_regions else "unknown"

        # Produce a ready-to-share Markdown summary covering scope, timings, and post-health.
        report_lines.append("# OKE Node Pool Recycle Report")
        report_lines.append("")
        report_lines.append(f"- Generated: {generated_at}")
        if self._timestamp_label:
            report_lines.append(f"- Run ID: {self._timestamp_label}")
        report_lines.append(f"- CSV Source: {self.csv_path}")
        report_lines.append(f"- Meta Source: {self.meta_file}")
        config_display = self.config_file if self.config_file else "~/.oci/config (default)"
        report_lines.append(f"- Config File: {config_display}")
        report_lines.append(f"- Regions Evaluated: {region_value}")
        report_lines.append(f"- Dry Run: {'Yes' if self.dry_run else 'No'}")
        report_lines.append(
            f"- Rows processed: {self._total_rows} (resolved {self._resolved_rows}, skipped {len(self._missing_hosts)})"
        )
        if self._log_path:
            report_lines.append(f"- Log File: {self._log_path}")
        report_lines.append("")

        report_lines.append("## Node Pool Summary")
        report_lines.append(
            "| Node Pool | Region | Target Image | Update Status | Duration (s) | Completed At | Post State | Healthy/Total Nodes |"
        )
        report_lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")

        if not self._summaries:
            report_lines.append(
                "| _None_ | _N/A_ | _N/A_ | _N/A_ | _N/A_ | _N/A_ | _N/A_ | _N/A_ |"
            )
        else:
            for summary in self._summaries:
                update_result = summary.update_result
                status = update_result.status if update_result else "N/A"
                duration = (
                    f"{update_result.duration_seconds:.1f}"
                    if update_result and update_result.duration_seconds is not None
                    else "—"
                )
                completed_at = (
                    update_result.finished_time.isoformat()
                    if update_result and update_result.finished_time
                    else "—"
                )
                post_state = summary.post_state or "Unknown"
                healthy = sum(
                    1
                    for _, state in summary.post_node_states
                    if state and state.upper() in {"ACTIVE", "RUNNING", "HEALTHY"}
                )
                total = len(summary.post_node_states)
                healthy_display = f"{healthy}/{total}" if total else "0/0"
                report_lines.append(
                    "| `{pool}` | {region} | {image} | {status} | {duration} | {completed} | {state} | {health} |".format(
                        pool=summary.node_pool_id,
                        region=summary.context.region,
                        image=summary.target_image,
                        status=status,
                        duration=duration,
                        completed=completed_at,
                        state=post_state,
                        health=healthy_display,
                    )
                )

        for summary in self._summaries:
            report_lines.append("")
            report_lines.append(f"### Node Pool `{summary.node_pool_id}`")
            report_lines.append("")

            update_result = summary.update_result
            report_lines.append(f"- Target image: `{summary.target_image}`")
            report_lines.append(
                "- Scope: project={project}, stage={stage}, region={region}".format(
                    project=summary.context.project,
                    stage=summary.context.stage,
                    region=summary.context.region,
                )
            )
            report_lines.append(
                f"- Update status: {update_result.status if update_result else 'N/A'}"
            )
            report_lines.append(f"- Post image: {summary.post_image_name or 'Unknown'}")
            report_lines.append(f"- Post state: {summary.post_state or 'Unknown'}")
            if update_result and update_result.work_request_id:
                report_lines.append(f"- Work request ID: `{update_result.work_request_id}`")
            if update_result and update_result.accepted_time:
                report_lines.append(
                    f"- Work request accepted: {update_result.accepted_time.isoformat()}"
                )
            if update_result and update_result.finished_time:
                report_lines.append(
                    f"- Work request finished: {update_result.finished_time.isoformat()}"
                )
            if update_result and update_result.duration_seconds is not None:
                report_lines.append(f"- Duration: {update_result.duration_seconds:.1f} seconds")
            if update_result and update_result.errors:
                report_lines.append("- Update errors:")
                for err in update_result.errors:
                    report_lines.append(f"  - {err}")

            report_lines.append("")
            report_lines.append(
                "| Node | Status | Duration (s) | Completed At | Work Request | Notes |"
            )
            report_lines.append("| --- | --- | --- | --- | --- | --- |")
            if not summary.node_results:
                report_lines.append("| _None_ | _N/A_ | _N/A_ | _N/A_ | _N/A_ | _N/A_ |")
            else:
                for node_result in summary.node_results:
                    duration_val = (
                        f"{node_result.duration_seconds:.1f}"
                        if node_result.duration_seconds is not None
                        else "—"
                    )
                    finished_val = (
                        node_result.finished_time.isoformat() if node_result.finished_time else "—"
                    )
                    work_request_val = (
                        f"`{node_result.work_request_id}`" if node_result.work_request_id else "—"
                    )
                    notes = "; ".join(node_result.errors) if node_result.errors else ""
                    report_lines.append(
                        "| {description} | {status} | {duration} | {finished} | {wr} | {notes} |".format(
                            description=node_result.description,
                            status=node_result.status,
                            duration=duration_val,
                            finished=finished_val,
                            wr=work_request_val,
                            notes=notes or "—",
                        )
                    )

            report_lines.append("")
            report_lines.append("Post-operation node health:")
            report_lines.append("")
            report_lines.append("| Node | Lifecycle State |")
            report_lines.append("| --- | --- |")
            if not summary.post_node_states:
                report_lines.append("| _Unknown_ | _N/A_ |")
            else:
                for node_name, state in summary.post_node_states:
                    report_lines.append(f"| `{node_name}` | {state or 'Unknown'} |")

        if self._missing_hosts:
            report_lines.append("")
            report_lines.append("## Skipped Hosts")
            report_lines.append("")
            report_lines.append("| Host | Compartment | Reason |")
            report_lines.append("| --- | --- | --- |")
            for host, compartment in self._missing_hosts:
                report_lines.append(
                    f"| `{host}` | `{compartment}` | No active compute instance found |"
                )

        report_lines.append("")

        self._report_path.parent.mkdir(parents=True, exist_ok=True)
        with self._report_path.open("w", encoding="utf-8") as handle:
            handle.write("\n".join(report_lines) + "\n")

        self.logger.info("Operation report written to %s", self._report_path)
        self.logger.info(
            "Recycle summary: %d total row(s), %d resolved, %d skipped",
            self._total_rows,
            self._resolved_rows,
            len(self._missing_hosts),
        )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recycle OKE node pools based on CSV input.")
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

    recycler = NodePoolRecycler(
        csv_path=csv_path,
        config_file=config_file,
        dry_run=args.dry_run,
        poll_seconds=args.poll_seconds,
        meta_file=meta_file,
    )
    return recycler.run()


if __name__ == "__main__":
    sys.exit(main())
