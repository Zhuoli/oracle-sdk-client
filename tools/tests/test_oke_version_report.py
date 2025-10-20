from datetime import datetime, timezone

from oci_client.models import OKEClusterInfo, OKENodePoolInfo

from oke_version_report import ClusterReportEntry, generate_html_report


def test_generate_html_report_includes_cluster_and_node_pool_data() -> None:
    cluster = OKEClusterInfo(
        cluster_id="ocid1.cluster.oc1..example",
        name="example-cluster",
        kubernetes_version="v1.27.2",
        lifecycle_state="ACTIVE",
        compartment_id="ocid1.compartment.oc1..example",
        available_upgrades=["v1.28.1"],
    )
    cluster.node_pools = [
        OKENodePoolInfo(
            node_pool_id="ocid1.nodepool.oc1..np1",
            name="pool-a",
            kubernetes_version="v1.27.2",
            lifecycle_state="ACTIVE",
        ),
        OKENodePoolInfo(
            node_pool_id="ocid1.nodepool.oc1..np2",
            name="pool-b",
            kubernetes_version="v1.27.2",
            lifecycle_state="SCALING",
        ),
    ]

    entry = ClusterReportEntry(
        project="remote-observer",
        stage="dev",
        region="us-phoenix-1",
        compartment_id="ocid1.compartment.oc1..example",
        cluster=cluster,
    )

    html = generate_html_report(
        entries=[entry],
        project_name="remote-observer",
        stage="dev",
        generated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )

    assert "example-cluster" in html
    assert "pool-a" in html
    assert "pool-b" in html
    assert "v1.27.2" in html
    assert "v1.28.1" in html
    assert "us-phoenix-1" in html


def test_generate_html_report_handles_no_entries() -> None:
    html = generate_html_report(
        entries=[],
        project_name="today-all",
        stage="prod",
        generated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )

    assert "No OKE clusters were discovered" in html
