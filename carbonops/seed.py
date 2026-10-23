"""Seed moto_server with a demo estate so the provider has real EC2/EBS objects. STUB: written on the day."""

from carbonops.models import Resource


def seed(
    resources: list[Resource],
    endpoint_url: str | None = None,
    region: str | None = None,
) -> dict[str, str]:
    """Create one EC2 instance per vm and one EBS volume per volume; return resource id -> AWS id.

    Endpoint and region default to env AWS_ENDPOINT_URL / AWS_DEFAULT_REGION.
    VM: run_instances with resource.instance_type, any image moto lists (describe_images).
    Volume: create_volume with size_gb, left unattached when resource.attached is False.
    Tags: resource.tags plus Name=<resource id>. Never write `sim` settings as tags.
    Idempotent: if an object tagged Name=<resource id> exists, reuse it instead of creating another.
    """
    raise NotImplementedError
