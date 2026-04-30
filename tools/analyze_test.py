from __future__ import annotations

import argparse
import csv
import importlib.util
from pathlib import Path


def load_analyze_module():
    path = Path(__file__).with_name("analyze.py")
    spec = importlib.util.spec_from_file_location("analyze", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_csv(path: Path, header: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def test_interval_delivery_uses_send_window_while_receive_rate_uses_receive_window(tmp_path: Path) -> None:
    analyze = load_analyze_module()
    send_path = tmp_path / "run.send.csv"
    recv_path = tmp_path / "run.recv.csv"
    run_id = "run-a"
    flow_id = "flow-a"
    send_attempt_ns = 100
    recv_ns = 1_100_000_000

    write_csv(
        send_path,
        [
            "schema",
            "run_id",
            "flow_id",
            "seq",
            "status",
            "scheduled_ns",
            "send_attempt_ns",
            "send_done_ns",
            "send_error",
            "late_by_ns",
            "bytes",
        ],
        [
            {
                "schema": "nsperf-v1",
                "run_id": run_id,
                "flow_id": flow_id,
                "seq": 0,
                "status": "sent",
                "scheduled_ns": 0,
                "send_attempt_ns": send_attempt_ns,
                "send_done_ns": 200,
                "send_error": "",
                "late_by_ns": 100,
                "bytes": 100,
            }
        ],
    )
    write_csv(
        recv_path,
        [
            "schema",
            "run_id_hash",
            "flow_id_hash",
            "seq",
            "scheduled_ns",
            "send_attempt_ns",
            "recv_ns",
            "bytes",
            "remote_addr",
            "decode_error",
        ],
        [
            {
                "schema": "nsperf-v1",
                "run_id_hash": analyze.hash_id(run_id),
                "flow_id_hash": analyze.hash_id(flow_id),
                "seq": 0,
                "scheduled_ns": 0,
                "send_attempt_ns": send_attempt_ns,
                "recv_ns": recv_ns,
                "bytes": 100,
                "remote_addr": "127.0.0.1:12345",
                "decode_error": "",
            }
        ],
    )

    result = analyze.analyze_data(
        argparse.Namespace(
            send=send_path,
            recv=recv_path,
            run_id=run_id,
            flow_id=flow_id,
            interval_ns=1_000_000_000,
        )
    )

    intervals = result["intervals"]["windows"]
    assert intervals[0]["send_window"]["send_attempts"] == 1
    assert intervals[0]["delivery_for_send_window"]["received"] == 1
    assert intervals[0]["delivery_for_send_window"]["lost_after_successful_send"] == 0
    assert intervals[0]["receive_window"]["received_valid"] == 0
    assert intervals[1]["send_window"]["send_attempts"] == 0
    assert intervals[1]["receive_window"]["received_valid"] == 1
