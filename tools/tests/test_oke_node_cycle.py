from types import SimpleNamespace

import pytest

import oke_node_cycle
from oke_node_cycle import NodeCycleResult, perform_node_cycles
from oke_upgrade import ReportCluster
from oci_client.models import OKEClusterInfo, OKENodePoolInfo


@pytest.fixture
def sample_entry() -> ReportCluster:
    return ReportCluster(
        project="proj",
        stage="dev",
        region="us-phoenix-1",
        cluster_name="test-cluster",
        cluster_version="v1.34.1",
        available_upgrades=[],
        compartment_ocid="ocid1.compartment.oc1..example",
        cluster_ocid="ocid1.cluster.oc1..example",
    )


def _build_fake_client(nodes):
    fake_ce = SimpleNamespace()
    fake_ce.calls = []

    def replace_boot_volume(cluster_id, node_id, _details):
        fake_ce.calls.append((cluster_id, node_id))
        return SimpleNamespace(headers={"opc-work-request-id": "wr1"})

    fake_ce.replace_boot_volume_cluster_node = replace_boot_volume
    fake_ce.get_node_pool = lambda node_pool_id: SimpleNamespace(
        data=SimpleNamespace(nodes=nodes)
    )
    return fake_ce


def test_perform_node_cycles_triggers_replace_boot_volume(monkeypatch, sample_entry):
    node = SimpleNamespace(id="ocid1.instance.oc1..node1", name="node-1", lifecycle_state="ACTIVE")
    fake_ce = _build_fake_client([node])

    fake_client = SimpleNamespace(
        container_engine_client=fake_ce,
        list_node_pools=lambda cluster_id, compartment_id: [
            OKENodePoolInfo(node_pool_id="ocid1.nodepool.oc1..np1", name="np1")
        ],
        get_oke_cluster=lambda cluster_id: OKEClusterInfo(
            cluster_id=cluster_id,
            name="test-cluster",
            kubernetes_version="v1.34.1",
            compartment_id="ocid1.compartment.oc1..example",
            lifecycle_state="ACTIVE",
        ),
    )

    monkeypatch.setattr("oke_node_cycle.setup_session_token", lambda project, stage, region: "profile")
    monkeypatch.setattr("oke_node_cycle.create_oci_client", lambda region, profile: fake_client)

    results = perform_node_cycles(
        [sample_entry],
        grace_period="PT15M",
        force_after_grace=False,
        dry_run=False,
    )

    assert fake_ce.calls == [("ocid1.cluster.oc1..example", "ocid1.instance.oc1..node1")]
    assert len(results) == 1
    assert isinstance(results[0], NodeCycleResult)
    assert results[0].work_request_id == "wr1"
    assert results[0].status in {"IN_PROGRESS", "SUCCEEDED"}


def test_perform_node_cycles_dry_run(monkeypatch, sample_entry):
    node = SimpleNamespace(id="ocid1.instance.oc1..node2", name="node-2", lifecycle_state="ACTIVE")
    fake_ce = _build_fake_client([node])

    fake_client = SimpleNamespace(
        container_engine_client=fake_ce,
        list_node_pools=lambda cluster_id, compartment_id: [
            OKENodePoolInfo(node_pool_id="ocid1.nodepool.oc1..np2", name="np2")
        ],
        get_oke_cluster=lambda cluster_id: OKEClusterInfo(
            cluster_id=cluster_id,
            name="test-cluster",
            kubernetes_version="v1.34.1",
            compartment_id="ocid1.compartment.oc1..example",
            lifecycle_state="ACTIVE",
        ),
    )

    monkeypatch.setattr("oke_node_cycle.setup_session_token", lambda project, stage, region: "profile")
    monkeypatch.setattr("oke_node_cycle.create_oci_client", lambda region, profile: fake_client)

    results = perform_node_cycles(
        [sample_entry],
        grace_period="PT15M",
        force_after_grace=False,
        dry_run=True,
    )

    assert fake_ce.calls == []
    assert len(results) == 1
    assert results[0].skipped is True
    assert results[0].status == "DRY_RUN"
def test_diagnose_report_flags_short_rows(tmp_path):
    html = """<!DOCTYPE html>
<html><body>
<table>
  <tbody>
    <tr><td>only</td><td>three</td><td>columns</td></tr>
  </tbody>
</table>
</body></html>
"""
    report_path = tmp_path / "report.html"
    report_path.write_text(html, encoding="utf-8")

    diagnostics = oke_node_cycle._diagnose_report(report_path)
    assert any("fewer than 9 columns" in line for line in diagnostics)
