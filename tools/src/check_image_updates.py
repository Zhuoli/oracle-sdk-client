import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Any

try:
    # OCI SDK
    import oci
except Exception as e:  # pragma: no cover
    print(f"Failed to import OCI SDK: {e}", file=sys.stderr)
    sys.exit(1)

# Reuse existing project utilities
from oci_client.utils.yamler import get_region_compartment_pairs  # type: ignore
from oci_client.utils.session import setup_session_token, create_oci_client  # type: ignore

# Optional: use rich for output
from rich.console import Console
from rich.table import Table
import csv
from pathlib import Path

console = Console()
MISSING = "—"


def _extract_compartment_id(value: Any) -> Optional[str]:
    """
    Extract a compartment_id string from various possible value shapes:
      - Direct string (already an OCID)
      - Dict with key 'compartment_id'
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        cid = value.get("compartment_id")
        if isinstance(cid, str):
            return cid
    return None


def _flatten_region_compartment_pairs(pairs) -> List[Tuple[str, str]]:
    """
    Accepts various possible shapes from get_region_compartment_pairs and
    returns a uniform list of (region, compartment_id).
    Supported shapes:
      - { "us-phoenix-1": "ocid1.compartment..." }
      - { "oc1": { "us-phoenix-1": "ocid1..." } }
      - { "oc1": { "us-phoenix-1": { "compartment_id": "ocid1..." } } }
      - [ ("us-phoenix-1", "ocid1..."), ... ]
      - [ { "region": "us-phoenix-1", "compartment_id": "ocid1..." }, ... ]
    """
    results: List[Tuple[str, str]] = []

    # Mapping types
    if isinstance(pairs, dict):
        for k, v in pairs.items():
            # If the value is another mapping, treat k as a realm and recurse one level
            if isinstance(v, dict):
                # v might be {region: compartment_id or {compartment_id: ...}}
                for region, comp_val in v.items():
                    cid = _extract_compartment_id(comp_val)
                    if isinstance(comp_val, dict) and "compartment_id" not in comp_val:
                        # comp_val might be another nested layer; try extracting one more level
                        cid = None
                        for maybe_region, maybe_comp in comp_val.items():  # type: ignore
                            # If the key looks like a region (contains '-'), prefer it
                            if isinstance(maybe_region, str) and "-" in maybe_region:
                                cid = _extract_compartment_id(maybe_comp)
                                region = maybe_region
                                break
                        if cid is None:
                            # Fallback attempt
                            cid = _extract_compartment_id(comp_val)
                    if cid:
                        results.append((str(region), cid))
            else:
                # k is region, v is compartment id string
                cid = _extract_compartment_id(v)
                if cid:
                    results.append((str(k), cid))

    # Iterable shapes like list/tuple
    elif isinstance(pairs, Iterable):
        for item in pairs:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                region, comp = item
                cid = _extract_compartment_id(comp)
                if cid:
                    results.append((str(region), cid))
            elif isinstance(item, dict):
                region = item.get("region")
                cid = _extract_compartment_id(item.get("compartment_id"))
                if region and cid:
                    results.append((str(region), cid))

    return results


def _build_client_for_region(project: str, stage: str, region: str):
    """
    Build an OCIClient for a region using the same token-based session flow as ssh-sync:
      1) Ensure a valid session token profile exists for the given project/stage/region.
      2) Create an OCIClient bound to that profile and region.
    """
    # Ensure session token profile exists and is valid (creates if needed)
    profile_name = setup_session_token(project_name=project, stage=stage, region=region)

    # Create an OCIClient using that profile
    client = create_oci_client(region=region, profile_name=profile_name)
    if client is None:
        raise RuntimeError(f"Failed to initialize OCI client for region {region} and profile {profile_name}")
    return client


def _get_primary_hostname_for_instance(
    compute_client: oci.core.ComputeClient,
    network_client: oci.core.VirtualNetworkClient,
    instance: oci.core.models.Instance,
) -> str:
    """
    Return instance.display_name for consistency with node cycling logic.

    Note: We use display_name instead of hostname_label because:
    1. hostname_label may have underscores converted to hyphens (DNS restrictions)
    2. node_cycle_pools.py matches instances by display_name
    3. This ensures CSV hostname matches what's used for instance lookup
    """
    # Always use display_name to ensure consistency with node cycling
    return instance.display_name or instance.id


def _format_defined_tags(dt: Any) -> str:
    """
    Produce a compact one-line summary of defined_tags structure:
    namespace={k=v, ...}; namespace2={...}
    """
    if not isinstance(dt, dict):
        return str(dt)
    parts: List[str] = []
    for ns, kv in dt.items():
        if isinstance(kv, dict):
            inner = ", ".join(f"{str(k)}={str(v)}" for k, v in kv.items())
            parts.append(f"{ns}={{ {inner} }}")
        else:
            parts.append(f"{ns}={str(kv)}")
    return "; ".join(parts)


def _safe_get_defined_tag(resource, namespace: str, key: str, verbose: bool = True) -> Optional[str]:
    """
    Attempt to fetch a defined tag, with detailed diagnostics when not found.
    Logs why retrieval failed and prints all existing defined tags on the resource.
    If verbose is False, it returns None silently.
    """
    # Identify resource for logging
    res_type = type(resource).__name__
    res_id = getattr(resource, "id", None)
    res_name = getattr(resource, "display_name", None)
    res_label = res_name or res_id or "<unknown>"

    dt = getattr(resource, "defined_tags", None)
    if not isinstance(dt, dict):
        if verbose:
            console.print(
                f"[yellow]{res_type} '{res_label}': defined_tags missing or not a dict (value={dt!r})[/yellow]"
            )
            # Also show freeform tags if present (can help troubleshooting)
            ff = getattr(resource, "freeform_tags", None)
            if isinstance(ff, dict) and ff:
                ff_summary = ", ".join(f"{k}={v}" for k, v in ff.items())
                console.print(f"[dim]{res_type} '{res_label}' freeform_tags: {ff_summary}[/dim]")
        return None

    ns = dt.get(namespace)
    if not isinstance(ns, dict):
        if verbose:
            available_namespaces = ", ".join(dt.keys()) if dt else "(none)"
            console.print(
                f"[yellow]{res_type} '{res_label}': namespace '{namespace}' not found in defined_tags. "
                f"Available namespaces: {available_namespaces}[/yellow]"
            )
            console.print(f"[dim]{res_type} '{res_label}' defined_tags: {_format_defined_tags(dt)}[/dim]")
        return None

    if key not in ns:
        if verbose:
            available_keys = ", ".join(ns.keys()) if ns else "(none)"
            console.print(
                f"[yellow]{res_type} '{res_label}': key '{key}' not found in namespace '{namespace}'. "
                f"Available keys in namespace: {available_keys}[/yellow]"
            )
            console.print(f"[dim]{res_type} '{res_label}' defined_tags[{namespace}]: {_format_defined_tags({namespace: ns})}[/dim]")
        return None

    val = ns.get(key)
    if not isinstance(val, str):
        if verbose:
            console.print(
                f"[yellow]{res_type} '{res_label}': value for defined_tags.{namespace}.{key} is not a string "
                f"(type={type(val).__name__}, value={val!r})[/yellow]"
            )
            console.print(f"[dim]{res_type} '{res_label}' defined_tags[{namespace}]: {_format_defined_tags({namespace: ns})}[/dim]")
        return None

    return val


def _get_image_type(resource, verbose: bool = True) -> Optional[str]:
    """
    Retrieve the image 'type' tag, preferring ics_images.type, then falling back to icm_images.type.
    """
    t = _safe_get_defined_tag(resource, "ics_images", "type", verbose=verbose)
    if t:
        return t
    return _safe_get_defined_tag(resource, "icm_images", "type", verbose=verbose)


def _find_latest_image_with_same_type(
    compute_client: oci.core.compute_client.ComputeClient,
    image_compartment_id: str,
    target_type: str,
) -> Optional[oci.core.models.Image]:
    """
    Find an image in 'image_compartment_id' that has:
      - defined_tags.ics_images.type (or icm_images.type) == target_type
      - defined_tags.ics_images.release == 'LATEST'
    Returns the first match if found.
    """
    try:
        images = oci.pagination.list_call_get_all_results(
            compute_client.list_images,
            compartment_id=image_compartment_id,
            sort_by="TIMECREATED",
            sort_order="DESC",
        ).data
    except Exception as e:
        console.print(f"[red]Failed to list images in compartment {image_compartment_id}: {e}[/red]")
        return None

    for img in images:
        if not getattr(img, "defined_tags", None):
            continue
        img_type = _get_image_type(img, verbose=False)
        release = _safe_get_defined_tag(img, "ics_images", "release", verbose=False)
        if img_type == target_type and release and release.upper() == "LATEST":
            return img
    return None


def _collect_instances_with_images(
    project: str, stage: str, region: str, compartment_id: str
) -> List[Tuple[str, str, str, str, str]]:
    """
    For a given region and compartment, return a list of tuples:
      (hostname, compartment_id, current_image_name, newer_image_name or '—')
    Lists ALL running instances and attempts to find a newer image for each.
    """
    try:
        client = _build_client_for_region(project, stage, region)
    except Exception as e:
        console.print(f"[red]Failed to initialize OCI client for {region}: {e}[/red]")
        return []

    # Access underlying SDK clients from the wrapper, mirroring ssh-sync behavior
    compute_client = getattr(client, "compute_client", None)
    network_client = getattr(client, "network_client", None)
    if compute_client is None or network_client is None:
        console.print("[red]OCIClient does not expose compute_client/network_client[/red]")
        return []

    try:
        instances = oci.pagination.list_call_get_all_results(
            compute_client.list_instances,
            compartment_id=compartment_id,
            lifecycle_state="RUNNING",
        ).data
    except Exception as e:
        console.print(f"[red]Failed to list instances in {region} / {compartment_id}: {e}[/red]")
        return []

    if not instances:
        console.print(f"[yellow]No RUNNING instances found in region {region}, compartment {compartment_id}[/yellow]")

    results: List[Tuple[str, str, str, str, str]] = []

    for inst in instances:
        hostname = _get_primary_hostname_for_instance(compute_client, network_client, inst)
        instance_name = getattr(inst, "display_name", None) or inst.id

        # Defaults if we cannot determine image info
        current_image_name = MISSING
        newer_image_name = MISSING

        # Fetch current image details
        image_id = getattr(inst, "image_id", None)
        image = None
        if not image_id:
            console.print(f"[yellow]Instance '{instance_name}' ({hostname}) has no image_id; skipping newer-image check[/yellow]")
        else:
            try:
                image = compute_client.get_image(image_id).data
                current_image_name = getattr(image, "display_name", "") or image_id
            except Exception as e:
                current_image_name = image_id
                console.print(f"[yellow]Instance '{instance_name}' ({hostname}): failed to fetch image '{image_id}': {e}[/yellow]")

        # If we have image info, try to find newer
        if image:
            image_compartment_id = getattr(image, "compartment_id", None)
            image_type = _get_image_type(image, verbose=True)

            missing_bits = []
            if not image_compartment_id:
                missing_bits.append("image.compartment_id")
            if not image_type:
                missing_bits.append("defined_tags.ics_images.type (or icm_images.type)")

            if missing_bits:
                console.print(
                    f"[yellow]Instance '{instance_name}' ({hostname}): cannot search for LATEST image; missing {', '.join(missing_bits)}[/yellow]"
                )
                console.print(
                    f"[dim]Image '{getattr(image, 'display_name', getattr(image, 'id', '<unknown>'))}' defined_tags: {_format_defined_tags(getattr(image, 'defined_tags', {}))}[/dim]"
                )
            else:
                latest_image = _find_latest_image_with_same_type(
                    compute_client, image_compartment_id=image_compartment_id, target_type=image_type
                )
                if not latest_image:
                    console.print(
                        f"[dim]Instance '{instance_name}' ({hostname}): no LATEST image found for type '{image_type}' in image compartment '{image_compartment_id}'[/dim]"
                    )
                else:
                    # compute candidate name
                    candidate_name = getattr(latest_image, "display_name", "") or getattr(latest_image, "id", "")
                    if not candidate_name:
                        console.print(
                            f"[dim]Instance '{instance_name}' ({hostname}): LATEST image found but has no display_name or id; cannot compare[/dim]"
                        )
                    elif candidate_name == current_image_name:
                        console.print(
                            f"[dim]Instance '{instance_name}' ({hostname}): current image matches LATEST '{candidate_name}'[/dim]"
                        )
                    else:
                        newer_image_name = candidate_name
                        console.print(
                            f"[green]Instance '{instance_name}' ({hostname}): newer image available -> '{newer_image_name}' (current '{current_image_name}')[/green]"
                        )

        results.append((hostname, region, inst.compartment_id, current_image_name, newer_image_name))

    return results


def main(argv: List[str]) -> int:
    if len(argv) < 3:
        console.print("[red]Usage: python src/check_image_updates.py <PROJECT> <STAGE>[/red]")
        return 1

    project = argv[1]
    stage = argv[2]

    yaml_path = Path(__file__).parent.parent / "meta.yaml"

    try:
        pairs = get_region_compartment_pairs(str(yaml_path), project_name=project, stage=stage)
    except Exception as e:
        console.print(f"[red]Failed to read region/compartment pairs from {yaml_path}: {e}[/red]")
        return 1

    region_compartment_list = _flatten_region_compartment_pairs(pairs)
    if not region_compartment_list:
        console.print(f"[yellow]No regions/compartments found for project={project}, stage={stage}[/yellow]")
        # Still print an empty table for consistency
        table = Table(title=f"Image Updates for Project '{project}' Stage '{stage}'")
        table.add_column("Host name", style="bold")
        table.add_column("Compartment ID")
        table.add_column("Current Image")
        table.add_column("Newer Available Image")
        console.print(table)
        return 0

    all_rows: List[Tuple[str, str, str, str, str]] = []
    for region, compartment_id in region_compartment_list:
        console.print(f"[cyan]Processing region {region} (compartment {compartment_id})...[/cyan]")
        rows = _collect_instances_with_images(project, stage, region, compartment_id)
        all_rows.extend(rows)

    # Always print table with ALL instances discovered
    table = Table(title=f"Image Updates for Project '{project}' Stage '{stage}'")
    table.add_column("Host name", style="bold")
    table.add_column("Region")
    table.add_column("Compartment ID")
    table.add_column("Current Image")
    table.add_column("Newer Available Image")

    for hostname, region, comp_id, current_img, newer_img in all_rows:
        table.add_row(hostname, region, comp_id, current_img, newer_img)

    console.print(table)

    # Write CSV file at project root
    csv_filename = Path(__file__).parent.parent.parent / "oci_image_updates_report.csv"
    try:
        with open(csv_filename, "w", newline='', encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Host name", "Region", "Compartment ID", "Current Image", "Newer Available Image"])
            for hostname, region, comp_id, current_img, newer_img in all_rows:
                writer.writerow([hostname, region, comp_id, current_img, newer_img])
        console.print(f"[green]CSV report saved to {csv_filename}[/green]")
    except Exception as e:
        console.print(f"[red]Failed to write CSV report: {e}[/red]")
    
    # Optional summary
    newer_count = sum(1 for _, _, _, _, newer in all_rows if newer and newer != MISSING)
    total_instances = len(all_rows)
    console.print(f"[dim]Summary: {newer_count} of {total_instances} running instances have a newer image available.[/dim]")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
