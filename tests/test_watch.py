"""watch / DIAG 口发现相关单测（不启真 Logel）。"""

from __future__ import annotations

from lcapt.cli import (
    EXIT_CAPTURE,
    EXIT_EXPORT,
    EXIT_OK,
    WATCH_DEFAULT_MAX_FAILURES,
    build_parser,
    watch_backoff_sec,
    watch_capture_ok,
)
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
    assert args.max_failures == WATCH_DEFAULT_MAX_FAILURES


def test_watch_backoff_exponential_then_cap():
    assert watch_backoff_sec(0) == 0.0
    assert watch_backoff_sec(1) == 2.0
    assert watch_backoff_sec(2) == 4.0
    assert watch_backoff_sec(3) == 8.0
    assert watch_backoff_sec(10) == 60.0
    assert watch_backoff_sec(2, base=1.0, cap=3.0) == 2.0
    assert watch_backoff_sec(5, base=1.0, cap=3.0) == 3.0


def test_watch_capture_ok_treats_export_fail_as_ok():
    assert watch_capture_ok(EXIT_OK) is True
    assert watch_capture_ok(EXIT_EXPORT) is True
    assert watch_capture_ok(EXIT_CAPTURE) is False


def test_provision_parser_has_no_max_size_mb():
    parser = build_parser()
    args = parser.parse_args(["provision", "--force", "--link"])
    assert not hasattr(args, "max_size_mb")
