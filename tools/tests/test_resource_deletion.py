from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from oci.exceptions import ServiceError
from rich.console import Console

from src.oci_client.resource_deletion import (
    BucketDeletionCommand,
    OKEDeletionCommand,
    ResourceDeletionError,
)


class FakeCollection:
    def __init__(self, objects=None, next_start_with=None):
        self.objects = objects or []
        self.next_start_with = next_start_with


class FakeResponse:
    def __init__(self, data):
        self.data = data


def make_console() -> Console:
    return Console(record=True)


def test_bucket_deletion_removes_versions_and_bucket():
    command = BucketDeletionCommand()
    command._max_delete_workers = 1
    object_storage = Mock()
    object_storage.get_namespace.return_value = SimpleNamespace(data="namespace")
    object_storage.get_bucket.return_value = SimpleNamespace(data=SimpleNamespace(versioning="Enabled"))

    versions_page1 = FakeResponse(
        FakeCollection(
            objects=[
                SimpleNamespace(name="file1.txt", version_id="v1"),
                SimpleNamespace(name="file2.txt", version_id="v2"),
            ],
            next_start_with="next",
        )
    )
    versions_page2 = FakeResponse(
        FakeCollection(
            objects=[SimpleNamespace(name="file3.txt", version_id="v3")],
        )
    )
    empty_versions = FakeResponse(FakeCollection(objects=[]))
    object_storage.list_object_versions.side_effect = [versions_page1, versions_page2, empty_versions]

    empty_objects = FakeResponse(FakeCollection(objects=[]))
    object_storage.list_objects.return_value = empty_objects

    client = SimpleNamespace(object_storage_client=object_storage)
    args = SimpleNamespace(bucket_name="bucket", namespace=None)

    command.execute(client, args, make_console())

    assert object_storage.delete_object.call_count == 3
    object_storage.delete_bucket.assert_called_once_with("namespace", "bucket")


def test_bucket_deletion_handles_standard_bucket_objects():
    command = BucketDeletionCommand()
    command._max_delete_workers = 1
    object_storage = Mock()
    object_storage.get_namespace.return_value = SimpleNamespace(data="namespace")
    object_storage.get_bucket.return_value = SimpleNamespace(data=SimpleNamespace(versioning="Disabled"))

    page1_objects = FakeResponse(
        FakeCollection(
            objects=[
                SimpleNamespace(name="file1.txt"),
                SimpleNamespace(name="file2.txt"),
            ],
            next_start_with="next",
        )
    )
    page2_objects = FakeResponse(FakeCollection(objects=[SimpleNamespace(name="file3.txt")]))
    empty_objects = FakeResponse(FakeCollection(objects=[]))
    object_storage.list_objects.side_effect = [page1_objects, page2_objects, empty_objects, empty_objects]

    client = SimpleNamespace(object_storage_client=object_storage)
    args = SimpleNamespace(bucket_name="bucket", namespace=None)

    command.execute(client, args, make_console())

    assert object_storage.list_object_versions.call_count == 0
    assert object_storage.delete_object.call_count == 3
    object_storage.delete_bucket.assert_called_once_with("namespace", "bucket")


def test_bucket_deletion_ignores_missing_bucket():
    command = BucketDeletionCommand()
    object_storage = Mock()
    object_storage.get_namespace.return_value = SimpleNamespace(data="namespace")
    object_storage.get_bucket.side_effect = ServiceError(
        status=404,
        code="BucketNotFound",
        headers={},
        message="Bucket missing",
    )

    client = SimpleNamespace(object_storage_client=object_storage)
    args = SimpleNamespace(bucket_name="bucket", namespace=None)

    command.execute(client, args, make_console())

    object_storage.delete_bucket.assert_not_called()


def test_bucket_deletion_surfaces_remaining_objects_error():
    command = BucketDeletionCommand()
    object_storage = Mock()
    object_storage.get_namespace.return_value = SimpleNamespace(data="namespace")
    object_storage.get_bucket.return_value = SimpleNamespace(data=SimpleNamespace(versioning="Disabled"))
    object_storage.list_objects.return_value = FakeResponse(FakeCollection(objects=[]))
    object_storage.delete_bucket.side_effect = ServiceError(
        status=409,
        code="BucketNotEmpty",
        headers={},
        message="bucket contains objects",
    )

    client = SimpleNamespace(object_storage_client=object_storage)
    args = SimpleNamespace(bucket_name="bucket", namespace=None)

    with pytest.raises(ResourceDeletionError):
        command.execute(client, args, make_console())


def test_oke_cluster_deletion_with_node_pools():
    command = OKEDeletionCommand()
    command._work_request_poll_seconds = 0
    command._work_request_max_attempts = 3

    ce_client = Mock()
    ce_client.get_cluster.return_value = SimpleNamespace(
        data=SimpleNamespace(name="cluster-name", compartment_id="compartment-id")
    )
    ce_client.list_node_pools.return_value = SimpleNamespace(
        data=[
            SimpleNamespace(id="nodepool1", name="np1"),
            SimpleNamespace(id="nodepool2", name="np2"),
        ],
        next_page=None,
    )
    ce_client.delete_node_pool.side_effect = [
        SimpleNamespace(headers={"opc-work-request-id": "wr-node-1"}),
        SimpleNamespace(headers={"opc-work-request-id": "wr-node-2"}),
    ]
    ce_client.delete_cluster.return_value = SimpleNamespace(
        headers={"opc-work-request-id": "wr-cluster"}
    )
    ce_client.get_work_request.side_effect = [
        SimpleNamespace(data=SimpleNamespace(status="SUCCEEDED")),
        SimpleNamespace(data=SimpleNamespace(status="SUCCEEDED")),
        SimpleNamespace(data=SimpleNamespace(status="SUCCEEDED")),
    ]
    ce_client.list_work_request_errors.return_value = SimpleNamespace(data=[])

    client = SimpleNamespace(container_engine_client=ce_client)
    args = SimpleNamespace(cluster_id="ocid1.cluster.oc1..example", skip_node_pools=False)

    command.execute(client, args, make_console())

    assert ce_client.delete_node_pool.call_count == 2
    ce_client.delete_cluster.assert_called_once_with("ocid1.cluster.oc1..example")


def test_oke_cluster_deletion_skips_node_pools():
    command = OKEDeletionCommand()
    command._work_request_poll_seconds = 0

    ce_client = Mock()
    ce_client.get_cluster.return_value = SimpleNamespace(
        data=SimpleNamespace(name="cluster-name", compartment_id="compartment-id")
    )
    ce_client.delete_cluster.return_value = SimpleNamespace(
        headers={"opc-work-request-id": "wr-cluster"}
    )
    ce_client.get_work_request.return_value = SimpleNamespace(
        data=SimpleNamespace(status="SUCCEEDED")
    )
    ce_client.list_work_request_errors.return_value = SimpleNamespace(data=[])

    client = SimpleNamespace(container_engine_client=ce_client)
    args = SimpleNamespace(cluster_id="ocid1.cluster.oc1..example", skip_node_pools=True)

    command.execute(client, args, make_console())

    ce_client.list_node_pools.assert_not_called()
    ce_client.delete_cluster.assert_called_once()


def test_oke_cluster_deletion_missing_cluster():
    command = OKEDeletionCommand()
    ce_client = Mock()
    ce_client.get_cluster.side_effect = ServiceError(
        status=404,
        code="NotFound",
        headers={},
        message="missing",
    )

    client = SimpleNamespace(container_engine_client=ce_client)
    args = SimpleNamespace(cluster_id="ocid1.cluster.oc1..missing", skip_node_pools=False)

    # Should not raise when cluster is already gone
    command.execute(client, args, make_console())

    ce_client.delete_cluster.assert_not_called()


def test_oke_cluster_deletion_work_request_failure():
    command = OKEDeletionCommand()
    command._work_request_poll_seconds = 0
    command._work_request_max_attempts = 2

    ce_client = Mock()
    ce_client.get_cluster.return_value = SimpleNamespace(
        data=SimpleNamespace(name="cluster-name", compartment_id="compartment-id")
    )
    ce_client.list_node_pools.return_value = SimpleNamespace(data=[], next_page=None)
    ce_client.delete_cluster.return_value = SimpleNamespace(
        headers={"opc-work-request-id": "wr-cluster"}
    )
    ce_client.get_work_request.side_effect = [
        SimpleNamespace(data=SimpleNamespace(status="FAILED")),
    ]
    ce_client.list_work_request_errors.return_value = SimpleNamespace(
        data=[SimpleNamespace(message="boom")]
    )

    client = SimpleNamespace(container_engine_client=ce_client)
    args = SimpleNamespace(cluster_id="ocid1.cluster.oc1..example", skip_node_pools=False)

    with pytest.raises(ResourceDeletionError):
        command.execute(client, args, make_console())
