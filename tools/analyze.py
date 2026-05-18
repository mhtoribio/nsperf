#!/usr/bin/env python3
"""Offline nsperf send/receive log analyzer."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any


FNV64_OFFSET = 0xCBF29CE484222325
FNV64_PRIME = 0x100000001B3
NS_PER_SECOND = 1_000_000_000
NS_PER_MILLISECOND = 1_000_000


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


def parse_interval_ns(value: str) -> int:
    try:
        seconds = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"invalid interval {value!r}") from exc
    if seconds <= 0:
        raise argparse.ArgumentTypeError("--interval must be greater than 0")

    interval_ns = (seconds * NS_PER_SECOND).to_integral_value(rounding=ROUND_HALF_UP)
    if interval_ns <= 0:
        raise argparse.ArgumentTypeError("--interval is too small")
    return int(interval_ns)


def parse_skip_start_ms_ns(value: str) -> int:
    try:
        milliseconds = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"invalid skip duration {value!r}") from exc
    if milliseconds < 0:
        raise argparse.ArgumentTypeError("--skip-start-ms must be non-negative")

    return int((milliseconds * NS_PER_MILLISECOND).to_integral_value(rounding=ROUND_HALF_UP))


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
    return bits / ((end_ns - start_ns) / NS_PER_SECOND)


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


def window_index(timestamp_ns: int, base_ns: int, interval_ns: int) -> int:
    if timestamp_ns < base_ns:
        return 0
    return (timestamp_ns - base_ns) // interval_ns


def send_window_timestamp(row: dict[str, str]) -> int:
    if send_status(row) == "skipped":
        return int_field(row, "scheduled_ns")
    if row.get("send_attempt_ns"):
        return int_field(row, "send_attempt_ns")
    return int_field(row, "scheduled_ns")


def start_skip_metadata(skip_start_ns: int) -> dict[str, Any]:
    return {
        "skip_start_ms": skip_start_ns / NS_PER_MILLISECOND,
        "skip_start_ns": skip_start_ns,
        "skip_cutoff_ns": None,
        "skipped_send_rows": 0,
        "skipped_recv_rows": 0,
    }


def apply_start_skip(
    send_rows: list[dict[str, str]],
    valid_recv: list[dict[str, str]],
    decode_error_rows: list[dict[str, str]],
    skip_start_ns: int,
) -> tuple[
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str]],
    dict[str, Any],
]:
    metadata = start_skip_metadata(skip_start_ns)
    if skip_start_ns == 0 or not send_rows:
        return send_rows, valid_recv, decode_error_rows, metadata

    first_send_ns = min(send_window_timestamp(row) for row in send_rows)
    cutoff_ns = first_send_ns + skip_start_ns
    filtered_send = [row for row in send_rows if send_window_timestamp(row) >= cutoff_ns]
    filtered_valid_recv = [row for row in valid_recv if receive_skip_timestamp(row) >= cutoff_ns]
    filtered_decode_errors = [row for row in decode_error_rows if receive_skip_timestamp(row) >= cutoff_ns]
    skipped_recv_rows = (
        len(valid_recv)
        + len(decode_error_rows)
        - len(filtered_valid_recv)
        - len(filtered_decode_errors)
    )

    metadata["skip_cutoff_ns"] = cutoff_ns
    metadata["skipped_send_rows"] = len(send_rows) - len(filtered_send)
    metadata["skipped_recv_rows"] = skipped_recv_rows
    return filtered_send, filtered_valid_recv, filtered_decode_errors, metadata


def receive_skip_timestamp(row: dict[str, str]) -> int:
    if row.get("send_attempt_ns"):
        return int_field(row, "send_attempt_ns")
    return int_field(row, "recv_ns")


def reorder_count(rows: list[dict[str, str]]) -> int:
    reorders = 0
    max_seen = -1
    for row in rows:
        seq = int_field(row, "seq")
        if seq < max_seen:
            reorders += 1
        max_seen = max(max_seen, seq)
    return reorders


def bucket_rows(rows: list[Any], base_ns: int, interval_ns: int, interval_count: int, timestamp_fn: Any) -> list[list[Any]]:
    buckets: list[list[Any]] = [[] for _ in range(interval_count)]
    for row in rows:
        idx = window_index(timestamp_fn(row), base_ns, interval_ns)
        if 0 <= idx < interval_count:
            buckets[idx].append(row)
    return buckets


def infer_ids(send_rows: list[dict[str, str]], run_id: str | None, flow_id: str | None) -> tuple[str | None, str | None]:
    if not send_rows:
        return run_id, flow_id
    if run_id is None:
        run_id = send_rows[0].get("run_id") or None
    if flow_id is None:
        flow_id = send_rows[0].get("flow_id") or None
    return run_id, flow_id


def build_intervals(
    interval_ns: int,
    send_rows: list[dict[str, str]],
    skipped_rows: list[dict[str, str]],
    attempt_rows: list[dict[str, str]],
    sent_ok: list[dict[str, str]],
    decode_error_rows: list[dict[str, str]],
    valid_recv: list[dict[str, str]],
    matched_receives: list[dict[str, int]],
    earliest_by_seq: dict[int, dict[str, int]],
) -> dict[str, Any] | None:
    timestamps = [send_window_timestamp(row) for row in send_rows]
    timestamps.extend(int_field(row, "recv_ns") for row in valid_recv)
    timestamps.extend(int_field(row, "recv_ns") for row in decode_error_rows)
    if not timestamps:
        return None

    base_ns = min(timestamps)
    max_ns = max(timestamps)
    interval_count = window_index(max_ns, base_ns, interval_ns) + 1
    reorder_counts = receive_reorder_counts_by_window(valid_recv, base_ns, interval_ns, interval_count)
    skipped_by_window = bucket_rows(
        skipped_rows, base_ns, interval_ns, interval_count, lambda row: int_field(row, "scheduled_ns")
    )
    attempts_by_window = bucket_rows(
        attempt_rows, base_ns, interval_ns, interval_count, send_window_timestamp
    )
    sent_ok_by_window = bucket_rows(
        sent_ok, base_ns, interval_ns, interval_count, send_window_timestamp
    )
    recv_by_window = bucket_rows(
        valid_recv, base_ns, interval_ns, interval_count, lambda row: int_field(row, "recv_ns")
    )
    decode_errors_by_window = bucket_rows(
        decode_error_rows, base_ns, interval_ns, interval_count, lambda row: int_field(row, "recv_ns")
    )
    matched_recv_by_recv_window = bucket_rows(
        matched_receives, base_ns, interval_ns, interval_count, lambda row: row["recv_ns"]
    )
    delivery_by_send_window = bucket_rows(
        list(earliest_by_seq.values()), base_ns, interval_ns, interval_count, lambda row: row["send_attempt_ns"]
    )

    intervals = []
    for index in range(interval_count):
        start_ns = base_ns + index * interval_ns
        end_ns = start_ns + interval_ns

        skipped_in_window = skipped_by_window[index]
        attempts_in_window = attempts_by_window[index]
        failures_in_window = [
            row for row in attempts_in_window if row.get("send_error") or send_status(row) == "send_error"
        ]
        sent_ok_in_window = sent_ok_by_window[index]
        sent_ok_seq_in_window = {int_field(row, "seq") for row in sent_ok_in_window}
        delivery_rows = delivery_by_send_window[index]
        delivery_latencies = [
            row["latency_ns"] for row in sorted(delivery_rows, key=lambda delivery: delivery["seq"])
        ]
        send_late = [int_field(row, "late_by_ns") for row in attempts_in_window if row.get("late_by_ns")]
        sender_errors_by_seq = [
            int_field(row, "late_by_ns")
            for row in sorted(attempts_in_window, key=lambda send_row: int_field(send_row, "seq"))
            if row.get("late_by_ns")
        ]
        skipped_late = [int_field(row, "late_by_ns") for row in skipped_in_window if row.get("late_by_ns")]
        sent_bits = sum(int_field(row, "bytes") for row in sent_ok_in_window) * 8

        recv_in_window = recv_by_window[index]
        decode_errors_in_window = decode_errors_by_window[index]
        recv_counts = Counter(int_field(row, "seq") for row in recv_in_window)
        recv_duplicates = sum(count - 1 for count in recv_counts.values() if count > 1)
        recv_bits = sum(int_field(row, "bytes") for row in recv_in_window) * 8
        matched_recv_in_window = matched_recv_by_recv_window[index]
        receive_spacing_errors = adjacent_spacing_errors(
            sorted(matched_recv_in_window, key=lambda recv_row: recv_row["recv_ns"])
        )

        received_for_send_window = len(delivery_rows)
        lost_for_send_window = len(sent_ok_seq_in_window) - received_for_send_window

        intervals.append(
            {
                "index": index,
                "start_ns": start_ns,
                "end_ns": end_ns,
                "start_s": (start_ns - base_ns) / NS_PER_SECOND,
                "end_s": (end_ns - base_ns) / NS_PER_SECOND,
                "duration_ns": interval_ns,
                "send_window": {
                    "scheduled_packets_logged": len(attempts_in_window) + len(skipped_in_window),
                    "send_attempts": len(attempts_in_window),
                    "local_send_failures": len(failures_in_window),
                    "skipped_by_generator": len(skipped_in_window),
                    "generated_bits": sent_bits,
                    "generated_bps": bitrate_value(sent_bits, start_ns, end_ns),
                    "sender_timing_error_ns": stats_ns_data(send_late),
                    "sender_timing_jitter_abs_ns": stats_ns_data(adjacent_abs_deltas(sender_errors_by_seq)),
                    "skipped_timing_error_ns": stats_ns_data(skipped_late),
                },
                "receive_window": {
                    "received_valid": len(recv_in_window),
                    "receive_decode_errors": len(decode_errors_in_window),
                    "received_bits": recv_bits,
                    "received_bps": bitrate_value(recv_bits, start_ns, end_ns),
                    "duplicates": recv_duplicates,
                    "reordered_receives": reorder_counts[index],
                    "receive_spacing_error_ns": stats_ns_data(receive_spacing_errors),
                },
                "delivery_for_send_window": {
                    "received": received_for_send_window,
                    "lost_after_successful_send": lost_for_send_window,
                    "host_local_latency_estimate_ns": stats_ns_data(delivery_latencies),
                    "host_local_latency_jitter_abs_ns": stats_ns_data(adjacent_abs_deltas(delivery_latencies)),
                    "host_local_latency_jitter_rfc3550_ns": rfc3550_jitter(delivery_latencies),
                },
            }
        )

    return {
        "interval_seconds": interval_ns / NS_PER_SECOND,
        "interval_ns": interval_ns,
        "base_ns": base_ns,
        "windows": intervals,
    }


def receive_reorder_counts_by_window(
    valid_recv: list[dict[str, str]],
    base_ns: int,
    interval_ns: int,
    interval_count: int,
) -> list[int]:
    counts = [0] * interval_count
    max_seen = -1
    for row in valid_recv:
        seq = int_field(row, "seq")
        if seq < max_seen:
            idx = window_index(int_field(row, "recv_ns"), base_ns, interval_ns)
            if 0 <= idx < interval_count:
                counts[idx] += 1
        max_seen = max(max_seen, seq)
    return counts


def analyze_data(args: argparse.Namespace) -> dict[str, Any]:
    send_rows = read_rows(args.send)
    recv_rows = read_rows(args.recv)
    skip_start_ns = getattr(args, "skip_start_ns", 0) or 0
    run_id, flow_id = infer_ids(send_rows, args.run_id, args.flow_id)

    if run_id is not None:
        send_rows = [row for row in send_rows if row.get("run_id") == run_id]
    if flow_id is not None:
        send_rows = [row for row in send_rows if row.get("flow_id") == flow_id]

    run_hash = hash_id(run_id) if run_id is not None else None
    flow_hash = hash_id(flow_id) if flow_id is not None else None

    decode_error_rows = [row for row in recv_rows if row.get("decode_error")]
    valid_recv = [row for row in recv_rows if not row.get("decode_error")]
    decode_errors = len(decode_error_rows)
    if run_hash is not None:
        valid_recv = [row for row in valid_recv if int_field(row, "run_id_hash", -1) == run_hash]
    if flow_hash is not None:
        valid_recv = [row for row in valid_recv if int_field(row, "flow_id_hash", -1) == flow_hash]

    send_rows, valid_recv, decode_error_rows, skip = apply_start_skip(
        send_rows, valid_recv, decode_error_rows, skip_start_ns
    )
    decode_errors = len(decode_error_rows)

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
    reorders = reorder_count(valid_recv)

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

    result = {
        "schema": "nsperf-analysis-v1",
        "run_id": run_id,
        "flow_id": flow_id,
        "skip_start_ms": skip["skip_start_ms"],
        "skip_start_ns": skip["skip_start_ns"],
        "skip_cutoff_ns": skip["skip_cutoff_ns"],
        "skipped_send_rows": skip["skipped_send_rows"],
        "skipped_recv_rows": skip["skipped_recv_rows"],
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

    if args.interval_ns is not None:
        result["intervals"] = build_intervals(
            args.interval_ns,
            send_rows,
            skipped_rows,
            attempt_rows,
            sent_ok,
            decode_error_rows,
            valid_recv,
            matched_receives,
            earliest_by_seq,
        )

    return result


def print_text(result: dict[str, Any]) -> None:
    counts = result["counts"]
    rates = result["rates"]
    timing = result["timing"]

    print(f"run_id: {result['run_id'] or 'n/a'}")
    print(f"flow_id: {result['flow_id'] or 'n/a'}")
    if result["skip_start_ns"]:
        cutoff = result["skip_cutoff_ns"] if result["skip_cutoff_ns"] is not None else "n/a"
        print(
            f"skip_start: {result['skip_start_ms']:.3f} ms "
            f"cutoff_ns={cutoff} "
            f"skipped_send_rows={result['skipped_send_rows']} "
            f"skipped_recv_rows={result['skipped_recv_rows']}"
        )
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

    intervals = result.get("intervals")
    if intervals is not None:
        print_interval_text(intervals)


def print_interval_text(intervals: dict[str, Any] | None) -> None:
    if intervals is None:
        print()
        print("intervals: n/a")
        return

    print()
    print(f"intervals: {intervals['interval_seconds']:.9g}s")
    for item in intervals["windows"]:
        send = item["send_window"]
        recv = item["receive_window"]
        delivery = item["delivery_for_send_window"]
        print(f"[{item['start_s']:.6f}-{item['end_s']:.6f}s]")
        print(
            "  "
            f"send_attempts={send['send_attempts']} "
            f"local_send_failures={send['local_send_failures']} "
            f"skipped_by_generator={send['skipped_by_generator']} "
            f"received_valid={recv['received_valid']} "
            f"receive_decode_errors={recv['receive_decode_errors']} "
            f"lost_after_successful_send={delivery['lost_after_successful_send']} "
            f"duplicates={recv['duplicates']} "
            f"reordered_receives={recv['reordered_receives']}"
        )
        print(
            "  "
            f"generated_bitrate={format_bitrate_value(send['generated_bps'])} "
            f"received_bitrate={format_bitrate_value(recv['received_bps'])}"
        )
        print(f"  sender_timing_error: {format_stats_ns(send['sender_timing_error_ns'])}")
        print(f"  sender_timing_jitter_abs: {format_stats_ns(send['sender_timing_jitter_abs_ns'])}")
        print(f"  skipped_timing_error: {format_stats_ns(send['skipped_timing_error_ns'])}")
        print(f"  host_local_latency_estimate: {format_stats_ns(delivery['host_local_latency_estimate_ns'])}")
        print(f"  host_local_latency_jitter_abs: {format_stats_ns(delivery['host_local_latency_jitter_abs_ns'])}")
        print(
            "  "
            "host_local_latency_jitter_rfc3550: "
            f"{format_ns(delivery['host_local_latency_jitter_rfc3550_ns']) if delivery['host_local_latency_jitter_rfc3550_ns'] is not None else 'n/a'}"
        )
        print(f"  receive_spacing_error: {format_stats_ns(recv['receive_spacing_error_ns'])}")


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
    parser.add_argument(
        "--interval",
        type=parse_interval_ns,
        dest="interval_ns",
        metavar="SECONDS",
        help="emit interval reports using fixed-width windows",
    )
    parser.add_argument(
        "--skip-start-ms",
        type=parse_skip_start_ms_ns,
        dest="skip_start_ns",
        metavar="MS",
        default=0,
        help="discard the first MS milliseconds from the selected stream before analysis",
    )
    parser.add_argument("--format", dest="output_format", choices=("text", "json"), default="text", help="output format")
    parser.add_argument("--json", action="store_const", const="json", dest="output_format", help="shortcut for --format json")
    return analyze(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
