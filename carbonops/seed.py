"""Seed the AWS emulator with a client's demo estate: EC2 instances, EBS volumes and 7 days of CPU metrics.

Sandbox only: refuses to run against a real AWS account.
"""

import json
import math
from datetime import datetime, timedelta, timezone

from carbonops import config
from carbonops.moto_runtime import is_sandbox
from carbonops.providers.aws import LIVE_STATES, boto_client, tag_dict, tag_spec

HOURS = 168
PEAK_SLOTS = [d * 24 + 14 for d in range(7)] + [3 * 24 + 10, 5 * 24 + 10]   # 2 pm daily + two mornings
MAX_SLOT = 3 * 24 + 14                                                     # mid-week afternoon


def cpu_series(cpu_avg: float, cpu_p95: float, cpu_max: float, hours: int = HOURS) -> list[float]:
    """Deterministic hourly CPU % whose mean, nearest-rank p95 and max equal the inputs.

    9 peak hours (1 at cpu_max, 8 at cpu_p95) put the p95 exactly at rank 160 of 168; the other
    159 hours follow a gentle day/night curve scaled so the overall mean is exact.
    """
    if hours != HOURS:
        raise ValueError('cpu_series is built for 168 hourly points')
    low_total = cpu_avg * hours - cpu_max - (len(PEAK_SLOTS) - 1) * cpu_p95
    low_slots = [i for i in range(hours) if i not in PEAK_SLOTS]
    low_mean = low_total / len(low_slots)
    if not 0 <= low_mean <= cpu_p95 <= cpu_max:
        raise ValueError(f'cannot build a series for avg={cpu_avg}, p95={cpu_p95}, max={cpu_max}')

    amplitude = min(0.5, 0.9 * (cpu_p95 / low_mean - 1)) if low_mean > 0 else 0.0
    while True:
        shape = [1 + amplitude * math.sin(2 * math.pi * ((i % 24) - 8) / 24) for i in low_slots]
        lows = [low_mean * w * len(shape) / sum(shape) for w in shape]
        if all(0 <= v <= cpu_p95 for v in lows):
            break
        amplitude /= 2

    series = [0.0] * hours
    for i, v in zip(low_slots, lows):
        series[i] = v
    for i in PEAK_SLOTS:
        series[i] = cpu_max if i == MAX_SLOT else cpu_p95
    return series


def _require_sandbox() -> None:
    if not is_sandbox():
        raise RuntimeError('seed/reset only run against the AWS emulator, never a real account.')


def _existing(client: str) -> dict[str, str]:
    ec2 = boto_client('ec2')
    found = {}
    reservations = ec2.describe_instances(Filters=[{'Name': 'tag:client', 'Values': [client]},
                                                   {'Name': 'instance-state-name', 'Values': LIVE_STATES}])
    for inst in (i for r in reservations['Reservations'] for i in r['Instances']):
        found[tag_dict(inst.get('Tags'))['Name']] = inst['InstanceId']
    for vol in ec2.describe_volumes(Filters=[{'Name': 'tag:client', 'Values': [client]}])['Volumes']:
        found[tag_dict(vol.get('Tags'))['Name']] = vol['VolumeId']
    return found


def _put_cpu(cw, instance_id: str, series: list[float]) -> None:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    data = [{'MetricName': 'CPUUtilization', 'Dimensions': [{'Name': 'InstanceId', 'Value': instance_id}],
             'Timestamp': now - timedelta(hours=len(series) - 1 - i), 'Value': round(v, 4), 'Unit': 'Percent'}
            for i, v in enumerate(series)]
    for start in range(0, len(data), 100):
        cw.put_metric_data(Namespace='AWS/EC2', MetricData=data[start:start + 100])


def _write_sim_state(client: str, sims: dict[str, dict]) -> None:
    path = config.state_dir() / 'sim_state.json'
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    state[client] = sims
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding='utf-8')


def seed(client: str) -> dict[str, str]:
    """Create the client's estate in the emulator (skipping anything already there); return resource id -> AWS id.

    Tags: Name=<id>, client, env, owner, the estate's extra tags, and uptime_hours_week on VMs
    (AWS Instance Scheduler conventions). Sim state goes to sim_state.json, never to tags.
    """
    _require_sandbox()
    resources, metrics, sims = config.load_estate(client)
    ec2, cw = boto_client('ec2'), boto_client('cloudwatch')
    ids = _existing(client)
    region = ec2.meta.region_name
    image_id = None

    for r in resources:
        if r.id in ids:
            continue
        tags = {'Name': r.id, 'client': client, **r.tags}
        if r.kind == 'vm':
            m = metrics[r.id]
            tags['uptime_hours_week'] = str(int(m.hours_per_week))
            image_id = image_id or ec2.describe_images()['Images'][0]['ImageId']
            ids[r.id] = ec2.run_instances(ImageId=image_id, InstanceType=r.instance_type, MinCount=1, MaxCount=1,
                                          TagSpecifications=tag_spec('instance', tags))['Instances'][0]['InstanceId']
            _put_cpu(cw, ids[r.id], cpu_series(m.cpu_avg, m.cpu_p95, m.cpu_max))
        else:
            ids[r.id] = ec2.create_volume(AvailabilityZone=f'{region}a', Size=r.size_gb, VolumeType='gp3',
                                          TagSpecifications=tag_spec('volume', tags))['VolumeId']
    _write_sim_state(client, sims)
    return ids


def reset(client: str) -> dict[str, str]:
    """Terminate/delete everything tagged with this client (instances, volumes, restore points), then seed again."""
    _require_sandbox()
    ec2 = boto_client('ec2')
    client_filter = [{'Name': 'tag:client', 'Values': [client]}]
    instances = [i['InstanceId'] for r in ec2.describe_instances(
        Filters=[*client_filter, {'Name': 'instance-state-name', 'Values': LIVE_STATES}])['Reservations']
        for i in r['Instances']]
    if instances:
        ec2.terminate_instances(InstanceIds=instances)
    for vol in ec2.describe_volumes(Filters=client_filter)['Volumes']:
        ec2.delete_volume(VolumeId=vol['VolumeId'])
    for image in ec2.describe_images(Owners=['self'], Filters=client_filter)['Images']:
        ec2.deregister_image(ImageId=image['ImageId'])
    for snap in ec2.describe_snapshots(OwnerIds=['self'], Filters=client_filter)['Snapshots']:
        ec2.delete_snapshot(SnapshotId=snap['SnapshotId'])

    path = config.state_dir() / 'snapshots.json'
    if path.exists():
        kept = {k: v for k, v in json.loads(path.read_text(encoding='utf-8')).items() if v.get('client') != client}
        path.write_text(json.dumps(kept, indent=2), encoding='utf-8')
    return seed(client)
