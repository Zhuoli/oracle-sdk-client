from pathlib import Path
from typing import List, Tuple

import pytest

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
        "<td>1.33.1, 1.34.1</td>"
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
    assert fake_client.calls == [("ocid1.cluster.oc1..clusterA", "1.34.1")]
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].work_request_id == "work-request-123"
