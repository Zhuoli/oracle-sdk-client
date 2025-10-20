#!/usr/bin/env python3
"""
Upgrade OKE node pools based on the generated HTML report.
"""

import argparse
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from rich.console import Console
from rich.logging import RichHandler

from oci_client.models import OKENodePoolInfo, OKEClusterInfo
from oci_client.utils.display import display_warning
from oci_client.utils.session import create_oci_client, setup_session_token
from oke_upgrade import ReportCluster, load_clusters_from_report

console = Console()
logger = logging.getLogger(__name__)


@dataclass
class NodePoolUpgradeResult:
    """Outcome for a node pool upgrade attempt."""

    entry: ReportCluster
    node_pool: Optional[OKENodePoolInfo]
    target_version: Optional[str]
    work_request_id: Optional[str]
    success: bool
    skipped: bool = False
    error: Optional[str] = None


def _build_filters(args: argparse.Namespace) -> Dict[str, List[str]]:
    filters: Dict[str, List[str]] = {}
    if args.project:
        filters["project"] = [args.project]
    if args.stage:
        filters["stage"] = [args.stage]
    if args.region:
        filters["region"] = [args.region]
    if args.cluster:
        filters["cluster"] = [args.cluster]
    if args.node_pool:
        filters["node_pool"] = args.node_pool
    return filters


def _entry_matches_filters(entry: ReportCluster, filters: Dict[str, List[str]]) -> bool:
    project_filter = filters.get("project")
    stage_filter = filters.get("stage")
    region_filter = filters.get("region")
    cluster_filter = filters.get("cluster")

    if project_filter and entry.project not in project_filter:
        return False
    if stage_filter and entry.stage not in stage_filter:
        return False
    if region_filter and entry.region not in region_filter:
        return False
    if cluster_filter and entry.cluster_ocid not in cluster_filter and entry.cluster_name not in cluster_filter:
        return False
    return True


def _node_pool_matches_filters(node_pool: OKENodePoolInfo, filters: Dict[str, List[str]]) -> bool:
    node_pool_filter = filters.get("node_pool")
    if not node_pool_filter:
        return True
    return node_pool.node_pool_id in node_pool_filter or node_pool.name in node_pool_filter


def _version_key(version: Optional[str]) -> Tuple[int, ...]:
    if not version:
        return (0,)
    digits = re.findall(r"\d+", version)
    if not digits:
        return (0,)
    return tuple(int(value) for value in digits)


def _control_plane_ready(
    entry: ReportCluster,
    cluster_info: OKEClusterInfo,
    target_version: Optional[str],
) -> Optional[str]:
    if entry.available_upgrades:
        return (
            f"Cluster {entry.cluster_name} ({entry.cluster_ocid}) still reports available control "
            f"plane upgrades in the HTML report ({', '.join(entry.available_upgrades)}). "
            "Complete the cluster upgrade first and regenerate the report."
        )

    actual_version = cluster_info.kubernetes_version
    if not actual_version:
        return (
            f"Cluster {entry.cluster_name} ({entry.cluster_ocid}) does not expose a control plane "
            "version. Verify OCI permissions and retry."
        )

    if target_version and actual_version != target_version:
        return (
            f"Cluster {entry.cluster_name} ({entry.cluster_ocid}) control plane is on "
            f"{actual_version}, but target version {target_version} was requested. "
            "Upgrade the control plane first."
        )

    return None


def perform_node_pool_upgrades(
    entries: Sequence[ReportCluster],
    *,
    requested_version: Optional[str],
    filters: Dict[str, List[str]],
    dry_run: bool,
) -> List[NodePoolUpgradeResult]:
    results: List[NodePoolUpgradeResult] = []
    clients: Dict[Tuple[str, str, str], Any] = {}

    for entry in entries:
        if filters and not _entry_matches_filters(entry, filters):
            logger.debug(
                "Skipping cluster %s due to filters project=%s stage=%s region=%s cluster_filter=%s node_pool_filter=%s",
                entry.cluster_name,
                filters.get("project"),
                filters.get("stage"),
                filters.get("region"),
                filters.get("cluster"),
                filters.get("node_pool"),
            )
            continue

        cache_key = (entry.project, entry.stage, entry.region)
        client = clients.get(cache_key)
        if client is None:
            profile_name = setup_session_token(entry.project, entry.stage, entry.region)
            client = create_oci_client(entry.region, profile_name)
            if not client:
                message = (
                    f"Unable to initialize OCI client for {entry.region} "
                    f"(project={entry.project}, stage={entry.stage})."
                )
                display_warning(message)
                results.append(
                    NodePoolUpgradeResult(
                        entry=entry,
                        node_pool=None,
                        target_version=None,
                        work_request_id=None,
                        success=False,
                        error=message,
                    )
                )
                continue
            clients[cache_key] = client

        try:
            cluster_info = client.get_oke_cluster(entry.cluster_ocid)
        except Exception as exc:  # pragma: no cover - defensive guard
            message = (
                f"Failed to fetch cluster details for {entry.cluster_name} "
                f"({entry.cluster_ocid}): {exc}"
            )
            display_warning(message)
            results.append(
                NodePoolUpgradeResult(
                    entry=entry,
                    node_pool=None,
                    target_version=None,
                    work_request_id=None,
                    success=False,
                    error=str(exc),
                )
            )
            continue

        target_version = requested_version or cluster_info.kubernetes_version

        readiness_error = _control_plane_ready(entry, cluster_info, requested_version)
        if readiness_error:
            display_warning(readiness_error)
            results.append(
                NodePoolUpgradeResult(
                    entry=entry,
                    node_pool=None,
                    target_version=target_version,
                    work_request_id=None,
                    success=False,
                    error=readiness_error,
                )
            )
            continue

        try:
            node_pools = client.list_node_pools(entry.cluster_ocid, entry.compartment_ocid)
        except Exception as exc:  # pragma: no cover - defensive guard
            message = (
                f"Failed to list node pools for cluster {entry.cluster_name} "
                f"({entry.cluster_ocid}): {exc}"
            )
            display_warning(message)
            results.append(
                NodePoolUpgradeResult(
                    entry=entry,
                    node_pool=None,
                    target_version=target_version,
                    work_request_id=None,
                    success=False,
                    error=str(exc),
                )
            )
            continue

        filtered_node_pools = [
            node_pool for node_pool in node_pools if _node_pool_matches_filters(node_pool, filters)
        ]

        if not filtered_node_pools:
            display_warning(
                f"No node pools matched the filters for cluster {entry.cluster_name} ({entry.cluster_ocid})."
            )
            results.append(
                NodePoolUpgradeResult(
                    entry=entry,
                    node_pool=None,
                    target_version=target_version,
                    work_request_id=None,
                    success=False,
                    error="No node pools matched filters.",
                )
            )
            continue

        assert target_version is not None  # for mypy; control plane readiness ensures this

        cluster_version_key = _version_key(cluster_info.kubernetes_version)

        for node_pool in filtered_node_pools:
            current_version = node_pool.kubernetes_version
            if current_version and _version_key(current_version) == _version_key(target_version):
                console.print(
                    f"[dim]Node pool [cyan]{node_pool.name}[/cyan] ({node_pool.node_pool_id}) "
                    f"already on {target_version}. Skipping.[/dim]"
                )
                results.append(
                    NodePoolUpgradeResult(
                        entry=entry,
                        node_pool=node_pool,
                        target_version=target_version,
                        work_request_id=None,
                        success=True,
                        skipped=True,
                    )
                )
                continue

            if current_version and cluster_version_key < _version_key(current_version):
                message = (
                    f"Node pool {node_pool.name} ({node_pool.node_pool_id}) reports "
                    f"version {current_version}, which is ahead of the cluster control plane "
                    f"{cluster_info.kubernetes_version}. Skipping."
                )
                display_warning(message)
                results.append(
                    NodePoolUpgradeResult(
                        entry=entry,
                        node_pool=node_pool,
                        target_version=target_version,
                        work_request_id=None,
                        success=False,
                        error=message,
                    )
                )
                continue

            if dry_run:
                console.print(
                    f"[yellow]DRY RUN[/yellow] Would upgrade node pool [cyan]{node_pool.name}[/cyan] "
                    f"({node_pool.node_pool_id}) to [green]{target_version}[/green]."
                )
                results.append(
                    NodePoolUpgradeResult(
                        entry=entry,
                        node_pool=node_pool,
                        target_version=target_version,
                        work_request_id=None,
                        success=True,
                    )
                )
                continue

            try:
                work_request_id = client.upgrade_oke_node_pool(
                    node_pool.node_pool_id, target_version
                )
                console.print(
                    f"[bold green]✓[/bold green] Upgrade triggered for node pool [cyan]{node_pool.name}[/cyan] "
                    f"({node_pool.node_pool_id}) to [green]{target_version}[/green]. "
                    f"Work request: [magenta]{work_request_id or 'N/A'}[/magenta]"
                )
                results.append(
                    NodePoolUpgradeResult(
                        entry=entry,
                        node_pool=node_pool,
                        target_version=target_version,
                        work_request_id=work_request_id or None,
                        success=True,
                    )
                )
            except Exception as exc:  # pragma: no cover - defensive guard
                message = (
                    f"Failed to trigger upgrade for node pool {node_pool.name} "
                    f"({node_pool.node_pool_id}): {exc}"
                )
                logger.error(message)
                results.append(
                    NodePoolUpgradeResult(
                        entry=entry,
                        node_pool=node_pool,
                        target_version=target_version,
                        work_request_id=None,
                        success=False,
                        error=str(exc),
                    )
                )

    return results


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True)],
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Trigger upgrades for OKE node pools listed in an HTML report.",
    )
    parser.add_argument("report_path", help="Path to the HTML report generated by oke_version_report.")
    parser.add_argument(
        "--target-version",
        help="Explicit Kubernetes version to upgrade to. Defaults to the cluster control plane version.",
    )
    parser.add_argument("--project", help="Only upgrade clusters for this project.")
    parser.add_argument("--stage", help="Only upgrade clusters for this stage.")
    parser.add_argument("--region", help="Only upgrade clusters in this region.")
    parser.add_argument(
        "--cluster",
        help="Only upgrade the cluster matching this name or OCID.",
    )
    parser.add_argument(
        "--node-pool",
        action="append",
        help="Only upgrade node pools matching this name or OCID. Can be provided multiple times.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned upgrades without calling OCI APIs.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    configure_logging(verbose=args.verbose)

    report_path = Path(args.report_path).expanduser().resolve()
    if not report_path.exists():
        console.print(f"[red]Report file not found: {report_path}[/red]")
        return 1

    console.print(
        f"[bold blue]🚀 Starting OKE node pool upgrades using report:[/bold blue] [cyan]{report_path}[/cyan]"
    )

    entries = load_clusters_from_report(report_path)
    if not entries:
        console.print("[yellow]No clusters found in the report. Nothing to do.[/yellow]")
        return 0

    filters = _build_filters(args)

    results = perform_node_pool_upgrades(
        entries,
        requested_version=args.target_version,
        filters=filters,
        dry_run=args.dry_run,
    )

    initiated = sum(
        1 for result in results if result.success and not result.skipped and not args.dry_run
    )
    planned = sum(1 for result in results if result.success and not result.skipped and args.dry_run)
    skipped = sum(1 for result in results if result.skipped)
    failures = sum(1 for result in results if not result.success)

    if args.dry_run:
        console.print(
            f"[bold blue]Summary:[/bold blue] planned {planned} node pool upgrade(s); "
            f"{skipped} already up-to-date; {failures} failure(s)."
        )
    else:
        console.print(
            f"[bold blue]Summary:[/bold blue] initiated {initiated} node pool upgrade(s); "
            f"{skipped} already up-to-date; {failures} failure(s)."
        )

    return 0 if failures == 0 else 2


if __name__ == "__main__":  # pragma: no cover - CLI entry
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Node pool upgrade process interrupted by user.[/yellow]")
        raise SystemExit(1)
