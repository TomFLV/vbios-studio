"""The Simple tab's logic, kept free of Qt: which settings get a slider, their ranges, the supporting limits that
follow them, and the warnings shown as a value moves. Everything returns stored units (as the editor API takes them);
the editor still validates every change."""
from __future__ import annotations
import math
import re
from dataclasses import dataclass
from typing import Callable

CONNECTOR = re.compile(r'connector_(\d+)_power_(max|rated)_w')
OK, INFO, WARN, DANGER = 'ok', 'info', 'warn', 'danger'


@dataclass
class Control:
    key: str                     # the field the slider sets
    title: str
    blurb: str                   # one line under the title
    unit: str
    lo: float
    hi: float
    step: float
    stock: float
    follow: Callable[[dict, float], dict]     # fields, value -> {key: stored value} that follow this one
    warn: Callable[[dict, float], tuple]      # fields, value -> (level, text)


def _num(field, key='current'):
    try:
        return float(field[key])
    except (KeyError, TypeError, ValueError):
        return None


def _editable(fields, key):
    f = fields.get(key)
    return f is not None and f.get('editable') and _num(f) is not None


def _up(value, step):
    return math.ceil(value / step - 1e-9) * step


def _fmt(v):
    return str(int(v)) if float(v).is_integer() else f'{v:g}'


# ---------------------------------------------------------------- supporting limits

def follow_power(fields, watts):
    """Limits that would cap the card below a raised board power ceiling, raised just enough (never lowered):
    the auxiliary connector channels (shared evenly), the 12 V input current thresholds (+5 % headroom) and the
    NVVDD output power threshold (same share of board power as stock). A lowered ceiling also lowers the boot
    default if that would sit above it."""
    out = {}
    stock = _num(fields['power_max_w'])
    channels = sorted({m.group(1) for k in fields if (m := CONNECTOR.fullmatch(k)) and _editable(fields, k)})
    if channels and watts > stock:
        per = _up(watts / len(channels), 5)
        for n in channels:
            for side in ('max', 'rated'):
                k = f'connector_{n}_power_{side}_w'
                if _editable(fields, k) and _num(fields[k]) < per:
                    out[k] = per
    if watts > stock:
        need_ma = _up(watts / 12 * 1.05, 0.5) * 1000
        for k in ('input_current_max_ma', 'input_current_rated_ma'):
            if _editable(fields, k) and _num(fields[k]) < need_ma:
                out[k] = need_ma
        rated = fields.get('nvvdd_power_threshold_rated_w')
        if rated and _editable(fields, 'nvvdd_power_threshold_rated_w') and stock:
            need = round(_up(watts * _num(rated) / stock, 0.5), 1)
            for k in ('nvvdd_power_threshold_max_w', 'nvvdd_power_threshold_rated_w'):
                if _editable(fields, k) and _num(fields[k]) < need:
                    out[k] = need
    default = fields.get('power_default_w')
    if default and _editable(fields, 'power_default_w') and _num(default) > watts:
        out['power_default_w'] = watts
    return {k: _fmt(v) for k, v in out.items()}


def follow_voltage(fields, mv):
    """The ALT equation ceiling follows a raised REL ceiling, so the two limits do not cap each other."""
    if _editable(fields, 'nvvdd_alt_equation_max_mv') and _num(fields['nvvdd_alt_equation_max_mv']) < mv:
        return {'nvvdd_alt_equation_max_mv': _fmt(mv)}
    return {}


def follow_nothing(fields, value):
    return {}


# ---------------------------------------------------------------- warnings

def warn_power(fields, watts):
    stock = _num(fields['power_max_w'])
    if watts <= stock:
        return OK, 'Within the factory range.'
    pct = (watts / stock - 1) * 100
    cable = ('Check the cable: a PCIe 8-pin is rated 150 W each; a 12V-2x6 cable carries what its sense pins '
             'report (up to 600 W).')
    if pct > 25:
        return DANGER, f'{pct:.0f} % above the factory ceiling. Needs strong cooling, a power supply with headroom ' \
                       f'and a cable rated for it. {cable}'
    return WARN, f'{pct:.0f} % above the factory ceiling. Watch temperatures and the power supply. {cable}'


def warn_boot_power(fields, watts):
    stock = _num(fields['power_default_w'])
    if watts <= stock:
        return OK, 'The card starts at this power limit without any tuning tool.'
    return INFO, 'Above the factory default: the card runs at more power out of the box, before any tool sets it.'


def warn_voltage(fields, mv):
    stock = _num(fields['nvvdd_rel_equation_max_mv'])
    if mv <= stock:
        return OK, 'Factory voltage ceiling.'
    if mv - stock > 50:
        return DANGER, (f'{mv - stock:.0f} mV above the factory ceiling: XOC range. Much more heat and faster chip '
                        f'wear; for benchmark runs with strong cooling. Not tested on every GPU family.')
    return WARN, (f'{mv - stock:.0f} mV above the factory ceiling. Higher voltage wears the chip faster; keep it '
                  f'cool. The card only uses it if its voltage-frequency curve asks for it.')


def warn_offset(fields, mhz):
    stock = _num(fields['gpc_offset_max_mhz'])
    if mhz <= stock:
        return OK, 'The range your overclocking tool (e.g. MSI Afterburner) may offer for the core clock.'
    return INFO, 'Only widens the range your overclocking tool offers; it does not overclock by itself.'


def warn_temp(fields, c):
    stock = _num(fields['max_operating_temp_c'])
    if c == stock:
        return OK, 'The temperature the card holds itself under.'
    if c > stock:
        return WARN, 'Above the factory limit: the card lets itself run hotter before it slows down.'
    return INFO, 'Below the factory limit: the card slows down earlier and runs cooler.'


# ---------------------------------------------------------------- the controls a ROM gets

def controls(fields):
    """Simple-tab controls available for this ROM, in display order."""
    out = []
    if _editable(fields, 'power_max_w'):
        f = fields['power_max_w']
        stock = _num(f)
        lo = max(_num(f.get('bounds') or {}, 'minimum') or 0, 0)
        out.append(Control('power_max_w', 'Power limit', 'The highest board power the card may draw, and the top of '
                           'the power slider in tuning tools.', 'W', lo, _up(stock * 1.5, 25), 5, stock,
                           follow_power, warn_power))
    if _editable(fields, 'power_default_w'):
        f = fields['power_default_w']
        stock = _num(f)
        lo = _num(fields['power_min_w']) if _editable(fields, 'power_min_w') else stock / 2
        out.append(Control('power_default_w', 'Boot power', 'The power limit the card starts with, before any tool '
                           'changes it.', 'W', lo, _num(fields['power_max_w']) * 1.5, 5, stock, follow_nothing,
                           warn_boot_power))
    if _editable(fields, 'nvvdd_rel_equation_max_mv'):
        f = fields['nvvdd_rel_equation_max_mv']
        stock = _num(f)
        ov = _num(fields.get('nvvdd_ov_equation_max_mv') or {}) or stock + 100
        lo = _num(f.get('bounds') or {}, 'minimum') or stock - 100
        out.append(Control('nvvdd_rel_equation_max_mv', 'Core voltage ceiling', 'The highest core voltage the card '
                           'may request (the VRel limit in GPU-Z).', 'mV', lo, max(ov, stock), 5, stock,
                           follow_voltage, warn_voltage))
    if _editable(fields, 'gpc_offset_max_mhz'):
        stock = _num(fields['gpc_offset_max_mhz'])
        out.append(Control('gpc_offset_max_mhz', 'Core clock offset range', 'How far tools like MSI Afterburner may '
                           'push the core clock offset.', 'MHz', 0, max(2000, stock), 50, stock, follow_nothing,
                           warn_offset))
    if _editable(fields, 'max_operating_temp_c'):
        f = fields['max_operating_temp_c']
        b = f.get('bounds') or {}
        out.append(Control('max_operating_temp_c', 'Temperature limit', 'The GPU temperature the card holds itself '
                           'under.', '°C', _num(b, 'minimum') or 40, _num(b, 'maximum') or _num(f) + 3, 1, _num(f),
                           follow_nothing, warn_temp))
    return out
