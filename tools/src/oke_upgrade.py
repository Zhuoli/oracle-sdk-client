#!/usr/bin/env python3
"""
Initiate OKE cluster upgrades based on the generated HTML report.
"""

import argparse
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from rich.console import Console
from rich.logging import RichHandler

from oci_client.models import OKEClusterInfo
from oci_client.utils.display import display_warning
from oci_client.utils.session import create_oci_client, setup_session_token

console = Console()
logger = logging.getLogger(__name__)


@dataclass
class ReportCluster:
    """Cluster entry parsed from the HTML report."""

    project: str
    stage: str
    region: str
    cluster_name: str
    cluster_version: str
    available_upgrades: List[str]
    compartment_ocid: str
    cluster_ocid: str


class _ReportHTMLParser(HTMLParser):
    """Lightweight HTML parser that captures table cell text from the report."""

    def __init__(self) -> None:
        super().__init__()
        self._in_tbody = False
        self._in_tr = False
        self._in_td = False
        self._cell_buffer: List[str] = []
        self._current_row: List[str] = []
        self.rows: List[List[str]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag == "tbody":
            self._in_tbody = True
            return

        if not self._in_tbody:
            return

        if tag == "tr":
            self._in_tr = True
            self._current_row = []
        elif tag == "td" and self._in_tr:
            self._in_td = True
            self._cell_buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "tbody":
            self._in_tbody = False
            return

        if not self._in_tbody:
            return

        if tag == "td" and self._in_td:
            self._in_td = False
            cell_text = "".join(self._cell_buffer).strip()
            self._current_row.append(cell_text)
        elif tag == "tr" and self._in_tr:
            self._in_tr = False
            if self._current_row:
                self.rows.append(self._current_row)

    def handle_data(self, data: str) -> None:
        if self._in_td:
            self._cell_buffer.append(data)


def _parse_available_upgrades(raw_value: str) -> List[str]:
    if not raw_value or raw_value.lower() == "none":
        return []

    versions: List[str] = []
    for candidate in raw_value.split(","):
        candidate = candidate.strip()
        if not candidate:
            continue
        normalized = _extract_version(candidate)
        if not normalized:
            continue
        if normalized not in versions:
            versions.append(normalized)
    return versions


def _parse_report_rows(rows: Iterable[Sequence[str]]) -> List[ReportCluster]:
    parsed_rows: List[ReportCluster] = []

    for raw_row in rows:
        if len(raw_row) < 9:
            # Skip summary rows (e.g., "no clusters discovered").
            continue

        project, stage, region, cluster_name, cluster_version, upgrades_raw, _, compartment_ocid, cluster_ocid = (
            raw_row[0],
            raw_row[1],
            raw_row[2],
            raw_row[3],
            raw_row[4],
            raw_row[5],
            raw_row[6],
            raw_row[7],
            raw_row[8],
        )

        parsed_rows.append(
            ReportCluster(
                project=project,
                stage=stage,
                region=region,
                cluster_name=cluster_name,
                cluster_version=cluster_version,
                available_upgrades=_parse_available_upgrades(upgrades_raw),
                compartment_ocid=compartment_ocid,
                cluster_ocid=cluster_ocid,
            )
        )

    return parsed_rows


def load_clusters_from_report(report_path: Path) -> List[ReportCluster]:
    """Read and parse the HTML report file."""
    parser = _ReportHTMLParser()
    html_content = report_path.read_text(encoding="utf-8")
    parser.feed(html_content)
    return _parse_report_rows(parser.rows)


def _version_key(version: str) -> Tuple[int, ...]:
    digits = re.findall(r"\d+", version)
    if not digits:
        return (0,)
    return tuple(int(value) for value in digits)


def _extract_version(value: str) -> Optional[str]:
    """Normalize version strings such as 'v1.34.1 (control plane)' -> '1.34.1'."""
    if not value:
        return None
    match = re.search(r"\d+(?:\.\d+)*", value)
    if match:
        return match.group(0)
    stripped = value.strip()
    return stripped or None


def choose_target_version(
    available: Sequence[str],
    requested_version: Optional[str] = None,
) -> Optional[str]:
    """
    Select the target version for upgrade.

    If ``requested_version`` is provided it must exist in ``available``.
    Otherwise the highest semantic version present in ``available`` is selected.
    """
    if not available:
        return None

    normalized_requested = _extract_version(requested_version) if requested_version else None

    if normalized_requested:
        normalized_available = [_extract_version(version) for version in available]
        if normalized_requested in normalized_available:
            index = normalized_available.index(normalized_requested)
            return available[index]
        return None

    return max(available, key=_version_key)


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


@dataclass
class UpgradeResult:
    """Outcome of an attempted upgrade."""

    entry: ReportCluster
    target_version: Optional[str]
    work_request_id: Optional[str]
    success: bool
    error: Optional[str] = None


def perform_cluster_upgrades(
    entries: Sequence[ReportCluster],
    *,
    requested_version: Optional[str],
    dry_run: bool,
    filters: Optional[Dict[str, List[str]]] = None,
) -> List[UpgradeResult]:
    filters = filters or {}
    results: List[UpgradeResult] = []
    clients: Dict[Tuple[str, str, str], Any] = {}

    for entry in entries:
        if filters and not _entry_matches_filters(entry, filters):
            logger.debug(
                "Skipping cluster %s due to filters project=%s stage=%s region=%s cluster_filter=%s",
                entry.cluster_name,
                filters.get("project"),
                filters.get("stage"),
                filters.get("region"),
                filters.get("cluster"),
            )
            continue

        normalized_request = _extract_version(requested_version) if requested_version else None
        report_target_version = choose_target_version(entry.available_upgrades, requested_version)

        if not entry.available_upgrades and not requested_version:
            logger.debug(
                "Skipping cluster %s (%s) in %s: no upgrades reported in HTML and no explicit target.",
                entry.cluster_name,
                entry.cluster_ocid,
                entry.region,
            )
            continue

        if dry_run:
            if report_target_version:
                console.print(
                    f"[yellow]DRY RUN[/yellow] Would upgrade [cyan]{entry.cluster_name}[/cyan] "
                    f"({entry.cluster_ocid}) in [cyan]{entry.region}[/cyan] to [green]{report_target_version}[/green]."
                )
                results.append(
                    UpgradeResult(entry=entry, target_version=report_target_version, work_request_id=None, success=True)
                )
            else:
                console.print(
                    f"[yellow]DRY RUN[/yellow] Cluster [cyan]{entry.cluster_name}[/cyan] "
                    f"({entry.cluster_ocid}) in [cyan]{entry.region}[/cyan] has no reported upgrades."
                )
                results.append(
                    UpgradeResult(entry=entry, target_version=None, work_request_id=None, success=False)
                )
            continue

        cache_key = (entry.project, entry.stage, entry.region)
        client = clients.get(cache_key)
        if client is None:
            profile_name = setup_session_token(entry.project, entry.stage, entry.region)
            client = create_oci_client(entry.region, profile_name)
            if not client:
                error_message = (
                    f"Unable to initialize OCI client for {entry.region} "
                    f"(project={entry.project}, stage={entry.stage}). Skipping cluster {entry.cluster_name}."
                )
                display_warning(error_message)
                results.append(
                    UpgradeResult(
                        entry=entry,
                        target_version=report_target_version,
                        work_request_id=None,
                        success=False,
                        error=error_message,
                    )
                )
                continue
            clients[cache_key] = client

        try:
            cluster_details = _resolve_cluster_details(client, entry.cluster_ocid)
        except Exception as exc:  # pragma: no cover - defensive handling
            error_message = (
                f"Failed to fetch cluster details for {entry.cluster_name} "
                f"({entry.cluster_ocid}) in {entry.region}: {exc}"
            )
            logger.error(error_message)
            results.append(
                UpgradeResult(
                    entry=entry,
                    target_version=report_target_version,
                    work_request_id=None,
                    success=False,
                    error=str(exc),
                )
            )
            continue

        api_available = cluster_details.available_upgrades
        api_normalized = [_extract_version(value) for value in api_available]

        fallback_message: Optional[str] = None

        api_target_version: Optional[str] = None

        if normalized_request:
            if normalized_request in api_normalized:
                api_target_version = api_available[api_normalized.index(normalized_request)]
            elif api_available:
                api_target_version = max(api_available, key=_version_key)
                fallback_message = (
                    f"Requested target version {requested_version} not available for cluster "
                    f"{entry.cluster_name} ({entry.cluster_ocid}). Falling back to {api_target_version}."
                )
        elif report_target_version:
            normalized_report = _extract_version(report_target_version)
            if normalized_report and normalized_report in api_normalized:
                api_target_version = api_available[api_normalized.index(normalized_report)]
            elif api_available:
                api_target_version = max(api_available, key=_version_key)
                fallback_message = (
                    f"Report suggested version {report_target_version} for cluster "
                    f"{entry.cluster_name} ({entry.cluster_ocid}), but OCI now offers "
                    f"{', '.join(api_available)}. Using {api_target_version} instead."
                )
        else:
            if api_available:
                api_target_version = max(api_available, key=_version_key)

        if fallback_message:
            console.print(f"[yellow]{fallback_message}[/yellow]")

        if not api_target_version:
            available_text = ", ".join(api_available) or "None"
            requested_text = requested_version or report_target_version
            message = (
                f"OCI reports no matching upgrade for cluster {entry.cluster_name} "
                f"({entry.cluster_ocid}) in {entry.region}. "
                f"Available (fresh): {available_text}. Requested: {requested_text or 'latest'}."
            )
            display_warning(message)
            results.append(
                UpgradeResult(
                    entry=entry,
                    target_version=None,
                    work_request_id=None,
                    success=False,
                    error=message,
                )
            )
            continue

        target_version = api_target_version

        try:
            work_request_id = client.upgrade_oke_cluster(entry.cluster_ocid, target_version)  # type: ignore[attr-defined]
            console.print(
                f"[bold green]✓[/bold green] Upgrade triggered for [cyan]{entry.cluster_name}[/cyan] "
                f"({entry.cluster_ocid}) to [green]{target_version}[/green]. "
                f"Work request: [magenta]{work_request_id or 'N/A'}[/magenta]"
            )
            results.append(
                UpgradeResult(
                    entry=entry,
                    target_version=target_version,
                    work_request_id=work_request_id or None,
                    success=True,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive handling
            error_message = (
                f"Failed to trigger upgrade for cluster {entry.cluster_name} "
                f"({entry.cluster_ocid}) in {entry.region}: {exc}"
            )
            logger.error(error_message)
            results.append(
                UpgradeResult(
                    entry=entry,
                    target_version=target_version,
                    work_request_id=None,
                    success=False,
                    error=str(exc),
                )
            )

    return results


def _resolve_cluster_details(client: Any, cluster_id: str) -> OKEClusterInfo:
    """
    Retrieve cluster details either via the dedicated helper or by directly querying
    the Container Engine client (maintains compatibility with older client versions).
    """
    if hasattr(client, "get_oke_cluster"):
        return client.get_oke_cluster(cluster_id)  # type: ignore[attr-defined]

    ce_client = getattr(client, "container_engine_client", None)
    if ce_client is None:
        raise AttributeError("OCI client does not expose container_engine_client")

    cluster = ce_client.get_cluster(cluster_id).data
    available_upgrades_attr = getattr(cluster, "available_kubernetes_upgrades", None)
    if available_upgrades_attr is None:
        available_upgrades_attr = getattr(cluster, "available_upgrades", None)
    available_upgrades = list(available_upgrades_attr or [])

    return OKEClusterInfo(
        cluster_id=cluster_id,
        name=getattr(cluster, "name", cluster_id),
        kubernetes_version=getattr(cluster, "kubernetes_version", None),
        lifecycle_state=getattr(cluster, "lifecycle_state", None),
        compartment_id=getattr(cluster, "compartment_id", None),
        available_upgrades=available_upgrades,
    )


def configure_logging(verbose: bool = False) -> None:
    """Configure standard logging with rich handler."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True)],
    )


def parse_arguments() -> argparse.Namespace:
    """Set up and parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Trigger upgrades for OKE clusters listed in an HTML report.",
    )
    parser.add_argument("report_path", help="Path to the HTML report generated by oke_version_report.")
    parser.add_argument(
        "--target-version",
        help="Explicit Kubernetes version to upgrade to. Must be present in the report's available upgrades.",
    )
    parser.add_argument("--project", help="Only upgrade clusters for this project.")
    parser.add_argument("--stage", help="Only upgrade clusters for this stage.")
    parser.add_argument("--region", help="Only upgrade clusters in this region.")
    parser.add_argument(
        "--cluster",
        help="Only upgrade the cluster matching this name or OCID.",
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
        f"[bold blue]🚀 Starting OKE upgrades using report:[/bold blue] [cyan]{report_path}[/cyan]"
    )

    entries = load_clusters_from_report(report_path)

    if not entries:
        console.print("[yellow]No clusters found in the report. Nothing to do.[/yellow]")
        return 0

    filters = _build_filters(args)

    results = perform_cluster_upgrades(
        entries,
        requested_version=args.target_version,
        dry_run=args.dry_run,
        filters=filters,
    )

    successes = sum(1 for result in results if result.success and not args.dry_run)
    dry_runs = sum(1 for result in results if result.success and args.dry_run)
    failures = sum(1 for result in results if not result.success)

    if args.dry_run:
        console.print(
            f"[bold blue]Summary:[/bold blue] planned {dry_runs} upgrade(s); "
            f"{failures} cluster(s) skipped."
        )
    else:
        console.print(
            f"[bold blue]Summary:[/bold blue] initiated {successes} upgrade(s); "
            f"{failures} failure(s)."
        )

    return 0 if failures == 0 else 2


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Upgrade process interrupted by user.[/yellow]")
        raise SystemExit(1)
