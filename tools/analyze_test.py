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


SEND_HEADER = [
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
]

RECV_HEADER = [
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
]


def sent_row(
    run_id: str,
    flow_id: str,
    seq: int,
    send_attempt_ns: int,
    *,
    scheduled_ns: int | None = None,
    bytes_: int = 100,
) -> dict[str, object]:
    if scheduled_ns is None:
        scheduled_ns = send_attempt_ns
    return {
        "schema": "nsperf-v1",
        "run_id": run_id,
        "flow_id": flow_id,
        "seq": seq,
        "status": "sent",
        "scheduled_ns": scheduled_ns,
        "send_attempt_ns": send_attempt_ns,
        "send_done_ns": send_attempt_ns + 1,
        "send_error": "",
        "late_by_ns": send_attempt_ns - scheduled_ns,
        "bytes": bytes_,
    }


def recv_row(
    analyze,
    run_id: str,
    flow_id: str,
    seq: int,
    send_attempt_ns: int,
    *,
    recv_ns: int | None = None,
    bytes_: int = 100,
) -> dict[str, object]:
    if recv_ns is None:
        recv_ns = send_attempt_ns + 10
    return {
        "schema": "nsperf-v1",
        "run_id_hash": analyze.hash_id(run_id),
        "flow_id_hash": analyze.hash_id(flow_id),
        "seq": seq,
        "scheduled_ns": send_attempt_ns,
        "send_attempt_ns": send_attempt_ns,
        "recv_ns": recv_ns,
        "bytes": bytes_,
        "remote_addr": "127.0.0.1:12345",
        "decode_error": "",
    }


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
            skip_start_ns=0,
        )
    )

    intervals = result["intervals"]["windows"]
    assert intervals[0]["send_window"]["send_attempts"] == 1
    assert intervals[0]["delivery_for_send_window"]["received"] == 1
    assert intervals[0]["delivery_for_send_window"]["lost_after_successful_send"] == 0
    assert intervals[0]["receive_window"]["received_valid"] == 0
    assert intervals[1]["send_window"]["send_attempts"] == 0
    assert intervals[1]["receive_window"]["received_valid"] == 1


def test_skip_start_ms_discards_initial_loss_and_resets_interval_origin(tmp_path: Path) -> None:
    analyze = load_analyze_module()
    send_path = tmp_path / "run.send.csv"
    recv_path = tmp_path / "run.recv.csv"
    run_id = "run-a"
    flow_id = "flow-a"

    write_csv(
        send_path,
        SEND_HEADER,
        [
            sent_row(run_id, flow_id, 0, 0),
            sent_row(run_id, flow_id, 1, 100_000_000),
            sent_row(run_id, flow_id, 2, 200_000_000),
        ],
    )
    write_csv(
        recv_path,
        RECV_HEADER,
        [recv_row(analyze, run_id, flow_id, 2, 200_000_000, recv_ns=250_000_000)],
    )

    result = analyze.analyze_data(
        argparse.Namespace(
            send=send_path,
            recv=recv_path,
            run_id=run_id,
            flow_id=flow_id,
            interval_ns=1_000_000_000,
            skip_start_ns=200_000_000,
        )
    )

    assert result["skip_cutoff_ns"] == 200_000_000
    assert result["skipped_send_rows"] == 2
    assert result["skipped_recv_rows"] == 0
    assert result["counts"]["send_attempts"] == 1
    assert result["counts"]["received_valid"] == 1
    assert result["counts"]["lost_after_successful_send"] == 0
    assert result["intervals"]["base_ns"] == 200_000_000
    assert len(result["intervals"]["windows"]) == 1


def test_skip_start_ms_uses_selected_run_flow_start(tmp_path: Path) -> None:
    analyze = load_analyze_module()
    send_path = tmp_path / "run.send.csv"
    recv_path = tmp_path / "run.recv.csv"
    target_run_id = "run-target"
    target_flow_id = "flow-target"

    write_csv(
        send_path,
        SEND_HEADER,
        [
            sent_row("other-run", "other-flow", 0, 0),
            sent_row(target_run_id, target_flow_id, 0, 1_000_000_000),
            sent_row(target_run_id, target_flow_id, 1, 1_300_000_000),
        ],
    )
    write_csv(
        recv_path,
        RECV_HEADER,
        [
            recv_row(analyze, target_run_id, target_flow_id, 0, 1_000_000_000),
            recv_row(analyze, target_run_id, target_flow_id, 1, 1_300_000_000),
        ],
    )

    result = analyze.analyze_data(
        argparse.Namespace(
            send=send_path,
            recv=recv_path,
            run_id=target_run_id,
            flow_id=target_flow_id,
            interval_ns=None,
            skip_start_ns=200_000_000,
        )
    )

    assert result["skip_cutoff_ns"] == 1_200_000_000
    assert result["skipped_send_rows"] == 1
    assert result["skipped_recv_rows"] == 1
    assert result["counts"]["send_attempts"] == 1
    assert result["counts"]["received_valid"] == 1
    assert result["counts"]["lost_after_successful_send"] == 0


def test_parse_skip_start_ms_accepts_decimal_and_rejects_negative() -> None:
    analyze = load_analyze_module()

    assert analyze.parse_skip_start_ms_ns("0.5") == 500_000

    try:
        analyze.parse_skip_start_ms_ns("-1")
    except argparse.ArgumentTypeError:
        pass
    else:
        assert False, "negative skip duration should be rejected"
