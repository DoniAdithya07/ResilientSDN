"""Shared figure style for the paper. Validated with the dataviz palette
checker on a white surface (all hard checks pass; slots 3-5 are below 3:1
contrast, so identity never relies on colour alone: every figure has a legend
or system names on an axis)."""
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Colour follows the system, never its position in a chart.
SYSTEM_COLORS = {
    'resilient_sdn': '#2a78d6',          # blue
    'simple_reactive': '#eb6834',        # orange
    'static': '#1baf7a',                 # aqua
    'qosentry': '#eda100',               # yellow
    'rsdn_relief_bypass': '#7a3fb5',     # purple
    'rsdn_capacity_only': '#e87ba4',     # magenta
    'rsdn_no_hysteresis': '#008300',     # green
    'rsdn_shortest': '#4a3aa7',          # violet
    'rsdn_single_threshold': '#e34948',  # red
}
SYSTEM_LABELS = {
    'resilient_sdn': 'ResilientSDN', 'simple_reactive': 'Simple reactive',
    'static': 'Static', 'qosentry': 'QoSentry', 'rsdn_relief_bypass': 'Relief bypass (experimental)',
    'rsdn_capacity_only': 'Capacity filter only', 'rsdn_no_hysteresis': 'No hysteresis',
    'rsdn_shortest': 'Shortest alternate', 'rsdn_single_threshold': 'Single threshold',
}
SYSTEM_ORDER = list(SYSTEM_COLORS)
TEXT, TEXT_2, GRID = '#0b0b0b', '#52514e', '#e6e5e1'


def setup():
    plt.rcParams.update({
        'figure.figsize': (3.5, 2.4),        # IEEE single column
        'figure.dpi': 150, 'savefig.dpi': 300, 'savefig.bbox': 'tight',
        'font.size': 8, 'axes.titlesize': 8, 'axes.labelsize': 8,
        'legend.fontsize': 7, 'xtick.labelsize': 7, 'ytick.labelsize': 7,
        'text.color': TEXT, 'axes.labelcolor': TEXT_2,
        'xtick.color': TEXT_2, 'ytick.color': TEXT_2,
        'axes.edgecolor': GRID, 'axes.spines.top': False, 'axes.spines.right': False,
        'axes.grid': True, 'axes.grid.axis': 'y', 'grid.color': GRID, 'grid.linewidth': 0.6,
        'axes.axisbelow': True, 'lines.linewidth': 2, 'lines.markersize': 5,
        'legend.frameon': False, 'pdf.fonttype': 42,
    })


def systems_in(names):
    return sorted(set(names), key=lambda s: SYSTEM_ORDER.index(s)
                  if s in SYSTEM_ORDER else len(SYSTEM_ORDER))


def save(fig, out_dir, name):
    out_dir.mkdir(parents=True, exist_ok=True)
    for ext in ('pdf', 'png'):
        fig.savefig(out_dir / f'{name}.{ext}')
    plt.close(fig)
