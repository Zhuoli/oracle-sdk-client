"""
Infrastructure for reusable OCI resource deletion commands.

The design centers around composable deletion command classes that register
their own CLI arguments while sharing the common setup for OCI authentication.
"""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable, List, Optional

import oci
from oci.exceptions import ServiceError
from rich.console import Console

from .client import OCIClient


class ResourceDeletionError(RuntimeError):
    """Raised when a resource cannot be deleted safely."""


class BaseDeletionCommand(ABC):
    """Abstract base class for resource deletion implementations."""

    name: str
    help_text: str

    def register(self, subparsers: argparse._SubParsersAction) -> None:
        """Attach this command to the provided subparser collection."""
        parser = subparsers.add_parser(self.name, help=self.help_text)
        self.add_arguments(parser)
        parser.set_defaults(handler=self)

    @abstractmethod
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Register CLI arguments specific to this resource type."""

    @abstractmethod
    def execute(self, client: OCIClient, args: argparse.Namespace, console: Console) -> None:
        """Perform the resource deletion."""


@dataclass
class _DeletionCounts:
    deleted_objects: int = 0
    deleted_versions: int = 0


class BucketDeletionCommand(BaseDeletionCommand):
    """Delete Object Storage buckets, draining contents beforehand."""

    name = "bucket"
    help_text = "Delete an Object Storage bucket (removing all objects and versions first)."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--bucket-name",
            required=True,
            help="Name of the Object Storage bucket to delete.",
        )
        parser.add_argument(
            "--namespace",
            required=False,
            help="Optional Object Storage namespace override (defaults to tenancy namespace).",
        )

    def execute(self, client: OCIClient, args: argparse.Namespace, console: Console) -> None:
        bucket_name: str = args.bucket_name
        namespace: Optional[str] = args.namespace

        object_storage = client.object_storage_client

        # Resolve namespace if not provided explicitly.
        if not namespace:
            namespace = object_storage.get_namespace().data

        console.print(
            f"[bold blue]Deleting bucket '{bucket_name}' in namespace '{namespace}'[/bold blue]"
        )

        try:
            bucket = object_storage.get_bucket(namespace, bucket_name).data
        except ServiceError as exc:
            if exc.status == 404:
                console.print(
                    f"[yellow]Bucket '{bucket_name}' was not found in namespace '{namespace}'. Nothing to delete.[/yellow]"
                )
                return
            raise ResourceDeletionError(
                f"Failed to look up bucket '{bucket_name}': {exc.message}"
            ) from exc

        console.print(f"[dim]Bucket versioning state: {bucket.versioning or 'Disabled'}[/dim]")

        counts = _DeletionCounts()

        try:
            self._remove_bucket_contents(
                object_storage=object_storage,
                namespace=namespace,
                bucket_name=bucket_name,
                versioning_state=str(bucket.versioning or "").lower(),
                counts=counts,
            )
        except ServiceError as exc:
            raise ResourceDeletionError(
                f"Failed while emptying bucket '{bucket_name}': {exc.code} - {exc.message}"
            ) from exc

        console.print(
            f"[green]Removed {counts.deleted_objects} objects and {counts.deleted_versions} versions.[/green]"
        )

        try:
            object_storage.delete_bucket(namespace, bucket_name)
        except ServiceError as exc:
            if exc.status == 404:
                console.print(
                    f"[yellow]Bucket '{bucket_name}' already deleted during cleanup.[/yellow]"
                )
                return
            if exc.status == 409 and exc.code == "BucketNotEmpty":
                raise ResourceDeletionError(
                    f"Bucket '{bucket_name}' is still reported as not empty. "
                    "Verify no new objects were uploaded and retry."
                ) from exc
            raise ResourceDeletionError(
                f"Failed to delete bucket '{bucket_name}': {exc.code} - {exc.message}"
            ) from exc

        console.print(f"[bold green]✓ Bucket '{bucket_name}' deleted successfully.[/bold green]")

    def _remove_bucket_contents(
        self,
        *,
        object_storage: oci.object_storage.ObjectStorageClient,
        namespace: str,
        bucket_name: str,
        versioning_state: str,
        counts: _DeletionCounts,
    ) -> None:
        """Iterate through bucket contents and delete them safely."""
        versioning_enabled = versioning_state in {"enabled", "suspended"}

        if versioning_enabled:
            self._delete_object_versions(
                object_storage=object_storage,
                namespace=namespace,
                bucket_name=bucket_name,
                counts=counts,
            )
        else:
            self._delete_current_objects(
                object_storage=object_storage,
                namespace=namespace,
                bucket_name=bucket_name,
                counts=counts,
            )

        # Ensure no residual current objects remain (handles versioning buckets too).
        self._delete_current_objects(
            object_storage=object_storage,
            namespace=namespace,
            bucket_name=bucket_name,
            counts=counts,
        )

    def _delete_current_objects(
        self,
        *,
        object_storage: oci.object_storage.ObjectStorageClient,
        namespace: str,
        bucket_name: str,
        counts: _DeletionCounts,
        start: Optional[str] = None,
    ) -> None:
        """Remove each current object version from the bucket."""
        next_start = start

        while True:
            response = object_storage.list_objects(
                namespace,
                bucket_name,
                start=next_start,
                limit=1000,
            )
            object_collection = response.data
            objects: Iterable = getattr(object_collection, "objects", [])

            if not objects:
                break

            for obj in objects:
                object_storage.delete_object(
                    namespace_name=namespace,
                    bucket_name=bucket_name,
                    object_name=getattr(obj, "name", getattr(obj, "object_name", "")),
                )
                counts.deleted_objects += 1

            next_start = getattr(object_collection, "next_start_with", None)
            if not next_start:
                break

    def _delete_object_versions(
        self,
        *,
        object_storage: oci.object_storage.ObjectStorageClient,
        namespace: str,
        bucket_name: str,
        counts: _DeletionCounts,
    ) -> None:
        """Remove all versions from a versioned bucket."""
        next_start: Optional[str] = None

        while True:
            response = object_storage.list_object_versions(
                namespace,
                bucket_name,
                start=next_start,
                limit=1000,
            )
            version_collection = response.data
            versions: List = getattr(version_collection, "objects", []) or []

            if not versions:
                break

            for version in versions:
                object_name = getattr(version, "name", getattr(version, "object_name", ""))
                version_id = getattr(version, "version_id", None)

                delete_kwargs = {
                    "namespace_name": namespace,
                    "bucket_name": bucket_name,
                    "object_name": object_name,
                }
                if version_id:
                    delete_kwargs["version_id"] = version_id

                object_storage.delete_object(**delete_kwargs)
                counts.deleted_versions += 1

            next_start = getattr(version_collection, "next_start_with", None)
            if not next_start:
                break


def get_deletion_commands() -> List[BaseDeletionCommand]:
    """Return the list of registered deletion commands."""
    return [BucketDeletionCommand()]
