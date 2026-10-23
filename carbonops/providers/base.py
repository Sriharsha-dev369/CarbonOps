"""What CarbonOps needs from a cloud. Resources are addressed by CarbonOps resource id (the `Name` tag)."""

from typing import Protocol, runtime_checkable

from carbonops.models import Action, HealthCheck, Metrics, Resource


@runtime_checkable
class CloudProvider(Protocol):
    def list_resources(self, client: str) -> list[Resource]:
        """Every live VM and volume tagged with this client."""
        ...

    def get_metrics(self, resource_id: str, days: int = 7) -> Metrics | None:
        """Hourly CPU over the last `days` days; None if the VM has no datapoints."""
        ...

    def snapshot(self, resource_id: str) -> str:
        """Record enough state to undo a change; return an id that `restore` accepts."""
        ...

    def apply(self, action: Action) -> None:
        """Perform STOP / SCHEDULE / RESIZE / SNAPSHOT / KEEP. Never deletes anything."""
        ...

    def health_ok(self, resource_id: str) -> HealthCheck:
        """Post-change health check of the resource, against the configured latency threshold."""
        ...

    def restore(self, resource_id: str, snapshot_id: str) -> None:
        """Return the resource to the state recorded by `snapshot`."""
        ...
