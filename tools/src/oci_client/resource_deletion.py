"""
Infrastructure for reusable OCI resource deletion commands.

The design centers around composable deletion command classes that register
their own CLI arguments while sharing the common setup for OCI authentication.
"""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List, Optional

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

    _delete_batch_size = 1000
    _max_delete_workers = 8

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
                console=console,
            )
        except ServiceError as exc:
            raise ResourceDeletionError(
                f"Failed while emptying bucket '{bucket_name}': {exc.code} - {exc.message}"
            ) from exc

        console.print(
            f"[green]Removed {counts.deleted_objects} objects and {counts.deleted_versions} versions.[/green]"
        )

        console.print(f"[dim]Deleting bucket resource '{bucket_name}'...[/dim]")
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
        console: Console,
    ) -> None:
        """Iterate through bucket contents and delete them safely."""
        versioning_enabled = versioning_state in {"enabled", "suspended"}

        if versioning_enabled:
            self._delete_object_versions(
                object_storage=object_storage,
                namespace=namespace,
                bucket_name=bucket_name,
                counts=counts,
                console=console,
            )
        else:
            self._delete_current_objects(
                object_storage=object_storage,
                namespace=namespace,
                bucket_name=bucket_name,
                counts=counts,
                console=console,
            )

        # Ensure no residual current objects remain (handles versioning buckets too).
        self._delete_current_objects(
            object_storage=object_storage,
            namespace=namespace,
            bucket_name=bucket_name,
            counts=counts,
            console=console,
        )

    def _delete_current_objects(
        self,
        *,
        object_storage: oci.object_storage.ObjectStorageClient,
        namespace: str,
        bucket_name: str,
        counts: _DeletionCounts,
        console: Console,
        start: Optional[str] = None,
    ) -> None:
        """Remove each current object version from the bucket."""
        next_start = start
        pending: List[Dict[str, Optional[str]]] = []

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
                object_name = getattr(obj, "name", getattr(obj, "object_name", ""))
                pending.append({"object_name": object_name})

                if len(pending) >= self._delete_batch_size:
                    self._process_delete_batch(
                        object_storage=object_storage,
                        namespace=namespace,
                        bucket_name=bucket_name,
                        items=pending,
                        console=console,
                        counts=counts,
                        is_version_batch=False,
                    )

            next_start = getattr(object_collection, "next_start_with", None)
            if not next_start:
                break

        self._process_delete_batch(
            object_storage=object_storage,
            namespace=namespace,
            bucket_name=bucket_name,
            items=pending,
            console=console,
            counts=counts,
            is_version_batch=False,
        )

    def _delete_object_versions(
        self,
        *,
        object_storage: oci.object_storage.ObjectStorageClient,
        namespace: str,
        bucket_name: str,
        counts: _DeletionCounts,
        console: Console,
    ) -> None:
        """Remove all versions from a versioned bucket."""
        next_start: Optional[str] = None
        pending: List[Dict[str, Optional[str]]] = []

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
                pending.append({"object_name": object_name, "version_id": version_id})

                if len(pending) >= self._delete_batch_size:
                    self._process_delete_batch(
                        object_storage=object_storage,
                        namespace=namespace,
                        bucket_name=bucket_name,
                        items=pending,
                        console=console,
                        counts=counts,
                        is_version_batch=True,
                    )

            next_start = getattr(version_collection, "next_start_with", None)
            if not next_start:
                break

        self._process_delete_batch(
            object_storage=object_storage,
            namespace=namespace,
            bucket_name=bucket_name,
            items=pending,
            console=console,
            counts=counts,
            is_version_batch=True,
        )

    def _process_delete_batch(
        self,
        *,
        object_storage: oci.object_storage.ObjectStorageClient,
        namespace: str,
        bucket_name: str,
        items: List[Dict[str, Optional[str]]],
        console: Console,
        counts: _DeletionCounts,
        is_version_batch: bool,
    ) -> None:
        """Flush pending objects using concurrent delete requests."""
        if not items:
            return

        action = "object versions" if is_version_batch else "objects"
        console.print(
            f"[dim]Deleting batch of {len(items)} {action} from bucket '{bucket_name}'[/dim]"
        )

        batch = list(items)

        errors: List[str] = []
        max_workers = min(self._max_delete_workers, len(batch))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    self._delete_single_object,
                    object_storage,
                    namespace,
                    bucket_name,
                    item,
                    console,
                ): item
                for item in batch
            }

            for future in as_completed(future_map):
                item = future_map[future]
                try:
                    future.result()
                except ServiceError as exc:
                    errors.append(
                        f"{item['object_name']}: {exc.code} - {exc.message}"
                    )
                except Exception as exc:  # pragma: no cover - unexpected
                    errors.append(f"{item['object_name']}: {exc}")

        if errors:
            raise ResourceDeletionError(
                f"Failed to delete {len(errors)} {action}: {errors[0]}"
            )

        if is_version_batch:
            counts.deleted_versions += len(batch)
        else:
            counts.deleted_objects += len(batch)

        items.clear()

    def _delete_single_object(
        self,
        object_storage: oci.object_storage.ObjectStorageClient,
        namespace: str,
        bucket_name: str,
        item: Dict[str, Optional[str]],
        console: Console,
    ) -> None:
        """Delete a single object or object version."""
        object_name = item["object_name"]
        version_id = item.get("version_id")

        if version_id:
            console.print(
                f"[dim]Deleting object '{object_name}' (version '{version_id}')[/dim]"
            )
            object_storage.delete_object(
                namespace_name=namespace,
                bucket_name=bucket_name,
                object_name=object_name,
                version_id=version_id,
            )
        else:
            console.print(f"[dim]Deleting object '{object_name}'[/dim]")
            object_storage.delete_object(
                namespace_name=namespace,
                bucket_name=bucket_name,
                object_name=object_name,
            )

def get_deletion_commands() -> List[BaseDeletionCommand]:
    """Return the list of registered deletion commands."""
    return [BucketDeletionCommand()]
