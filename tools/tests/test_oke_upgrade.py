from pathlib import Path
from types import SimpleNamespace
from typing import List, Tuple

import pytest

from oci_client.models import OKEClusterInfo
from oke_upgrade import (
    ReportCluster,
    choose_target_version,
    load_clusters_from_report,
    perform_cluster_upgrades,
)


def _write_temp_report(tmp_path: Path, rows: List[str]) -> Path:
    html = """<!DOCTYPE html>
<html>
<body>
  <table>
    <tbody>
{rows}
    </tbody>
  </table>
</body>
</html>
""".format(rows="\n".join(rows))
    report_path = tmp_path / "report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path


def test_load_clusters_from_report_parses_rows(tmp_path: Path) -> None:
    row = (
        "      <tr>"
        "<td>remote-observer</td>"
        "<td>dev</td>"
        "<td>us-phoenix-1</td>"
        "<td>cluster-a</td>"
        "<td>1.32.1</td>"
        "<td>v1.33.1, 1.34.1 (control plane)</td>"
        "<td>Node pools</td>"
        "<td>ocid1.compartment.oc1..example</td>"
        "<td>ocid1.cluster.oc1..clusterA</td>"
        "</tr>"
    )
    report_path = _write_temp_report(tmp_path, [row])

    clusters = load_clusters_from_report(report_path)

    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.project == "remote-observer"
    assert cluster.stage == "dev"
    assert cluster.region == "us-phoenix-1"
    assert cluster.cluster_name == "cluster-a"
    assert cluster.available_upgrades == ["1.33.1", "1.34.1"]


def test_choose_target_version_prefers_requested() -> None:
    available = ["1.33.1", "1.34.1"]

    assert choose_target_version(available, requested_version="1.33.1") == "1.33.1"
    assert choose_target_version(available, requested_version="1.35.0") is None


def test_choose_target_version_selects_highest() -> None:
    available = ["v1.31.5", "v1.34.1", "v1.32.2"]

    assert choose_target_version(available) == "v1.34.1"


def test_choose_target_version_handles_prefixed_request() -> None:
    available = ["1.33.1", "1.34.1"]

    assert choose_target_version(available, requested_version="v1.34.1") == "1.34.1"


def test_perform_cluster_upgrades_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = ReportCluster(
        project="remote-observer",
        stage="dev",
        region="us-phoenix-1",
        cluster_name="cluster-a",
        cluster_version="1.32.1",
        available_upgrades=["1.34.1"],
        compartment_ocid="ocid1.compartment.oc1..example",
        cluster_ocid="ocid1.cluster.oc1..clusterA",
    )

    # Ensure we would fail if the function attempted to create a client during dry-run.
    monkeypatch.setattr("oke_upgrade.setup_session_token", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("should not be called")))  # type: ignore
    monkeypatch.setattr("oke_upgrade.create_oci_client", lambda *args, **kwargs: None)  # type: ignore

    results = perform_cluster_upgrades(
        [entry],
        requested_version=None,
        dry_run=True,
        filters={},
    )

    assert len(results) == 1
    assert results[0].success is True
    assert results[0].skipped is False
    assert results[0].target_version == "1.34.1"
    assert results[0].work_request_id is None


def test_perform_cluster_upgrades_triggers_upgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = ReportCluster(
        project="remote-observer",
        stage="dev",
        region="us-phoenix-1",
        cluster_name="cluster-a",
        cluster_version="1.32.1",
        available_upgrades=["1.34.1"],
        compartment_ocid="ocid1.compartment.oc1..example",
        cluster_ocid="ocid1.cluster.oc1..clusterA",
    )

    requested_profile = {}

    def fake_setup_session_token(project: str, stage: str, region: str) -> str:
        requested_profile["key"] = (project, stage, region)
        return "profile-name"

    class FakeClient:
        def __init__(self) -> None:
            self.calls: List[Tuple[str, str]] = []

        def get_oke_cluster(self, cluster_id: str) -> OKEClusterInfo:
            assert cluster_id == entry.cluster_ocid
            return OKEClusterInfo(
                cluster_id=cluster_id,
                name="cluster-a",
                kubernetes_version="1.32.1",
                lifecycle_state="ACTIVE",
                compartment_id=entry.compartment_ocid,
                available_upgrades=["v1.34.1"],
            )

        def upgrade_oke_cluster(self, cluster_id: str, target_version: str) -> str:
            self.calls.append((cluster_id, target_version))
            return "work-request-123"

    fake_client = FakeClient()

    monkeypatch.setattr("oke_upgrade.setup_session_token", fake_setup_session_token)  # type: ignore
    monkeypatch.setattr("oke_upgrade.create_oci_client", lambda region, profile: fake_client)  # type: ignore

    results = perform_cluster_upgrades(
        [entry],
        requested_version=None,
        dry_run=False,
        filters={},
    )

    assert requested_profile["key"] == ("remote-observer", "dev", "us-phoenix-1")
    assert fake_client.calls == [("ocid1.cluster.oc1..clusterA", "v1.34.1")]
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].skipped is False
    assert results[0].work_request_id == "work-request-123"


def test_perform_cluster_upgrades_uses_container_engine_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = ReportCluster(
        project="remote-observer",
        stage="dev",
        region="us-phoenix-1",
        cluster_name="cluster-a",
        cluster_version="1.32.1",
        available_upgrades=["1.34.1"],
        compartment_ocid="ocid1.compartment.oc1..example",
        cluster_ocid="ocid1.cluster.oc1..clusterA",
    )

    class FakeCEClient:
        def get_cluster(self, cluster_id: str) -> SimpleNamespace:
            assert cluster_id == entry.cluster_ocid
            cluster = SimpleNamespace(
                name="cluster-a",
                kubernetes_version="1.32.1",
                lifecycle_state="ACTIVE",
                compartment_id=entry.compartment_ocid,
                available_kubernetes_upgrades=["v1.34.1"],
            )
            return SimpleNamespace(data=cluster)

    class FakeClient:
        def __init__(self) -> None:
            self.container_engine_client = FakeCEClient()
            self.calls: List[Tuple[str, str]] = []

        def upgrade_oke_cluster(self, cluster_id: str, target_version: str) -> str:
            self.calls.append((cluster_id, target_version))
            return "work-request-234"

    fake_client = FakeClient()

    monkeypatch.setattr("oke_upgrade.setup_session_token", lambda *args, **kwargs: "profile-name")  # type: ignore
    monkeypatch.setattr("oke_upgrade.create_oci_client", lambda region, profile: fake_client)  # type: ignore

    results = perform_cluster_upgrades(
        [entry],
        requested_version=None,
        dry_run=False,
        filters={},
    )

    assert fake_client.calls == [("ocid1.cluster.oc1..clusterA", "v1.34.1")]
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].work_request_id == "work-request-234"


def test_perform_cluster_upgrades_falls_back_to_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = ReportCluster(
        project="remote-observer",
        stage="dev",
        region="us-phoenix-1",
        cluster_name="cluster-a",
        cluster_version="1.32.1",
        available_upgrades=["1.33.1"],
        compartment_ocid="ocid1.compartment.oc1..example",
        cluster_ocid="ocid1.cluster.oc1..clusterA",
    )

    class FakeClient:
        def __init__(self) -> None:
            self.calls: List[Tuple[str, str]] = []

        def get_oke_cluster(self, cluster_id: str) -> OKEClusterInfo:
            return OKEClusterInfo(
                cluster_id=cluster_id,
                name="cluster-a",
                kubernetes_version="1.32.1",
                lifecycle_state="ACTIVE",
                compartment_id=entry.compartment_ocid,
                available_upgrades=["v1.34.0", "v1.34.1"],
            )

        def upgrade_oke_cluster(self, cluster_id: str, target_version: str) -> str:
            self.calls.append((cluster_id, target_version))
            return "work-request-345"

    fake_client = FakeClient()

    monkeypatch.setattr("oke_upgrade.setup_session_token", lambda *args, **kwargs: "profile-name")  # type: ignore
    monkeypatch.setattr("oke_upgrade.create_oci_client", lambda region, profile: fake_client)  # type: ignore

    results = perform_cluster_upgrades(
        [entry],
        requested_version="1.33.1",
        dry_run=False,
        filters={},
    )

    assert fake_client.calls == [("ocid1.cluster.oc1..clusterA", "v1.34.1")]
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].skipped is False
    assert results[0].target_version == "v1.34.1"


def test_perform_cluster_upgrades_marks_skip_when_no_upgrades(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = ReportCluster(
        project="today-all",
        stage="dev",
        region="us-phoenix-1",
        cluster_name="cluster-phx",
        cluster_version="1.34.1",
        available_upgrades=[],
        compartment_ocid="ocid1.compartment.oc1..phx",
        cluster_ocid="ocid1.cluster.oc1..phx",
    )

    # The entry claims no upgrades, and OCI agrees.
    class FakeClient:
        def get_oke_cluster(self, cluster_id: str) -> OKEClusterInfo:
            return OKEClusterInfo(
                cluster_id=cluster_id,
                name="cluster",
                kubernetes_version="1.34.1",
                lifecycle_state="ACTIVE",
                compartment_id="ocid1.compartment",
                available_upgrades=[],
            )

        def upgrade_oke_cluster(self, cluster_id: str, target_version: str) -> str:
            raise AssertionError("should not attempt upgrade when no versions available")

    monkeypatch.setattr(
        "oke_upgrade.setup_session_token",
        lambda *args, **kwargs: "profile",
    )  # type: ignore
    monkeypatch.setattr(
        "oke_upgrade.create_oci_client",
        lambda region, profile: FakeClient(),
    )  # type: ignore

    results = perform_cluster_upgrades(
        [entry],
        requested_version=None,
        dry_run=False,
        filters={},
    )

    assert len(results) == 1
    assert results[0].success is True
    assert results[0].skipped is True
    assert results[0].target_version is None


def test_perform_cluster_upgrades_processes_multiple_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [
        ReportCluster(
            project="today-all",
            stage="dev",
            region="us-phoenix-1",
            cluster_name="cluster-phx",
            cluster_version="1.31.1",
            available_upgrades=["1.34.0", "1.34.1"],
            compartment_ocid="ocid1.compartment.oc1..phx",
            cluster_ocid="ocid1.cluster.oc1..phx",
        ),
        ReportCluster(
            project="today-all",
            stage="dev",
            region="us-ashburn-1",
            cluster_name="cluster-iad",
            cluster_version="1.31.1",
            available_upgrades=["1.33.0", "1.33.1"],
            compartment_ocid="ocid1.compartment.oc1..iad",
            cluster_ocid="ocid1.cluster.oc1..iad",
        ),
        ReportCluster(
            project="today-all",
            stage="dev",
            region="eu-frankfurt-1",
            cluster_name="cluster-fra",
            cluster_version="1.31.1",
            available_upgrades=["1.33.0", "1.33.1"],
            compartment_ocid="ocid1.compartment.oc1..fra",
            cluster_ocid="ocid1.cluster.oc1..fra",
        ),
    ]

    class FakeClient:
        def __init__(self, available: List[str]) -> None:
            self.available = available
            self.calls: List[Tuple[str, str]] = []

        def get_oke_cluster(self, cluster_id: str) -> OKEClusterInfo:
            return OKEClusterInfo(
                cluster_id=cluster_id,
                name="cluster",
                kubernetes_version="1.31.1",
                compartment_id="ocid1.compartment",
                lifecycle_state="ACTIVE",
                available_upgrades=self.available,
            )

        def upgrade_oke_cluster(self, cluster_id: str, target_version: str) -> str:
            self.calls.append((cluster_id, target_version))
            return f"wr-{cluster_id.split('.')[-1]}"

    fake_clients = {
        ("today-all", "dev", "us-phoenix-1"): FakeClient(["v1.34.0", "v1.34.1"]),
        ("today-all", "dev", "us-ashburn-1"): FakeClient(["v1.33.0", "v1.33.1"]),
        ("today-all", "dev", "eu-frankfurt-1"): FakeClient(["v1.33.0", "v1.33.1"]),
    }

    def fake_setup_session_token(project: str, stage: str, region: str) -> str:
        key = (project, stage, region)
        assert key in fake_clients
        return f"profile-{region}"

    def fake_create_oci_client(region: str, profile: str) -> FakeClient:
        for key, client in fake_clients.items():
            if key[2] == region:
                return client
        raise AssertionError(f"Unexpected region {region}")

    monkeypatch.setattr("oke_upgrade.setup_session_token", fake_setup_session_token)  # type: ignore
    monkeypatch.setattr("oke_upgrade.create_oci_client", fake_create_oci_client)  # type: ignore

    results = perform_cluster_upgrades(
        entries,
        requested_version="1.33.1",
        dry_run=False,
        filters={},
    )

    assert len(results) == 3
    assert all(result.success for result in results)
    assert [r.skipped for r in results] == [False, False, False]
    assert fake_clients[("today-all", "dev", "us-phoenix-1")].calls == [("ocid1.cluster.oc1..phx", "v1.34.1")]
    assert fake_clients[("today-all", "dev", "us-ashburn-1")].calls == [("ocid1.cluster.oc1..iad", "v1.33.1")]
    assert fake_clients[("today-all", "dev", "eu-frankfurt-1")].calls == [("ocid1.cluster.oc1..fra", "v1.33.1")]
