"""watch / DIAG 口发现相关单测（不启真 Logel）。"""

from __future__ import annotations

from lcapt.cli import build_parser
from lcapt.export import find_sprd_diag_ports


def test_find_sprd_diag_ports_filters_names():
    devices = {
        "COM31": "SPRD DIAG (COM31)",
        "COM33": "SPRD AT (COM33)",
        "COM30": "SPRD CP LOG (COM30)",
        "COM40": "SPRD DIAG(SENSOR HUB)",
        "COM41": "SPRD DIAG(AGDSP)",
    }
    assert find_sprd_diag_ports(devices) == ["COM31"]


def test_watch_parser_defaults():
    parser = build_parser()
    args = parser.parse_args(["watch"])
    assert args.command == "watch"
    assert args.duration == 0.0
    assert args.settle == 3.0
    assert args.poll == 1.0
    assert args.once is False
    assert args.playback is True
