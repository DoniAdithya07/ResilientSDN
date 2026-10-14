"""Baseline C: interface for QoSentry (DDQN-based rerouting).

QoSentry's trained model is not part of this project. To compare against it,
set QOSENTRY_POLICY to "module:function", where function(state) returns
{flow_id: switch_path} for the flows to move. state = {
    'link_stats': {(a, b): {...}}, 'flows': {flow_id: rate}, 'paths': {flow_id: path}}.
Without a policy the app routes like static routing and logs a warning, so its
results must not be reported as QoSentry.

Run: QOSENTRY_POLICY=pkg.mod:act ryu-manager baselines/qosentry_stub.py
"""
import importlib
import os

from controller.routing_base import RoutingBase


class QoSentry(RoutingBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        spec = os.environ.get('QOSENTRY_POLICY')
        self.policy = None
        if spec:
            module, func = spec.split(':')
            self.policy = getattr(importlib.import_module(module), func)
        else:
            self.logger.warning('QOSENTRY_POLICY not set: behaving as static routing')
        self.log_event('config', policy=spec)

    def on_interval(self, link_stats):
        if self.policy is None:
            return
        state = {'link_stats': link_stats,
                 'flows': {f: self.monitor.get_flow_rate(f) for f in self.current_paths},
                 'paths': {f: self.switch_path(f) for f in self.current_paths}}
        for flow_id, path in (self.policy(state) or {}).items():
            if flow_id in self.flows and path != self.switch_path(flow_id):
                self.set_path(self.flows[flow_id], path, reason='qosentry policy')
