"""What the executor needs from a cloud. Resources are addressed by CarbonOps resource id."""

from typing import Protocol, runtime_checkable

from carbonops.models import Action


@runtime_checkable
class Provider(Protocol):
    def snapshot(self, resource_id: str) -> str:
        """Record enough state to undo a change (instance type, run state; EBS snapshot for volumes).

        Returns a snapshot id that `restore` accepts.
        """
        ...

    def apply(self, action: Action) -> None:
        """Perform STOP / SCHEDULE / RESIZE / SNAPSHOT on `action.resource_id`."""
        ...

    def health_ok(self, action: Action) -> bool:
        """Post-change health check for the action's resource. Demo `sim` settings are read here."""
        ...

    def restore(self, resource_id: str, snapshot_id: str) -> None:
        """Return the resource to the state recorded by `snapshot`."""
        ...
