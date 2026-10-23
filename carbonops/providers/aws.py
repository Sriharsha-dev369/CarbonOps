"""AWS EC2/EBS provider, run against moto_server in the demo. STUB: written on the day."""

from carbonops.models import Action


class AwsProvider:
    """Implements providers.base.Provider with boto3.

    Construct with the sim settings from config.load_estate and the AWS ids from seed.seed.
    Endpoint and region come from env AWS_ENDPOINT_URL / AWS_DEFAULT_REGION when not given.
    """

    def __init__(
        self,
        aws_ids: dict[str, str],
        sims: dict[str, dict],
        endpoint_url: str | None = None,
        region: str | None = None,
    ) -> None:
        """Keep the arguments and create an EC2 boto3 client.

        aws_ids: CarbonOps resource id -> EC2 instance id / EBS volume id.
        sims:    resource id -> demo-only settings; never written as AWS tags.
        """
        raise NotImplementedError

    def snapshot(self, resource_id: str) -> str:
        """VM: remember instance type and run state, return a local snapshot id.
        Volume: create_snapshot and return the EBS snapshot id.
        """
        raise NotImplementedError

    def apply(self, action: Action) -> None:
        """STOP -> stop_instances. SCHEDULE -> stop now and tag `schedule` with params['hours_per_week'].
        RESIZE -> stop, modify_instance_attribute(InstanceType=params['to']), start.
        SNAPSHOT -> create_snapshot of the volume. Anything else -> ValueError.
        """
        raise NotImplementedError

    def health_ok(self, action: Action) -> bool:
        """False if sims[action.resource_id]['health_fail_after'] == action.type
        (staging-api fails after RESIZE); otherwise True if the resource is in the expected state.
        """
        raise NotImplementedError

    def restore(self, resource_id: str, snapshot_id: str) -> None:
        """Put back the instance type and run state recorded by `snapshot`."""
        raise NotImplementedError
