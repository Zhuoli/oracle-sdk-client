from types import SimpleNamespace
from typing import List

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


def _build_fake_client(nodes, maximum_unavailable="2"):
    fake_ce = SimpleNamespace()
    fake_ce.update_calls: List[tuple] = []

    def update_node_pool(node_pool_id, details):
        fake_ce.update_calls.append((node_pool_id, details))
        work_request_id = f"wr{len(fake_ce.update_calls)}"
        return SimpleNamespace(headers={"opc-work-request-id": work_request_id})

    fake_ce.update_node_pool = update_node_pool
    fake_ce.get_node_pool = lambda node_pool_id: SimpleNamespace(
        data=SimpleNamespace(
            nodes=nodes,
            node_pool_cycling_details=SimpleNamespace(maximum_unavailable=maximum_unavailable),
            kubernetes_version="1.34.1",
        )
    )
    return fake_ce


def test_perform_node_cycles_triggers_replace_boot_volume(monkeypatch, sample_entry):
    fake_ce = _build_fake_client([SimpleNamespace(id="node1"), SimpleNamespace(id="node2")])

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

    assert len(fake_ce.update_calls) == 1
    pool_id, details = fake_ce.update_calls[0]
    assert pool_id == "ocid1.nodepool.oc1..np1"
    cycling = details.node_pool_cycling_details
    assert cycling.is_node_cycling_enabled is True
    assert cycling.cycle_modes == ["BOOT_VOLUME_REPLACE"]
    assert cycling.maximum_unavailable == 2
    assert results[0].work_request_id == "wr1"
    assert results[0].status in {"IN_PROGRESS", "UNKNOWN"}


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

    assert fake_ce.update_calls == []
    assert len(results) == 1
    assert results[0].skipped is True
    assert results[0].status == "DRY_RUN"


def test_perform_node_cycles_respects_maximum_unavailable(monkeypatch, sample_entry):
    nodes = [
        SimpleNamespace(id=f"ocid1.instance.oc1..node{i}", name=f"node-{i}", lifecycle_state="ACTIVE")
        for i in range(1, 5)
    ]

    fake_ce = _build_fake_client(nodes, maximum_unavailable="2")

    fake_client = SimpleNamespace(
        container_engine_client=fake_ce,
        list_node_pools=lambda cluster_id, compartment_id: [
            OKENodePoolInfo(node_pool_id="ocid1.nodepool.oc1..np-max", name="np-max")
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

    assert len(fake_ce.update_calls) == 1
    pool_id, details = fake_ce.update_calls[0]
    assert pool_id == "ocid1.nodepool.oc1..np-max"
    assert details.node_pool_cycling_details.maximum_unavailable == 2
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
