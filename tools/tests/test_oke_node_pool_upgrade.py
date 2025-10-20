from typing import List

import pytest

from oci_client.models import OKEClusterInfo, OKENodePoolInfo
from oke_node_pool_upgrade import (
    NodePoolUpgradeResult,
    _control_plane_ready,
    perform_node_pool_upgrades,
)
from oke_upgrade import ReportCluster


def _sample_entry() -> ReportCluster:
    return ReportCluster(
        project="remote-observer",
        stage="dev",
        region="us-phoenix-1",
        cluster_name="cluster-a",
        cluster_version="1.34.1",
        available_upgrades=[],
        compartment_ocid="ocid1.compartment.oc1..example",
        cluster_ocid="ocid1.cluster.oc1..clusterA",
    )


def test_control_plane_ready_blocks_available_upgrades() -> None:
    entry = _sample_entry()
    entry.available_upgrades = ["1.35.0"]

    cluster_info = OKEClusterInfo(
        cluster_id=entry.cluster_ocid,
        name=entry.cluster_name,
        kubernetes_version="1.34.1",
        lifecycle_state="ACTIVE",
        compartment_id=entry.compartment_ocid,
        available_upgrades=["1.35.0"],
    )

    message = _control_plane_ready(entry, cluster_info, target_version="1.34.1")

    assert message is not None
    assert "available control plane upgrades" in message


def test_perform_node_pool_upgrades_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _sample_entry()
    cluster_info = OKEClusterInfo(
        cluster_id=entry.cluster_ocid,
        name=entry.cluster_name,
        kubernetes_version="1.34.1",
        lifecycle_state="ACTIVE",
        compartment_id=entry.compartment_ocid,
        available_upgrades=[],
    )
    node_pools = [
        OKENodePoolInfo(
            node_pool_id="ocid1.nodepool.oc1..np1",
            name="pool-a",
            kubernetes_version="1.32.1",
            lifecycle_state="ACTIVE",
        )
    ]

    class FakeClient:
        def get_oke_cluster(self, cluster_id: str) -> OKEClusterInfo:
            assert cluster_id == entry.cluster_ocid
            return cluster_info

        def list_node_pools(
            self, cluster_id: str, compartment_id: str
        ) -> List[OKENodePoolInfo]:
            assert cluster_id == entry.cluster_ocid
            assert compartment_id == entry.compartment_ocid
            return node_pools

        def upgrade_oke_node_pool(self, node_pool_id: str, target_version: str) -> str:
            raise AssertionError("Should not be called during dry-run")

    monkeypatch.setattr(
        "oke_node_pool_upgrade.setup_session_token",
        lambda *args, **kwargs: "profile-name",
    )
    monkeypatch.setattr(
        "oke_node_pool_upgrade.create_oci_client",
        lambda region, profile: FakeClient(),
    )

    results = perform_node_pool_upgrades(
        [entry],
        requested_version=None,
        filters={},
        dry_run=True,
    )

    assert len(results) == 1
    result = results[0]
    assert isinstance(result, NodePoolUpgradeResult)
    assert result.success is True
    assert result.skipped is False
    assert result.target_version == "1.34.1"


def test_perform_node_pool_upgrades_requires_control_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _sample_entry()
    entry.available_upgrades = ["1.35.0"]
    cluster_info = OKEClusterInfo(
        cluster_id=entry.cluster_ocid,
        name=entry.cluster_name,
        kubernetes_version="1.32.1",
        lifecycle_state="ACTIVE",
        compartment_id=entry.compartment_ocid,
        available_upgrades=["1.35.0"],
    )

    class FakeClient:
        def get_oke_cluster(self, cluster_id: str) -> OKEClusterInfo:
            return cluster_info

        def list_node_pools(self, cluster_id: str, compartment_id: str) -> List[OKENodePoolInfo]:
            return []

    monkeypatch.setattr(
        "oke_node_pool_upgrade.setup_session_token",
        lambda *args, **kwargs: "profile-name",
    )
    monkeypatch.setattr(
        "oke_node_pool_upgrade.create_oci_client",
        lambda region, profile: FakeClient(),
    )

    results = perform_node_pool_upgrades(
        [entry],
        requested_version="1.34.1",
        filters={},
        dry_run=False,
    )

    assert len(results) == 1
    assert results[0].success is False
    assert results[0].node_pool is None
    assert "control plane" in (results[0].error or "")


def test_perform_node_pool_upgrades_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _sample_entry()
    cluster_info = OKEClusterInfo(
        cluster_id=entry.cluster_ocid,
        name=entry.cluster_name,
        kubernetes_version="1.34.1",
        lifecycle_state="ACTIVE",
        compartment_id=entry.compartment_ocid,
        available_upgrades=[],
    )
    node_pool = OKENodePoolInfo(
        node_pool_id="ocid1.nodepool.oc1..np1",
        name="pool-a",
        kubernetes_version="1.32.1",
        lifecycle_state="ACTIVE",
    )

    class FakeClient:
        def __init__(self) -> None:
            self.calls: List[str] = []

        def get_oke_cluster(self, cluster_id: str) -> OKEClusterInfo:
            return cluster_info

        def list_node_pools(
            self, cluster_id: str, compartment_id: str
        ) -> List[OKENodePoolInfo]:
            return [node_pool]

        def upgrade_oke_node_pool(self, node_pool_id: str, target_version: str) -> str:
            self.calls.append(f"{node_pool_id}:{target_version}")
            return "wr-123"

    fake_client = FakeClient()

    monkeypatch.setattr(
        "oke_node_pool_upgrade.setup_session_token",
        lambda *args, **kwargs: "profile-name",
    )
    monkeypatch.setattr(
        "oke_node_pool_upgrade.create_oci_client",
        lambda region, profile: fake_client,
    )

    results = perform_node_pool_upgrades(
        [entry],
        requested_version=None,
        filters={},
        dry_run=False,
    )

    assert fake_client.calls == ["ocid1.nodepool.oc1..np1:1.34.1"]
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].work_request_id == "wr-123"
