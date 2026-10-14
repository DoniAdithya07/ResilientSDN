"""Replay a packet trace (CAIDA or MAWI pcap) as background traffic.

The trace's addresses are rewritten to one Mininet host pair, then tcpreplay
sends it from the source host at a fixed rate, looping for the whole run.
CAIDA traces need a CAIDA data agreement; MAWI traces are public
(mawi.wide.ad.jp). Point experiment_config.yaml `trace.file` at a pcap.
"""
import subprocess
from subprocess import DEVNULL


def replay(src, dst, pcap, mbps, seconds, work_dir):
    rewritten = work_dir / f'trace_{src.name}.pcap'
    subprocess.run(
        ['tcprewrite', f'--infile={pcap}', f'--outfile={rewritten}',
         f'--srcipmap=0.0.0.0/0:{src.IP()}/32', f'--dstipmap=0.0.0.0/0:{dst.IP()}/32',
         f'--enet-smac={src.MAC()}', f'--enet-dmac={dst.MAC()}', '--fixcsum'],
        check=True, stdout=DEVNULL, stderr=DEVNULL)
    return src.popen(['tcpreplay', '-q', '-i', src.defaultIntf().name,
                      f'--mbps={mbps}', '--loop=0', f'--duration={int(seconds)}',
                      str(rewritten)], stdout=DEVNULL, stderr=DEVNULL)
