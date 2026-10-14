"""Adversarial attack patterns S7-S9 (S10 is resolved by the runner)."""
import time

ALT_PORT_OFFSET = 50   # second server port per attack flow (pulses)
PRELOAD_PORT_OFFSET = 100
PRELOAD_LEAD_S = 6     # S9: seconds of preload before the attack starts


def per_flow_mbps(total_pct, target_mbps, legit_mbps, n_flows):
    """Rate per attack flow so the target link reaches total_pct utilisation."""
    return max(0.0, total_pct / 100 * target_mbps - legit_mbps) / max(1, n_flows)


def run(attacker, s, plan, ports, seconds, legit_mbps):
    pairs, target = plan['attack'], plan['target_mbps']
    stop = attacker.stop_event
    end = time.time() + seconds

    if s['pattern'] == 'pulse':
        def pulse():
            # Alternate server ports: back-to-back iperf3 tests on one port
            # can hit "server busy" while the previous test is closing.
            high = True
            while time.time() < end and not stop.is_set():
                pct = s['high'] if high else s['low']
                use = ports if high else [p + ALT_PORT_OFFSET for p in ports]
                attacker.flows(pairs, use, per_flow_mbps(pct, target, legit_mbps, len(pairs)),
                               s['interval'])
                stop.wait(s['interval'])
                high = not high
        attacker.in_background(pulse)

    elif s['pattern'] == 'timed':
        def timed():
            rate = per_flow_mbps(s['rate_pct'], target, legit_mbps, len(pairs))
            while time.time() < end and not stop.is_set():
                attacker.flows(pairs, ports, rate, s['burst'])
                stop.wait(s['interval'])
        attacker.in_background(timed)

    elif s['pattern'] == 'presaturate':
        preload = plan['preload']
        preload_ports = [ports[0] + PRELOAD_PORT_OFFSET + i for i in range(len(preload))]
        for (src, dst, link_mbps, _), port in zip(preload, preload_ports):
            attacker.flows([(src, dst)], [port], s['preload_alt_paths'] * link_mbps, seconds)
        def attack_after_preload():  # let the preload settle in the stats first
            if not stop.wait(PRELOAD_LEAD_S):
                attacker.flows(pairs, ports,
                               per_flow_mbps(s['attack_pct'], target, legit_mbps, len(pairs)),
                               seconds - PRELOAD_LEAD_S)
        attacker.in_background(attack_after_preload)
