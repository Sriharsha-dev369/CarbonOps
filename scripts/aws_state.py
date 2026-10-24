"""Show a client's live AWS state straight from the AWS API, not from CarbonOps, to check what it really changed.

Usage: uv run python scripts/aws_state.py [client_a]
The app must be running (./run.sh): it hosts the AWS emulator.
"""

import os
import sys

import boto3
from botocore.exceptions import EndpointConnectionError
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'testing')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'testing')
client = sys.argv[1] if len(sys.argv) > 1 else 'client_a'
endpoint = os.environ.get('AWS_ENDPOINT_URL', 'http://localhost:5050')
ec2 = boto3.client('ec2', endpoint_url=endpoint, region_name=os.environ.get('AWS_DEFAULT_REGION', 'ap-south-1'))
mine = [{'Name': 'tag:client', 'Values': [client]}]


def tags(item: dict) -> dict[str, str]:
    return {t['Key']: t['Value'] for t in item.get('Tags', [])}


try:
    reservations = ec2.describe_instances(
        Filters=[*mine, {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']}])
except EndpointConnectionError:
    sys.exit(f'No AWS at {endpoint}. Start the app first (./run.sh): it hosts the AWS emulator.')

print(f'{client}: read directly from the AWS API at {endpoint}\n')
print(f"{'instance':16} {'env':8} {'type':11} {'state':8} {'schedule':13} note tag")
for inst in sorted((i for r in reservations['Reservations'] for i in r['Instances']), key=lambda i: tags(i)['Name']):
    t = tags(inst)
    print(f"{t['Name']:16} {t.get('env', ''):8} {inst['InstanceType']:11} {inst['State']['Name']:8} "
          f"{t.get('schedule', '-'):13} {t.get('note', '')[:60]}")
for vol in ec2.describe_volumes(Filters=mine)['Volumes']:
    t = tags(vol)
    print(f"{t['Name']:16} {t.get('env', ''):8} {str(vol['Size']) + ' GB':11} "
          f"{'attached' if vol.get('Attachments') else 'detached':8}")
images = ec2.describe_images(Owners=['self'], Filters=mine)['Images']
snapshots = ec2.describe_snapshots(OwnerIds=['self'], Filters=mine)['Snapshots']
print(f'\nRestore points taken before changes: {len(images)} machine images, {len(snapshots)} volume snapshots')
