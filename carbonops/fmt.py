"""Display formatting shared by the app, CLI and reports."""


def inr(amount: float) -> str:
    """₹ with Indian digit grouping: 1109845 -> '₹11,09,845'."""
    sign, whole = ('-' if amount < 0 else ''), str(round(abs(amount)))
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ','.join([head, *groups, tail]) if head else ','.join([*groups, tail])
    return f'{sign}₹{whole}'


def kg(amount: float) -> str:
    return f'{amount:,.1f} kg'


def action_label(action_type: str, params: dict, instance_type: str | None = None) -> str:
    """Human wording for an action, e.g. 'Resize m5.2xlarge → m5.xlarge'."""
    if action_type == 'RESIZE':
        return f"Resize {instance_type or ''} → {params.get('to', '?')}".replace('  ', ' ')
    if action_type == 'SCHEDULE':
        return f"Schedule to office hours ({params.get('hours_per_week', '?'):g} h/week)" \
            if isinstance(params.get('hours_per_week'), (int, float)) else 'Schedule to office hours'
    return {'STOP': 'Stop instance', 'SNAPSHOT': 'Snapshot volume', 'KEEP': 'Keep as is',
            'TERMINATE': 'Terminate instance', 'DELETE': 'Delete resource'}.get(action_type, action_type.title())


def evidence(kind: str, ev: dict) -> str:
    """Finding evidence in words, e.g. 'avg CPU 2%, max 9% over 7 days'."""
    if kind == 'IDLE':
        return f"avg CPU {ev.get('cpu_avg', '?'):g}%, max {ev.get('cpu_max', '?'):g}% over 7 days"
    if kind == 'ALWAYS_ON':
        return f"running {ev.get('hours_per_week', '?'):g} h/week outside production"
    if kind == 'OVERSIZED':
        return f"p95 CPU {ev.get('cpu_p95', '?'):g}% over 7 days"
    if kind == 'UNATTACHED':
        return f"{ev.get('size_gb', '?')} GB volume attached to nothing"
    return ', '.join(f'{k} {v}' for k, v in ev.items())
