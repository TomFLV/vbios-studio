"""Every GPU statistic Windows exposes without a kernel driver, read directly (no nvidia-smi):

- NVML (nvml.dll, NVIDIA's documented management library): clocks, temperatures and thresholds, power and limits,
  energy, loads, encoder/decoder, memory, BAR1, PCIe link/throughput/replays, fans, P-state, limiter reasons and the
  cumulative time spent in each limiter.
- NVAPI (nvapi64.dll) private interfaces, as documented by LibreHardwareMonitor (MPL-2.0, Interop/NvApi.cs and
  Hardware/Gpu/NvidiaGpu.cs): hotspot and memory-junction temperatures, core voltage, power topology (% of TDP),
  dynamic P-state loads, fan RPM, base and boost clocks.

Every reading is optional: a call a driver or GPU does not support is skipped, never guessed.
"""
from __future__ import annotations
import ctypes
import os
import time
from ctypes import byref, c_char, c_int, c_uint, c_ulonglong, c_void_p, sizeof

# ---------------------------------------------------------------- NVML

NVML_OK = 0


class _Util(ctypes.Structure):
    _fields_ = [('gpu', c_uint), ('memory', c_uint)]


class _Mem(ctypes.Structure):
    _fields_ = [('total', c_ulonglong), ('free', c_ulonglong), ('used', c_ulonglong)]


class _Violation(ctypes.Structure):
    _fields_ = [('reference', c_ulonglong), ('violation', c_ulonglong)]


# NVML perf policies for nvmlDeviceGetViolationStatus (ns of time the clocks were held back by each)
POLICIES = ((0, 'Power'), (1, 'Thermal'), (5, 'Voltage (reliability)'), (3, 'Board limit'), (2, 'Sync boost'),
            (4, 'Low utilization'))
REASONS = ((0x4, 'Power limit (Pwr)'), (0x400, 'Voltage ceiling (VRel)'), (0x80, 'Power brake'),
           (0x40, 'HW thermal'), (0x20, 'SW thermal'), (0x8, 'HW slowdown'), (0x100, 'Display clock'),
           (0x2, 'Application clocks'), (0x10, 'Sync boost'))


class Nvml:
    def __init__(self):
        self.dll = ctypes.WinDLL(os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'nvml.dll'))
        self.dll.nvmlInit_v2()
        self.handles = []
        n = c_uint()
        self.dll.nvmlDeviceGetCount_v2(byref(n))
        for i in range(n.value):
            h = c_void_p()
            if self.dll.nvmlDeviceGetHandleByIndex_v2(c_uint(i), byref(h)) == NVML_OK:
                self.handles.append(h)

    def call(self, name, *args):
        fn = getattr(self.dll, name, None)
        return fn is not None and fn(*args) == NVML_OK

    def u32(self, name, h, *pre):
        v = c_uint()
        return v.value if self.call(name, h, *pre, byref(v)) else None

    def u64(self, name, h, *pre):
        v = c_ulonglong()
        return v.value if self.call(name, h, *pre, byref(v)) else None

    def text(self, name, h=None, size=96):
        buf = (c_char * size)()
        args = (h, buf, c_uint(size)) if h is not None else (buf, c_uint(size))
        return buf.value.decode(errors='replace') if self.call(name, *args) else None


# ---------------------------------------------------------------- NVAPI (private interfaces, LibreHardwareMonitor)

def _ver(size, version):
    return size | (version << 16)


class _ThermalSensors(ctypes.Structure):
    _fields_ = [('version', c_uint), ('mask', c_uint), ('reserved', c_int * 8), ('temps', c_int * 32)]


class _VoltRails(ctypes.Structure):          # explicit size 0x4C; core microvolts at 0x28
    _fields_ = [('version', c_uint), ('reserved', c_uint * 9), ('uv', c_uint), ('uv_high', c_uint),
                ('tail', c_uint * 7)]


class _PowerEntry(ctypes.Structure):
    _fields_ = [('domain', c_uint), ('r0', c_uint), ('usage', c_uint), ('r1', c_uint)]


class _PowerTopology(ctypes.Structure):
    _fields_ = [('version', c_uint), ('count', c_uint), ('entries', _PowerEntry * 4)]


class _PState(ctypes.Structure):
    _fields_ = [('present', c_uint), ('pct', c_int)]


class _PStates(ctypes.Structure):
    _fields_ = [('version', c_uint), ('flags', c_uint), ('util', _PState * 8)]


class _FanItem(ctypes.Structure):
    _fields_ = [('id', c_uint), ('rpm', c_uint), ('min', c_uint), ('max', c_uint), ('level', c_uint),
                ('reserved', c_uint * 8)]


class _FanStatus(ctypes.Structure):
    _fields_ = [('version', c_uint), ('count', c_uint), ('reserved', c_ulonglong * 4), ('items', _FanItem * 32)]


class _ClockDomain(ctypes.Structure):
    _fields_ = [('present', c_uint), ('khz', c_uint)]


class _Clocks(ctypes.Structure):
    _fields_ = [('version', c_uint), ('clock_type', c_uint), ('domains', _ClockDomain * 32)]


assert sizeof(_VoltRails) == 0x4C and sizeof(_ThermalSensors) == 168


class NvApi:
    IDS = dict(init=0x0150E828, enum=0xE5AC921F, bus=0x1BE0B8E5, thermal=0x65FE3AAD, volt=0x465F9BCF,
               topology=0xEDCF624E, pstates=0x60DED2ED, fans=0x35AED5E8, clocks=0xDCB616C3)

    def __init__(self):
        dll = ctypes.WinDLL('nvapi64.dll')
        dll.nvapi_QueryInterface.restype = c_void_p
        dll.nvapi_QueryInterface.argtypes = [c_uint]
        self.fn = {}
        for k, i in self.IDS.items():
            p = dll.nvapi_QueryInterface(i)
            if p:
                self.fn[k] = ctypes.CFUNCTYPE(c_int, c_void_p, c_void_p)(p)
        ctypes.CFUNCTYPE(c_int)(dll.nvapi_QueryInterface(self.IDS['init']))()
        handles, n = (c_void_p * 64)(), c_uint()
        self.handles = {}
        if 'enum' in self.fn and self.fn['enum'](handles, byref(n)) == 0:
            for i in range(n.value):
                bus = c_uint()
                if self.fn['bus'](handles[i], byref(bus)) == 0:
                    self.handles[bus.value] = handles[i]
        self.thermal_mask = {}

    def _call(self, name, h, struct):
        return name in self.fn and self.fn[name](h, byref(struct)) == 0

    def thermal(self, h):
        if h not in self.thermal_mask:            # highest sensor bit the driver accepts (LHM's probing)
            mask = 0
            for bit in range(32):
                s = _ThermalSensors(_ver(sizeof(_ThermalSensors), 2), 1 << bit)
                if not self._call('thermal', h, s):
                    mask = (1 << bit) - 1
                    break
            self.thermal_mask[h] = mask
        if not self.thermal_mask[h]:
            return None
        s = _ThermalSensors(_ver(sizeof(_ThermalSensors), 2), self.thermal_mask[h])
        return [t / 256.0 for t in s.temps] if self._call('thermal', h, s) else None

    def voltage(self, h):
        s = _VoltRails(_ver(sizeof(_VoltRails), 1))
        if not self._call('volt', h, s):
            return None
        uv = (s.uv_high << 32) | s.uv
        return uv / 1e6 if 0 < uv < 3_000_000 else None

    def topology(self, h):
        s = _PowerTopology(_ver(sizeof(_PowerTopology), 1))
        return {e.domain: e.usage / 1000.0 for e in s.entries[:s.count]} if self._call('topology', h, s) else None

    def loads(self, h):
        s = _PStates(_ver(sizeof(_PStates), 1))
        return {i: s.util[i].pct for i in range(8) if s.util[i].present & 1} if self._call('pstates', h, s) else None

    def fans(self, h):
        s = _FanStatus(_ver(sizeof(_FanStatus), 1))
        return [(it.rpm, it.level) for it in s.items[:s.count]] if self._call('fans', h, s) else None

    def clocks(self, h, kind):
        """kind 0 current, 1 base, 2 boost: {domain: MHz} (0 graphics, 4 memory, 7 processor, 8 video)."""
        for version in (3, 2, 1):
            s = _Clocks(_ver(sizeof(_Clocks), version), kind)
            if self._call('clocks', h, s):
                return {i: s.domains[i].khz / 1000.0 for i in range(32) if s.domains[i].present & 1}
        return None


# ---------------------------------------------------------------- reference data (published specifications)

CHIPS = [  # (first device id, last device id, chip, process, die mm², transistors bn)
    (0x1E00, 0x1E7F, 'TU102', 'TSMC 12 nm', 754, 18.6), (0x1E80, 0x1EFF, 'TU104', 'TSMC 12 nm', 545, 13.6),
    (0x1F00, 0x1F7F, 'TU106', 'TSMC 12 nm', 445, 10.8), (0x1F80, 0x1FFF, 'TU117', 'TSMC 12 nm', 200, 4.7),
    (0x2180, 0x21DF, 'TU116', 'TSMC 12 nm', 284, 6.6), (0x2200, 0x223F, 'GA102', 'Samsung 8 nm', 628, 28.3),
    (0x2400, 0x243F, 'GA103', 'Samsung 8 nm', 496, 22.0), (0x2480, 0x24FF, 'GA104', 'Samsung 8 nm', 392, 17.4),
    (0x2500, 0x257F, 'GA106', 'Samsung 8 nm', 276, 12.0), (0x2580, 0x25FF, 'GA107', 'Samsung 8 nm', 200, 8.7),
    (0x2680, 0x26FF, 'AD102', 'TSMC 4N', 609, 76.3), (0x2700, 0x277F, 'AD103', 'TSMC 4N', 379, 45.9),
    (0x2780, 0x27FF, 'AD104', 'TSMC 4N', 294, 35.8), (0x2800, 0x287F, 'AD106', 'TSMC 4N', 190, 22.9),
    (0x2880, 0x28FF, 'AD107', 'TSMC 4N', 159, 18.9), (0x2B80, 0x2BFF, 'GB202', 'TSMC 4N', 750, 92.2),
    (0x2C00, 0x2C7F, 'GB203', 'TSMC 4N', 378, 45.6), (0x2D00, 0x2D7F, 'GB206', 'TSMC 4N', 181, 21.9),
    (0x2D80, 0x2DFF, 'GB207', 'TSMC 4N', 149, 16.9), (0x2F00, 0x2F7F, 'GB205', 'TSMC 4N', 263, 31.1),
]
VENDORS = {0x10DE: 'NVIDIA', 0x1458: 'Gigabyte', 0x1043: 'ASUS', 0x1462: 'MSI', 0x3842: 'EVGA', 0x19DA: 'Zotac',
           0x196E: 'PNY', 0x1569: 'Palit', 0x1B4C: 'Galax / KFA2', 0x7377: 'Colorful', 0x10B0: 'Gainward',
           0x1682: 'XFX', 0x1028: 'Dell', 0x103C: 'HP', 0x17AA: 'Lenovo', 0x1D05: 'Tongfang', 0x1558: 'Clevo',
           0x1849: 'ASRock', 0x174B: 'Sapphire', 0x148C: 'PowerColor', 0x1ACC: 'Point of View', 0x1B13: 'Jaton',
           0x1DA2: 'Sapphire', 0x1C4D: 'Inno3D', 0x1D58: 'Biostar'}
CORES_PER_SM = {'turing': 64, 'ampere': 128, 'ada': 128, 'blackwell': 128}
TENSOR_PER_SM = {'turing': 8, 'ampere': 4, 'ada': 4, 'blackwell': 4}


def chip_of(device):
    return next((c for c in CHIPS if c[0] <= device <= c[1]), None)


# ---------------------------------------------------------------- system

class _MemStatus(ctypes.Structure):
    _fields_ = [('length', c_uint), ('load', c_uint), ('total', c_ulonglong), ('avail', c_ulonglong),
                ('pagetotal', c_ulonglong), ('pageavail', c_ulonglong), ('virttotal', c_ulonglong),
                ('virtavail', c_ulonglong), ('extavail', c_ulonglong)]


class _FileTime(ctypes.Structure):
    _fields_ = [('lo', c_uint), ('hi', c_uint)]


def _ft(f):
    return (f.hi << 32) | f.lo


# ---------------------------------------------------------------- the reader

class Reader:
    """Open once, then read() as often as you like; returns an ordered list of (group, name, value, unit)."""

    def __init__(self, index=0):
        self.index = index
        self.nvml = Nvml()
        self.h = self.nvml.handles[index]
        try:
            self.api = NvApi()
        except OSError:
            self.api = None
        self.bus = None
        pci = (c_char * 128)()
        if self.nvml.call('nvmlDeviceGetPciInfo_v3', self.h, pci):
            self.bus = int.from_bytes(bytes(pci[20:24]), 'little')        # nvmlPciInfo_t.bus
        self.nh = self.api.handles.get(self.bus) if self.api and self.bus is not None else None
        self.name = self.nvml.text('nvmlDeviceGetName', self.h) or 'NVIDIA GPU'
        self._cpu = None
        self.static = self.card()

    # -- the Graphics Card sheet (values that do not change while running)
    def card(self):
        n, h = self.nvml, self.h
        pci = (c_char * 128)()
        dev = sub = 0
        if n.call('nvmlDeviceGetPciInfo_v3', h, pci):
            raw = bytes(pci)
            dev = int.from_bytes(raw[28:32], 'little') >> 16               # pciDeviceId: device << 16 | vendor
            sub = int.from_bytes(raw[32:36], 'little')                     # pciSubSystemId
        from nvidia_vbios.families import architecture
        family = (architecture(dev) or {}).get('family') if dev else None
        chip = chip_of(dev)
        cores = n.u32('nvmlDeviceGetNumGpuCores', h)
        sms = cores // CORES_PER_SM.get(family, 128) if cores else None
        mem = _Mem()
        n.call('nvmlDeviceGetMemoryInfo', h, byref(mem))
        bar = _Mem()
        n.call('nvmlDeviceGetBAR1MemoryInfo', h, byref(bar))
        width = n.u32('nvmlDeviceGetMemoryBusWidth', h)
        mem_max = n.u32('nvmlDeviceGetMaxClockInfo', h, c_uint(2))
        major, minor = c_int(), c_int()
        cc = f'{major.value}.{minor.value}' if n.call('nvmlDeviceGetCudaComputeCapability', h, byref(major),
                                                       byref(minor)) else None
        cuda = None
        v = c_int()
        if n.call('nvmlSystemGetCudaDriverVersion_v2', byref(v)):
            cuda = f'{v.value // 1000}.{v.value % 1000 // 10}'
        base = boost = mbase = None
        if self.nh:
            b, bo = self.api.clocks(self.nh, 1) or {}, self.api.clocks(self.nh, 2) or {}
            base, boost, mbase = b.get(0), bo.get(0), b.get(4)
        rows = [
            ('Name', self.name),
            ('GPU', f'{chip[2]} ({family.title()})' if chip and family else family or '—'),
            ('Revision', None),
            ('Technology', chip[3] if chip else None),
            ('Die size', f'{chip[4]} mm²' if chip else None),
            ('Transistors', f'{chip[5]:g} billion' if chip else None),
            ('BIOS version', n.text('nvmlDeviceGetVbiosVersion', h, 32)),
            ('Subvendor', VENDORS.get(sub & 0xFFFF, f'{sub & 0xFFFF:04X}') if sub else None),
            ('Device ID', f'10DE {dev:04X} - {sub & 0xFFFF:04X} {sub >> 16:04X}' if dev else None),
            ('Bus interface', self._link_text()),
            ('Shaders (CUDA cores)', f'{cores:,}' if cores else None),
            ('SMs', sms),
            ('TMUs', sms * 4 if sms else None),
            ('Tensor cores', sms * TENSOR_PER_SM.get(family, 4) if sms else None),
            ('RT cores', sms if sms and family else None),
            ('Memory size', f'{mem.total / 2**30:.0f} GB' if mem.total else None),
            ('Bus width', f'{width} bit' if width else None),
            ('Memory clock (max)', f'{mem_max} MHz  ({mem_max * 2 / 1000:.1f} Gbps)' if mem_max else None),
            ('Bandwidth', f'{mem_max * 2 / 1000 * width / 8:.1f} GB/s' if mem_max and width else None),
            ('GPU clock (base)', f'{base:.0f} MHz' if base else None),
            ('Boost clock', f'{boost:.0f} MHz' if boost else None),
            ('Memory clock (base)', f'{mbase:.0f} MHz' if mbase else None),
            ('Resizable BAR', ('Enabled' if bar.total >= mem.total * 0.9 else f'Disabled (BAR1 {bar.total >> 20} MB)')
             if bar.total and mem.total else None),
            ('Driver', n.text('nvmlSystemGetDriverVersion', None, 80)),
            ('CUDA (driver)', cuda),
            ('Compute capability', cc),
            ('PCI bus', f'{self.bus:02X}' if self.bus is not None else None),
        ]
        return [(k, v) for k, v in rows if v not in (None, '')]

    def _link_text(self):
        n, h = self.nvml, self.h
        cg, cw = n.u32('nvmlDeviceGetCurrPcieLinkGeneration', h), n.u32('nvmlDeviceGetCurrPcieLinkWidth', h)
        mg, mw = n.u32('nvmlDeviceGetMaxPcieLinkGeneration', h), n.u32('nvmlDeviceGetMaxPcieLinkWidth', h)
        if not mg:
            return None
        gpu_gen = n.u32('nvmlDeviceGetGpuMaxPcieLinkGeneration', h)
        text = f'PCIe x{mw} {mg}.0 @ x{cw} {cg}.0'
        if gpu_gen and gpu_gen > mg:
            text += f'  (card supports {gpu_gen}.0; the slot or platform limits it to {mg}.0)'
        elif cg != mg:
            text += '  (the link slows down at idle to save power)'
        return text

    # -- live sensors
    def read(self):
        n, h, out = self.nvml, self.h, []
        add = lambda g, k, v, u='': v is not None and out.append((g, k, v, u))  # noqa: E731
        # clocks
        for t, name in ((0, 'GPU clock'), (1, 'SM clock'), (2, 'Memory clock'), (3, 'Video clock')):
            add('Clocks', name, n.u32('nvmlDeviceGetClockInfo', h, c_uint(t)), 'MHz')
        # temperatures
        add('Temperatures', 'GPU temperature', n.u32('nvmlDeviceGetTemperature', h, c_uint(0)), '°C')
        temps = self.api.thermal(self.nh) if self.nh else None
        if temps:
            add('Temperatures', 'Hot spot', round(temps[1], 1) if temps[1] > 0 else None, '°C')
            memj = temps[7] if self.name.startswith('NVIDIA GeForce RTX 40') else (
                temps[2] if 'RTX 50' in self.name else temps[9])
            add('Temperatures', 'Memory junction', round(memj, 1) if memj > 0 else None, '°C')
        for t, name in ((3, 'GPU max operating temp'), (1, 'Slowdown temperature'), (0, 'Shutdown temperature')):
            add('Temperatures', name, n.u32('nvmlDeviceGetTemperatureThreshold', h, c_uint(t)), '°C')
        # voltage + power
        add('Power', 'GPU core voltage', round(self.api.voltage(self.nh), 4) if self.nh and
            self.api.voltage(self.nh) else None, 'V')
        p = n.u32('nvmlDeviceGetPowerUsage', h)
        add('Power', 'Board power draw', round(p / 1000, 1) if p is not None else None, 'W')
        topo = self.api.topology(self.nh) if self.nh else None
        if topo:
            add('Power', 'Power consumption (% TDP)', round(topo.get(1, topo.get(0)), 1), '%')
            if 0 in topo and 1 in topo:
                add('Power', 'GPU chip power (% TDP)', round(topo[0], 1), '%')
        for name, fn in (('Power limit (enforced)', 'nvmlDeviceGetEnforcedPowerLimit'),
                         ('Power limit (boot default)', 'nvmlDeviceGetPowerManagementDefaultLimit')):
            v = n.u32(fn, h)
            add('Power', name, round(v / 1000) if v else None, 'W')
        lo, hi = c_uint(), c_uint()
        if n.call('nvmlDeviceGetPowerManagementLimitConstraints', h, byref(lo), byref(hi)):
            add('Power', 'Power limit range', f'{lo.value // 1000} – {hi.value // 1000}', 'W')
        e = n.u64('nvmlDeviceGetTotalEnergyConsumption', h)
        add('Power', 'Energy since driver load', round(e / 3.6e9, 3) if e else None, 'kWh')
        # loads
        u = _Util()
        if n.call('nvmlDeviceGetUtilizationRates', h, byref(u)):
            add('Loads', 'GPU load', u.gpu, '%')
            add('Loads', 'Memory controller load', u.memory, '%')
        loads = self.api.loads(self.nh) if self.nh else None
        if loads:
            add('Loads', 'Video engine load', loads.get(2), '%')
            add('Loads', 'Bus interface load', loads.get(3), '%')
        for name, fn in (('Encoder load', 'nvmlDeviceGetEncoderUtilization'),
                         ('Decoder load', 'nvmlDeviceGetDecoderUtilization')):
            v, period = c_uint(), c_uint()
            if n.call(fn, h, byref(v), byref(period)):
                add('Loads', name, v.value, '%')
        # memory
        mem = _Mem()
        if n.call('nvmlDeviceGetMemoryInfo', h, byref(mem)):
            add('Memory', 'Memory used', mem.used >> 20, 'MB')
            add('Memory', 'Memory free', mem.free >> 20, 'MB')
        bar = _Mem()
        if n.call('nvmlDeviceGetBAR1MemoryInfo', h, byref(bar)):
            add('Memory', 'BAR1 used', bar.used >> 20, 'MB')
        # bus
        add('PCIe', 'Link', f"x{n.u32('nvmlDeviceGetCurrPcieLinkWidth', h)} "
                            f"Gen {n.u32('nvmlDeviceGetCurrPcieLinkGeneration', h)}")
        for c, name in ((1, 'PCIe receive'), (0, 'PCIe transmit')):
            v = n.u32('nvmlDeviceGetPcieThroughput', h, c_uint(c))
            add('PCIe', name, round(v / 1024, 1) if v is not None else None, 'MB/s')
        add('PCIe', 'PCIe replay errors', n.u32('nvmlDeviceGetPcieReplayCounter', h))
        # fans
        fans = self.api.fans(self.nh) if self.nh else None
        count = n.u32('nvmlDeviceGetNumFans', h) or 0
        for i in range(count):
            add('Fans', f'Fan {i + 1} speed', n.u32('nvmlDeviceGetFanSpeed_v2', h, c_uint(i)), '%')
        for i, (rpm, _) in enumerate(fans or []):
            add('Fans', f'Fan {i + 1} RPM', rpm, 'RPM')
        # state + limiters
        ps = n.u32('nvmlDeviceGetPerformanceState', h)
        add('Limits', 'Performance state', f'P{ps}' if ps is not None and ps < 32 else None)
        r = n.u64('nvmlDeviceGetCurrentClocksEventReasons', h)
        if r is None:
            r = n.u64('nvmlDeviceGetCurrentClocksThrottleReasons', h)
        if r is not None:
            names = [name for bit, name in REASONS if r & bit]
            add('Limits', 'PerfCap reason', ', '.join(names) if names else ('Idle' if r & 1 else 'None'))
        for pol, name in POLICIES:
            v = _Violation()
            if n.call('nvmlDeviceGetViolationStatus', h, c_uint(pol), byref(v)) and v.violation:
                add('Limits', f'Time limited: {name}', round(v.violation / 1e9, 1), 's')
        # system
        ms = _MemStatus(sizeof(_MemStatus))
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(byref(ms)):
            add('System', 'System memory used', (ms.total - ms.avail) >> 20, 'MB')
        idle, kern, user = _FileTime(), _FileTime(), _FileTime()
        if ctypes.windll.kernel32.GetSystemTimes(byref(idle), byref(kern), byref(user)):
            now = (_ft(idle), _ft(kern) + _ft(user))
            if self._cpu:
                di, dt = now[0] - self._cpu[0], now[1] - self._cpu[1]
                add('System', 'CPU load', round(100 * (1 - di / dt), 1) if dt else None, '%')
            self._cpu = now
        return out


class Stats:
    """Current / min / max / average per sensor, and optional CSV logging."""

    def __init__(self):
        self.reset()
        self.log = None

    def reset(self):
        self.data = {}

    def update(self, rows):
        for g, k, v, u in rows:
            d = self.data.setdefault(k, dict(group=g, unit=u, cur=v, min=v, max=v, sum=0.0, n=0))
            d['cur'] = v
            if isinstance(v, (int, float)):
                d['min'] = v if not isinstance(d['min'], (int, float)) else min(d['min'], v)
                d['max'] = v if not isinstance(d['max'], (int, float)) else max(d['max'], v)
                d['sum'] += v
                d['n'] += 1
        if self.log:
            if not self.log['header']:
                self.log['file'].write('time,' + ','.join(f'"{k} [{u}]"' if u else f'"{k}"' for _, k, _, u in rows)
                                       + '\n')
                self.log['header'] = [k for _, k, _, _ in rows]
            vals = {k: v for _, k, v, _ in rows}
            self.log['file'].write(time.strftime('%Y-%m-%d %H:%M:%S,') +
                                   ','.join(f'"{vals.get(k, "")}"' for k in self.log['header']) + '\n')
            self.log['file'].flush()

    def start_log(self, path):
        self.stop_log()
        self.log = dict(file=open(path, 'w', encoding='utf-8'), header=None, path=path)

    def stop_log(self):
        if self.log:
            self.log['file'].close()
            self.log = None
