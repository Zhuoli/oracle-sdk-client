#!/usr/bin/env python3
"""
Generate HTML reports detailing OKE cluster and node pool Kubernetes versions.
"""

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import List, Optional, Sequence

from rich.console import Console
from rich.logging import RichHandler

from oci_client.models import OKEClusterInfo
from oci_client.utils.config import load_region_compartments
from oci_client.utils.display import (
    display_configuration_info,
    display_region_header,
    display_success,
    display_warning,
)
from oci_client.utils.session import create_oci_client, setup_session_token

console = Console()
logger = logging.getLogger(__name__)


@dataclass
class ClusterReportEntry:
    """Aggregated information for a single OKE cluster."""

    project: str
    stage: str
    region: str
    compartment_id: str
    cluster: OKEClusterInfo


def configure_logging() -> None:
    """Configure application logging with rich formatting."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True)],
    )


def parse_arguments() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Generate an HTML report of OKE cluster and node pool versions.",
    )
    parser.add_argument("project_name", help="Project name defined in meta.yaml")
    parser.add_argument("stage", help="Stage name defined in meta.yaml (e.g. dev, staging, prod)")
    parser.add_argument(
        "--config-file",
        default="meta.yaml",
        help="Path to configuration file (default: meta.yaml)",
    )
    parser.add_argument(
        "--output-dir",
        default="reports",
        help="Directory where the report will be written (default: reports)",
    )
    return parser.parse_args()


def collect_cluster_entries(
    *,
    project_name: str,
    stage: str,
    config_file: str,
) -> List[ClusterReportEntry]:
    """Collect OKE cluster information for the configured regions."""
    region_compartments = load_region_compartments(project_name, stage, config_file)
    display_configuration_info(
        project_name, stage, config_file, len(region_compartments), region_compartments
    )

    entries: List[ClusterReportEntry] = []

    for region, compartment_id in region_compartments.items():
        display_region_header(region)

        profile_name = setup_session_token(project_name, stage, region)
        client = create_oci_client(region, profile_name)
        if not client:
            display_warning(f"Skipping region {region}: failed to initialize OCI client.")
            continue

        try:
            clusters = client.list_oke_clusters(compartment_id)
        except Exception as exc:  # pragma: no cover - defensive user feedback
            display_warning(
                f"Unable to list OKE clusters in {region} (compartment {compartment_id}): {exc}"
            )
            continue

        if not clusters:
            display_warning(f"No OKE clusters found in {region}.")
            continue

        display_success(f"Found {len(clusters)} OKE cluster(s) in {region}.")

        for cluster in clusters:
            try:
                node_pools = client.list_node_pools(cluster.cluster_id, compartment_id)
            except Exception as exc:  # pragma: no cover - defensive user feedback
                display_warning(
                    f"Failed to list node pools for cluster {cluster.name} ({cluster.cluster_id}): {exc}"
                )
                node_pools = []

            cluster.node_pools = node_pools
            entries.append(
                ClusterReportEntry(
                    project=project_name,
                    stage=stage,
                    region=region,
                    compartment_id=compartment_id,
                    cluster=cluster,
                )
            )

    return entries


def _format_node_pools(node_pools: Sequence, default_text: str = "No node pools discovered.") -> str:
    """Render node pool information as HTML."""
    if not node_pools:
        return f"<em>{escape(default_text)}</em>"

    items = []
    for node_pool in node_pools:
        node_name = escape(getattr(node_pool, "name", "Unnamed node pool"))
        version = escape(getattr(node_pool, "kubernetes_version", "Unknown") or "Unknown")
        lifecycle = escape(getattr(node_pool, "lifecycle_state", "Unknown") or "Unknown")
        items.append(
            f"<li><strong>{node_name}</strong><br>"
            f"Version: {version} &bull; State: {lifecycle}</li>"
        )

    return "<ul>" + "".join(items) + "</ul>"


def generate_html_report(
    *,
    entries: Sequence[ClusterReportEntry],
    project_name: str,
    stage: str,
    generated_at: datetime,
) -> str:
    """Generate the HTML report string."""
    timestamp = generated_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")

    rows = []
    for entry in entries:
        cluster = entry.cluster
        node_pools_html = _format_node_pools(cluster.node_pools)
        upgrades = ", ".join(cluster.available_upgrades) if cluster.available_upgrades else "None"

        rows.append(
            "<tr>"
            f"<td>{escape(entry.project)}</td>"
            f"<td>{escape(entry.stage)}</td>"
            f"<td>{escape(entry.region)}</td>"
            f"<td class='mono'>{escape(entry.compartment_id)}</td>"
            f"<td>{escape(cluster.name)}</td>"
            f"<td class='mono'>{escape(cluster.cluster_id)}</td>"
            f"<td>{escape(cluster.kubernetes_version or 'Unknown')}</td>"
            f"<td>{escape(upgrades)}</td>"
            f"<td>{node_pools_html}</td>"
            "</tr>"
        )

    if not rows:
        rows.append(
            "<tr><td colspan='9'><em>No OKE clusters were discovered for the provided "
            "project and stage.</em></td></tr>"
        )

    styles = """
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 2rem; }
    h1 { margin-bottom: 0.25rem; }
    h2 { margin-top: 0; color: #555; }
    table { border-collapse: collapse; width: 100%; margin-top: 1.5rem; }
    th, td { border: 1px solid #ccc; padding: 0.5rem; vertical-align: top; }
    th { background-color: #f5f5f5; text-align: left; }
    tr:nth-child(even) { background-color: #fafafa; }
    ul { margin: 0.25rem 0 0.25rem 1.2rem; }
    .mono { font-family: "SFMono-Regular", Menlo, Monaco, Consolas, "Liberation Mono", monospace; }
    footer { margin-top: 2rem; font-size: 0.85rem; color: #666; }
    """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>OKE Version Report - {escape(project_name)} - {escape(stage)}</title>
  <style>{styles}</style>
</head>
<body>
  <h1>OKE Version Report</h1>
  <h2>Project: {escape(project_name)} &mdash; Stage: {escape(stage)}</h2>
  <p>Generated at {escape(timestamp)}</p>
  <table>
    <thead>
      <tr>
        <th>Project</th>
        <th>Stage</th>
        <th>Region</th>
        <th>Compartment OCID</th>
        <th>Cluster Name</th>
        <th>Cluster OCID</th>
        <th>Cluster Version</th>
        <th>Available Upgrades</th>
        <th>Node Pools</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
  <footer>Report generated by oracle-sdk-client tools.</footer>
</body>
</html>
"""

    return html


def write_report(output_path: Path, content: str) -> None:
    """Persist the rendered HTML report to disk."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    logger.info("HTML report written to %s", output_path)


def main() -> int:
    """CLI entry point."""
    args = parse_arguments()
    configure_logging()

    console.print("[bold green]🔍 Generating OKE version report...[/bold green]")

    entries = collect_cluster_entries(
        project_name=args.project_name,
        stage=args.stage,
        config_file=args.config_file,
    )

    generated_at = datetime.now(timezone.utc)
    html = generate_html_report(
        entries=entries,
        project_name=args.project_name,
        stage=args.stage,
        generated_at=generated_at,
    )

    output_dir = Path(args.output_dir)
    output_filename = f"oke_versions_{args.project_name}_{args.stage}.html"
    output_path = output_dir / output_filename
    write_report(output_path, html)

    console.print(
        f"[bold green]✅ Report complete.[/bold green] Saved to [cyan]{output_path}[/cyan]"
    )
    if not entries:
        console.print("[yellow]No clusters discovered. Verify meta.yaml or OCI permissions.[/yellow]")

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Report generation interrupted by user.[/yellow]")
        raise SystemExit(1)
