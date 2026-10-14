"""Legitimate traffic: iperf3 UDP flows (throughput + loss) and ping (latency).
Also the iperf3 helpers the attack code uses. Runs inside the Mininet process.
"""
import threading
import time
from subprocess import DEVNULL


def start_server(host, port):
    return host.popen(['iperf3', '-s', '-p', str(port)], stdout=DEVNULL, stderr=DEVNULL)


def udp_flow(src, dst, port, mbps, seconds, json_log=None):
    cmd = ['iperf3', '-c', dst.IP(), '-p', str(port), '-u',
           '-b', f'{mbps:.3f}M', '-t', str(max(1, round(seconds)))]
    if json_log:
        cmd += ['-J', '--logfile', str(json_log)]
    return src.popen(cmd, stdout=DEVNULL, stderr=DEVNULL)


class LegitimateTraffic:
    def __init__(self, net, run_dir):
        self.net = net
        self.run_dir = run_dir
        self.stop_event = threading.Event()

    def start(self, pairs, mbps, seconds, tag, ports, ping=True):
        """One UDP flow per (src, dst) pair; results in <tag>_<i>.json / _ping.txt."""
        for i, ((s, d), port) in enumerate(zip(pairs, ports)):
            src, dst = self.net.get(s), self.net.get(d)
            udp_flow(src, dst, port, mbps, seconds, self.run_dir / f'{tag}_{i}.json')
            if ping:
                # no shell: a space in the run dir path would break `> {out}`
                with open(self.run_dir / f'{tag}_{i}_ping.txt', 'w') as out:
                    src.popen(['ping', '-i', '0.2', '-w', str(int(seconds)), dst.IP()],
                              stdout=out, stderr=DEVNULL)

    def start_bursty(self, pairs, burst_mbps, burst_s, period_s, seconds, ports):
        """Short bursts (for Ablation D): above threshold for fewer than N polls."""
        def run():
            end = time.time() + seconds
            while time.time() < end and not self.stop_event.is_set():
                for (s, d), port in zip(pairs, ports):
                    udp_flow(self.net.get(s), self.net.get(d), port, burst_mbps, burst_s)
                self.stop_event.wait(period_s)
        threading.Thread(target=run, daemon=True).start()

    def stop(self):
        self.stop_event.set()
