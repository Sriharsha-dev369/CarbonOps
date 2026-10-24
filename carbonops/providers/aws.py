"""AWS EC2 + CloudWatch provider. Runs against moto in the demo; unchanged against real AWS.

The endpoint comes from AWS_ENDPOINT_URL and is omitted when unset, so real AWS works as is.
"""

import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3

from carbonops import config
from carbonops.models import Action, ActionType, HealthCheck, Metrics, Resource

# Tags CarbonOps itself manages; everything else is the owner's and is shown (as untrusted data).
SYSTEM_TAGS = {'Name', 'client', 'uptime_hours_week', 'schedule', 'schedule_hours_week'}
LIVE_STATES = ['pending', 'running', 'stopping', 'stopped']
WAIT = {'Delay': 2, 'MaxAttempts': 60}


def boto_client(service: str) -> Any:
    kwargs: dict[str, Any] = {'region_name': os.environ.get('AWS_DEFAULT_REGION', 'ap-south-1')}
    if os.environ.get('AWS_ENDPOINT_URL'):
        kwargs['endpoint_url'] = os.environ['AWS_ENDPOINT_URL']
    return boto3.client(service, **kwargs)


def tag_dict(tags: list[dict] | None) -> dict[str, str]:
    return {t['Key']: t['Value'] for t in tags or []}


def tag_spec(resource_type: str, tags: dict[str, str]) -> list[dict]:
    return [{'ResourceType': resource_type, 'Tags': [{'Key': k, 'Value': v} for k, v in tags.items()]}]


def p95(values: list[float]) -> float:
    """Nearest-rank 95th percentile."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def load_sim_state() -> dict[str, dict[str, dict]]:
    path = config.state_dir() / 'sim_state.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


class AwsProvider:
    """CloudProvider for one client's estate."""

    def __init__(self, client: str) -> None:
        self.client = client
        self.ec2 = boto_client('ec2')
        self.cw = boto_client('cloudwatch')
        self.health = config.load_health()
        self._ids: dict[str, tuple[str, str]] = {}          # resource id -> ('vm' | 'volume', AWS id)
        self._last_applied: dict[str, ActionType] = {}

    # --- discovery -----------------------------------------------------------

    def list_resources(self, client: str | None = None) -> list[Resource]:
        client = client or self.client
        client_filter = [{'Name': 'tag:client', 'Values': [client]}]
        resources = []
        reservations = self.ec2.describe_instances(
            Filters=[*client_filter, {'Name': 'instance-state-name', 'Values': LIVE_STATES}])['Reservations']
        for inst in (i for r in reservations for i in r['Instances']):
            tags = tag_dict(inst.get('Tags'))
            self._ids[tags['Name']] = ('vm', inst['InstanceId'])
            resources.append(Resource(
                id=tags['Name'], client=client, kind='vm', instance_type=inst['InstanceType'],
                env=tags['env'], owner=tags.get('owner', ''), state=inst['State']['Name'],
                tags={k: v for k, v in tags.items() if k not in SYSTEM_TAGS},
            ))
        for vol in self.ec2.describe_volumes(Filters=client_filter)['Volumes']:
            tags = tag_dict(vol.get('Tags'))
            self._ids[tags['Name']] = ('volume', vol['VolumeId'])
            resources.append(Resource(
                id=tags['Name'], client=client, kind='volume', size_gb=vol['Size'], env=tags['env'],
                owner=tags.get('owner', ''), state=vol['State'], attached=bool(vol.get('Attachments')),
                tags={k: v for k, v in tags.items() if k not in SYSTEM_TAGS},
            ))
        return resources

    def _lookup(self, resource_id: str) -> tuple[str, str]:
        if resource_id not in self._ids:
            self.list_resources()
        if resource_id not in self._ids:
            raise KeyError(f'{resource_id} not found in AWS for {self.client}')
        return self._ids[resource_id]

    def set_tag(self, resource_id: str, key: str, value: str | None) -> None:
        """Set one owner tag on a VM or volume, or remove it when `value` is None (red-team demo)."""
        if key in SYSTEM_TAGS:
            raise ValueError(f'{key} is managed by CarbonOps')
        _, aws_id = self._lookup(resource_id)
        if value is None:
            self.ec2.delete_tags(Resources=[aws_id], Tags=[{'Key': key}])
        else:
            self.ec2.create_tags(Resources=[aws_id], Tags=[{'Key': key, 'Value': value}])

    def _instance(self, instance_id: str) -> dict:
        return self.ec2.describe_instances(InstanceIds=[instance_id])['Reservations'][0]['Instances'][0]

    def get_metrics(self, resource_id: str, days: int = 7) -> Metrics | None:
        kind, instance_id = self._lookup(resource_id)
        if kind != 'vm':
            return None
        now = datetime.now(timezone.utc)
        points = self.cw.get_metric_statistics(
            Namespace='AWS/EC2', MetricName='CPUUtilization',
            Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}],
            StartTime=now - timedelta(days=days + 1), EndTime=now + timedelta(minutes=1),
            Period=3600, Statistics=['Average', 'Maximum'],
        )['Datapoints']
        points = sorted(points, key=lambda p: p['Timestamp'])[-days * 24:]   # the most recent `days` of hours
        if not points:
            return None
        hourly = [p['Average'] for p in points]
        tags = tag_dict(self._instance(instance_id).get('Tags'))
        return Metrics(
            cpu_avg=round(sum(hourly) / len(hourly), 2),
            cpu_p95=round(p95(hourly), 2),
            cpu_max=round(max(p['Maximum'] for p in points), 2),
            hours_per_week=float(tags.get('uptime_hours_week', 168)),
        )

    # --- change, check, undo -------------------------------------------------

    def _snapshots(self) -> dict[str, dict]:
        path = config.state_dir() / 'snapshots.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

    def _save_snapshot(self, snapshot_id: str, record: dict) -> None:
        path = config.state_dir() / 'snapshots.json'
        data = self._snapshots()
        data[snapshot_id] = record
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding='utf-8')

    def snapshot(self, resource_id: str) -> str:
        kind, aws_id = self._lookup(resource_id)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
        tags = {'client': self.client, 'carbonops': 'restore-point', 'resource': resource_id}
        if kind == 'vm':
            inst = self._instance(aws_id)
            snapshot_id = self.ec2.create_image(
                InstanceId=aws_id, Name=f'carbonops-{resource_id}-{stamp}', NoReboot=True,
                TagSpecifications=tag_spec('image', tags))['ImageId']
            inst_tags = tag_dict(inst.get('Tags'))
            record = {'kind': 'vm', 'aws_id': aws_id, 'instance_type': inst['InstanceType'],
                      'state': inst['State']['Name'],
                      'tags': {k: inst_tags[k] for k in ('uptime_hours_week', 'schedule', 'schedule_hours_week')
                               if k in inst_tags}}
        else:
            snapshot_id = self.ec2.create_snapshot(
                VolumeId=aws_id, Description=f'CarbonOps restore point for {resource_id}',
                TagSpecifications=tag_spec('snapshot', tags))['SnapshotId']
            record = {'kind': 'volume', 'aws_id': aws_id}
        self._save_snapshot(snapshot_id, {'client': self.client, 'resource_id': resource_id, **record})
        return snapshot_id

    def _stop(self, instance_id: str) -> None:
        self.ec2.stop_instances(InstanceIds=[instance_id])
        self.ec2.get_waiter('instance_stopped').wait(InstanceIds=[instance_id], WaiterConfig=WAIT)

    def _start(self, instance_id: str) -> None:
        self.ec2.start_instances(InstanceIds=[instance_id])
        self.ec2.get_waiter('instance_running').wait(InstanceIds=[instance_id], WaiterConfig=WAIT)

    def apply(self, action: Action) -> None:
        kind, aws_id = self._lookup(action.resource_id)
        if action.type == 'KEEP':
            pass
        elif action.type == 'SNAPSHOT':
            if kind != 'volume':
                raise ValueError(f'SNAPSHOT applies to volumes, {action.resource_id} is a VM')
            self.ec2.create_snapshot(VolumeId=aws_id, Description=f'CarbonOps snapshot of {action.resource_id}',
                                     TagSpecifications=tag_spec('snapshot', {'client': self.client,
                                                                             'resource': action.resource_id}))
        elif kind != 'vm':
            raise ValueError(f'{action.type} applies to VMs, {action.resource_id} is a volume')
        elif action.type == 'STOP':
            self._stop(aws_id)
        elif action.type == 'SCHEDULE':
            hours = str(int(action.params['hours_per_week']))
            # How AWS Instance Scheduler is driven: tags on the instance.
            self.ec2.create_tags(Resources=[aws_id], Tags=[
                {'Key': 'schedule', 'Value': 'office-hours'},
                {'Key': 'schedule_hours_week', 'Value': hours},
                {'Key': 'uptime_hours_week', 'Value': hours},
            ])
        elif action.type == 'RESIZE':
            was_running = self._instance(aws_id)['State']['Name'] == 'running'
            self._stop(aws_id)
            self.ec2.modify_instance_attribute(InstanceId=aws_id, InstanceType={'Value': action.params['to']})
            if was_running:
                self._start(aws_id)
        else:
            raise ValueError(f'{action.type} is never executed by CarbonOps')
        self._last_applied[action.resource_id] = action.type

    def health_ok(self, resource_id: str) -> HealthCheck:
        sim = load_sim_state().get(self.client, {}).get(resource_id, {})
        broken = sim.get('health_fail_after') is not None and sim['health_fail_after'] == self._last_applied.get(resource_id)
        latency = self.health.unhealthy_ms if broken else self.health.healthy_ms
        self.cw.put_metric_data(Namespace=self.health.namespace, MetricData=[{
            'MetricName': self.health.metric, 'Value': latency, 'Unit': 'Milliseconds',
            'Dimensions': [{'Name': 'ResourceId', 'Value': resource_id}],
        }])
        return HealthCheck(ok=latency <= self.health.threshold_ms, latency_ms=latency,
                           threshold_ms=self.health.threshold_ms)

    def restore(self, resource_id: str, snapshot_id: str) -> None:
        record = self._snapshots().get(snapshot_id)
        if record is None:
            raise KeyError(f'no restore point {snapshot_id}')
        if record['kind'] != 'vm':
            return   # volume snapshots are non-destructive: nothing to undo
        aws_id = record['aws_id']
        inst = self._instance(aws_id)
        if inst['InstanceType'] != record['instance_type']:
            if inst['State']['Name'] != 'stopped':
                self._stop(aws_id)
            self.ec2.modify_instance_attribute(InstanceId=aws_id, InstanceType={'Value': record['instance_type']})
        state = self._instance(aws_id)['State']['Name']
        if record['state'] == 'running' and state != 'running':
            self._start(aws_id)
        elif record['state'] == 'stopped' and state != 'stopped':
            self._stop(aws_id)
        current = tag_dict(self._instance(aws_id).get('Tags'))
        added = [k for k in ('schedule', 'schedule_hours_week') if k in current and k not in record['tags']]
        if added:
            self.ec2.delete_tags(Resources=[aws_id], Tags=[{'Key': k} for k in added])
        if record['tags']:
            self.ec2.create_tags(Resources=[aws_id], Tags=[{'Key': k, 'Value': v} for k, v in record['tags'].items()])
        self._last_applied.pop(resource_id, None)
