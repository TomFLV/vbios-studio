"""VBIOS Studio: one window to edit an NVIDIA VBIOS copy, load it through the driver's registry override,
restart and verify. The ROM parsing, validation and loading are the unmodified NVIDIA VBIOS Vix Editor and
Windows Vix Loader by panchovix (vendor/), GPL-3.0-or-later; this package is the user interface around them."""
import sys
from pathlib import Path

__version__ = '1.0.0'
ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'vendor'
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))
