#!/usr/bin/env python3
"""Offline nsperf send/receive log analyzer."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any


FNV64_OFFSET = 0xCBF29CE484222325
FNV64_PRIME = 0x100000001B3


def hash_id(value: str) -> int:
    h = FNV64_OFFSET
    for byte in value.encode():
        h ^= byte
        h = (h * FNV64_PRIME) & 0xFFFFFFFFFFFFFFFF
    return h


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def int_field(row: dict[str, str], name: str, default: int = 0) -> int:
    value = row.get(name, "")
    if value == "":
        return default
    return int(value)


def send_status(row: dict[str, str]) -> str:
    status = row.get("status", "")
    if status:
        return status
    if int_field(row, "skipped_before") > 0:
        return "sent"
    if row.get("send_error"):
        return "send_error"
    return "sent"


def bitrate_value(bits: int, start_ns: int | None, end_ns: int | None) -> float | None:
    if start_ns is None or end_ns is None or end_ns <= start_ns:
        return None
    return bits / ((end_ns - start_ns) / 1_000_000_000)


def format_ns(value: int) -> str:
    magnitude = abs(value)
    if magnitude < 1_000:
        return f"{value} ns"
    if magnitude < 1_000_000:
        return f"{value} ns ({value / 1_000:.3f} us)"
    if magnitude < 1_000_000_000:
        return f"{value} ns ({value / 1_000_000:.3f} ms)"
    return f"{value} ns ({value / 1_000_000_000:.3f} s)"


def percentile(ordered: list[int], fraction: float) -> int:
    idx = min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)
    return ordered[idx]


def stats_ns_data(values: list[int]) -> dict[str, int] | None:
    if not values:
        return None
    ordered = sorted(values)
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return {
        "count": len(values),
        "min_ns": ordered[0],
        "mean_ns": round(mean),
        "p50_ns": percentile(ordered, 0.50),
        "p95_ns": percentile(ordered, 0.95),
        "p99_ns": percentile(ordered, 0.99),
        "max_ns": ordered[-1],
        "stdev_ns": round(math.sqrt(variance)),
    }


def format_stats_ns(data: dict[str, int] | None) -> str:
    if data is None:
        return "n/a"
    return (
        f"count={data['count']} "
        f"min={format_ns(data['min_ns'])} "
        f"mean={format_ns(data['mean_ns'])} "
        f"p50={format_ns(data['p50_ns'])} "
        f"p95={format_ns(data['p95_ns'])} "
        f"p99={format_ns(data['p99_ns'])} "
        f"max={format_ns(data['max_ns'])} "
        f"stdev={format_ns(data['stdev_ns'])}"
    )


def adjacent_abs_deltas(values: list[int]) -> list[int]:
    return [abs(curr - prev) for prev, curr in zip(values, values[1:])]


def adjacent_spacing_errors(rows: list[dict[str, int]]) -> list[int]:
    errors = []
    for prev, curr in zip(rows, rows[1:]):
        recv_gap = curr["recv_ns"] - prev["recv_ns"]
        send_gap = curr["send_attempt_ns"] - prev["send_attempt_ns"]
        errors.append(recv_gap - send_gap)
    return errors


def rfc3550_jitter(values: list[int]) -> int | None:
    if len(values) < 2:
        return None
    jitter = 0.0
    prev = values[0]
    for curr in values[1:]:
        jitter += (abs(curr - prev) - jitter) / 16.0
        prev = curr
    return round(jitter)


def infer_ids(send_rows: list[dict[str, str]], run_id: str | None, flow_id: str | None) -> tuple[str | None, str | None]:
    if not send_rows:
        return run_id, flow_id
    if run_id is None:
        run_id = send_rows[0].get("run_id") or None
    if flow_id is None:
        flow_id = send_rows[0].get("flow_id") or None
    return run_id, flow_id


def analyze_data(args: argparse.Namespace) -> dict[str, Any]:
    send_rows = read_rows(args.send)
    recv_rows = read_rows(args.recv)
    run_id, flow_id = infer_ids(send_rows, args.run_id, args.flow_id)

    if run_id is not None:
        send_rows = [row for row in send_rows if row.get("run_id") == run_id]
    if flow_id is not None:
        send_rows = [row for row in send_rows if row.get("flow_id") == flow_id]

    run_hash = hash_id(run_id) if run_id is not None else None
    flow_hash = hash_id(flow_id) if flow_id is not None else None

    valid_recv = [row for row in recv_rows if not row.get("decode_error")]
    decode_errors = len(recv_rows) - len(valid_recv)
    if run_hash is not None:
        valid_recv = [row for row in valid_recv if int_field(row, "run_id_hash", -1) == run_hash]
    if flow_hash is not None:
        valid_recv = [row for row in valid_recv if int_field(row, "flow_id_hash", -1) == flow_hash]

    skipped_rows = [row for row in send_rows if send_status(row) == "skipped"]
    attempt_rows = [row for row in send_rows if send_status(row) != "skipped"]
    local_failures = [row for row in attempt_rows if row.get("send_error") or send_status(row) == "send_error"]
    sent_ok = [row for row in attempt_rows if not row.get("send_error") and send_status(row) == "sent"]

    sent_by_seq = {int_field(row, "seq"): row for row in sent_ok}
    recv_counts = Counter(int_field(row, "seq") for row in valid_recv)
    recv_unique = set(recv_counts)
    sent_unique = set(sent_by_seq)

    duplicates = sum(count - 1 for count in recv_counts.values() if count > 1)
    lost = len(sent_unique - recv_unique)

    reorders = 0
    max_seen = -1
    for row in valid_recv:
        seq = int_field(row, "seq")
        if seq < max_seen:
            reorders += 1
        max_seen = max(max_seen, seq)

    send_late = [int_field(row, "late_by_ns") for row in attempt_rows if row.get("late_by_ns")]
    skipped_late = [int_field(row, "late_by_ns") for row in skipped_rows if row.get("late_by_ns")]
    sender_errors_by_seq = [
        int_field(row, "late_by_ns")
        for row in sorted(attempt_rows, key=lambda send_row: int_field(send_row, "seq"))
        if row.get("late_by_ns")
    ]

    matched_receives = []
    for row in valid_recv:
        seq = int_field(row, "seq")
        sent = sent_by_seq.get(seq)
        if sent is None:
            continue
        send_attempt_ns = int_field(sent, "send_attempt_ns")
        recv_ns = int_field(row, "recv_ns")
        matched_receives.append(
            {
                "seq": seq,
                "send_attempt_ns": send_attempt_ns,
                "recv_ns": recv_ns,
                "latency_ns": recv_ns - send_attempt_ns,
            }
        )

    latencies = [row["latency_ns"] for row in matched_receives]
    earliest_by_seq = {}
    for row in matched_receives:
        prev = earliest_by_seq.get(row["seq"])
        if prev is None or row["recv_ns"] < prev["recv_ns"]:
            earliest_by_seq[row["seq"]] = row
    seq_ordered_receives = [earliest_by_seq[seq] for seq in sorted(earliest_by_seq)]
    seq_ordered_latencies = [row["latency_ns"] for row in seq_ordered_receives]
    arrival_ordered_receives = sorted(matched_receives, key=lambda recv_row: recv_row["recv_ns"])
    latency_jitter = adjacent_abs_deltas(seq_ordered_latencies)
    sender_jitter = adjacent_abs_deltas(sender_errors_by_seq)
    receive_spacing_errors = adjacent_spacing_errors(arrival_ordered_receives)
    rfc_jitter = rfc3550_jitter(seq_ordered_latencies)

    send_start = min((int_field(row, "send_attempt_ns") for row in sent_ok), default=None)
    send_end = max((int_field(row, "send_done_ns") for row in sent_ok), default=None)
    recv_start = min((int_field(row, "recv_ns") for row in valid_recv), default=None)
    recv_end = max((int_field(row, "recv_ns") for row in valid_recv), default=None)

    sent_bits = sum(int_field(row, "bytes") for row in sent_ok) * 8
    recv_bits = sum(int_field(row, "bytes") for row in valid_recv) * 8

    warnings = []
    if local_failures:
        warnings.append("local send failures occurred; network loss estimates are incomplete")
    if skipped_rows:
        warnings.append("generator skipped missed send slots; traffic was not fully generated")

    return {
        "schema": "nsperf-analysis-v1",
        "run_id": run_id,
        "flow_id": flow_id,
        "counts": {
            "scheduled_packets_logged": len(send_rows),
            "send_attempts": len(attempt_rows),
            "local_send_failures": len(local_failures),
            "skipped_by_generator": len(skipped_rows),
            "received_valid": len(valid_recv),
            "receive_decode_errors": decode_errors,
            "lost_after_successful_send": lost,
            "duplicates": duplicates,
            "reordered_receives": reorders,
        },
        "rates": {
            "generated_bps": bitrate_value(sent_bits, send_start, send_end),
            "received_bps": bitrate_value(recv_bits, recv_start, recv_end),
            "generated_bits": sent_bits,
            "received_bits": recv_bits,
            "send_start_ns": send_start,
            "send_end_ns": send_end,
            "recv_start_ns": recv_start,
            "recv_end_ns": recv_end,
        },
        "timing": {
            "sender_timing_error_ns": stats_ns_data(send_late),
            "sender_timing_jitter_abs_ns": stats_ns_data(sender_jitter),
            "skipped_timing_error_ns": stats_ns_data(skipped_late),
            "host_local_latency_estimate_ns": stats_ns_data(latencies),
            "host_local_latency_jitter_abs_ns": stats_ns_data(latency_jitter),
            "host_local_latency_jitter_rfc3550_ns": rfc_jitter,
            "receive_spacing_error_ns": stats_ns_data(receive_spacing_errors),
        },
        "warnings": warnings,
    }


def print_text(result: dict[str, Any]) -> None:
    counts = result["counts"]
    rates = result["rates"]
    timing = result["timing"]

    print(f"run_id: {result['run_id'] or 'n/a'}")
    print(f"flow_id: {result['flow_id'] or 'n/a'}")
    print(f"scheduled_packets_logged: {counts['scheduled_packets_logged']}")
    print(f"send_attempts: {counts['send_attempts']}")
    print(f"local_send_failures: {counts['local_send_failures']}")
    print(f"skipped_by_generator: {counts['skipped_by_generator']}")
    print(f"received_valid: {counts['received_valid']}")
    print(f"receive_decode_errors: {counts['receive_decode_errors']}")
    print(f"lost_after_successful_send: {counts['lost_after_successful_send']}")
    print(f"duplicates: {counts['duplicates']}")
    print(f"reordered_receives: {counts['reordered_receives']}")
    print(f"generated_bitrate: {format_bitrate_value(rates['generated_bps'])}")
    print(f"received_bitrate: {format_bitrate_value(rates['received_bps'])}")
    print(f"sender_timing_error: {format_stats_ns(timing['sender_timing_error_ns'])}")
    print(f"sender_timing_jitter_abs: {format_stats_ns(timing['sender_timing_jitter_abs_ns'])}")
    print(f"skipped_timing_error: {format_stats_ns(timing['skipped_timing_error_ns'])}")
    print(f"host_local_latency_estimate: {format_stats_ns(timing['host_local_latency_estimate_ns'])}")
    print(f"host_local_latency_jitter_abs: {format_stats_ns(timing['host_local_latency_jitter_abs_ns'])}")
    print(f"host_local_latency_jitter_rfc3550: {format_ns(timing['host_local_latency_jitter_rfc3550_ns']) if timing['host_local_latency_jitter_rfc3550_ns'] is not None else 'n/a'}")
    print(f"receive_spacing_error: {format_stats_ns(timing['receive_spacing_error_ns'])}")
    for warning in result["warnings"]:
        print(f"warning: {warning}")


def format_bitrate_value(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f} bit/s"


def analyze(args: argparse.Namespace) -> int:
    result = analyze_data(args)
    if args.output_format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print_text(result)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", type=Path, required=True, help="client send CSV")
    parser.add_argument("--recv", type=Path, required=True, help="server receive CSV")
    parser.add_argument("--run-id", help="run identifier to analyze; inferred from send log by default")
    parser.add_argument("--flow-id", help="flow identifier to analyze; inferred from send log by default")
    parser.add_argument("--format", dest="output_format", choices=("text", "json"), default="text", help="output format")
    parser.add_argument("--json", action="store_const", const="json", dest="output_format", help="shortcut for --format json")
    return analyze(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
