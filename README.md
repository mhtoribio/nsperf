# nsperf

`nsperf` is a small UDP performance and tracing tool for local and network-namespace simulation experiments. It is intentionally fire-and-forget: the client does not open a TCP control connection, does not perform a UDP handshake, and does not require the server to be ready.

## Build

With Nix:

```sh
nix develop
go build -o bin/nsperf ./cmd/nsperf
```

Without Nix, install Go and Python 3, then run the same `go build` command.

Useful development checks:

```sh
go test ./...
golangci-lint run ./...
python3 -B -m py_compile tools/analyze.py
```

## Quick Start

Start a passive receiver:

```sh
mkdir -p logs
./bin/nsperf server --bind 0.0.0.0 --port 5201 --out logs/run1.recv.csv
```

In another shell, send UDP traffic. The client starts immediately by default and does not wait for the server:

```sh
./bin/nsperf client \
  --dst 127.0.0.1 \
  --port 5201 \
  --bitrate 10M \
  --duration 10s \
  --length 1200 \
  --run-id run1 \
  --flow-id flow-a \
  --out logs/run1.send.csv
```

Analyze the logs after the run:

```sh
python3 tools/analyze.py --send logs/run1.send.csv --recv logs/run1.recv.csv
python3 tools/analyze.py --send logs/run1.send.csv --recv logs/run1.recv.csv --json
```

For network namespaces, run the same commands through your namespace tooling, for example `ip netns exec <ns> ./bin/nsperf ...`.

## Scheduled Starts

`nsperf clock` prints the current `CLOCK_MONOTONIC` timestamp in nanoseconds. Scripts can add an offset and pass the result to `--start-mono-ns`:

```sh
start_ns=$(($(./bin/nsperf clock) + 5 * 1000 * 1000 * 1000))
./bin/nsperf client --dst 10.0.0.2 --port 5201 --bitrate 10M --duration 30s --length 1200 --start-mono-ns "$start_ns" --run-id run1 --flow-id flow-a --out logs/run1.send.csv
```

## Architecture

The implementation is split into five small pieces:

- `cmd/nsperf`: Go CLI binary with `server` and `client` subcommands.
- `internal/app`: client/server command behavior and CLI value parsing.
- `internal/clock`: Linux `CLOCK_MONOTONIC` timestamp helper.
- `internal/packet`: datagram header encoding/decoding and flow identifiers.
- `internal/logio`: CSV log writers with explicit schemas.
- `tools/analyze.py`: offline analyzer that joins client send logs and server receive logs.

The client is the source of truth for traffic generation. It records when each packet was scheduled, when `sendto` was attempted, whether the local send succeeded, and whether the generator was late. The server is passive and records one receive row per datagram. The analyzer compares the two logs after the run.

## CLI Summary

```text
nsperf server \
  --bind 0.0.0.0 \
  --port 5201 \
  --out logs/run1.recv.csv

nsperf client \
  --dst 10.0.0.2 \
  --port 5201 \
  --bitrate 10M \
  --duration 30s \
  --length 1200 \
  --flow-id flow-a \
  --run-id run1 \
  --out logs/run1.send.csv

nsperf clock
```

Client options:

- `--dst`: destination address.
- `--port`: destination UDP port.
- `--bitrate`: target datagram stream bitrate, with suffixes like `K`, `M`, `G`.
- `--duration`: send duration, using Go duration syntax.
- `--length`: UDP datagram size in bytes.
- `--start-mono-ns`: optional absolute monotonic start timestamp in nanoseconds.
- `--run-id`, `--flow-id`: identifiers written to packets and logs.
- `--out`: send CSV path.
- `--overrun-policy`: default `skip-missed`, avoiding artificial catch-up bursts.
- `--quiet`: suppress progress logs.

Server options:

- `--bind`: local bind address.
- `--port`: local UDP port.
- `--out`: receive CSV path.
- `--quiet`: suppress progress logs.

## Datagram Format

Each UDP datagram should begin with a fixed binary header, followed by padding bytes to reach `--length`.

```text
magic          uint32  "NSPF"
version        uint16
header_len     uint16
run_id_hash    uint64
flow_id_hash   uint64
sequence       uint64
scheduled_ns   uint64  monotonic timestamp selected by sender
send_attempt_ns uint64 monotonic timestamp immediately before sendto
payload_len    uint32
flags          uint32
```

The packet header carries compact hashes for matching. The full `run_id` and `flow_id` strings live in the logs.

## Log Formats

Use CSV with a header row. All timestamps are nanoseconds from the local monotonic clock unless noted. Since the main simulation target is one host, send and receive monotonic timestamps can be compared for a host-local one-way delay estimate.

Send log:

```text
schema,run_id,flow_id,seq,status,scheduled_ns,send_attempt_ns,send_done_ns,send_error,late_by_ns,bytes
```

Receive log:

```text
schema,run_id_hash,flow_id_hash,seq,scheduled_ns,send_attempt_ns,recv_ns,bytes,remote_addr,decode_error
```

`status` is `sent`, `send_error`, or `skipped`. `send_error` and `decode_error` are empty on success. Under the default no-catch-up policy, missed send slots are written as explicit `skipped` send-log rows with a sequence number and scheduled timestamp, but no send timestamp or byte count.

## Offline Analysis

`tools/analyze.py` reads one send log and one receive log and reports:

- packets scheduled, sent, locally failed, skipped, received, lost, duplicated;
- generated and received bitrate;
- sender timing error from `send_attempt_ns - scheduled_ns`;
- sender timing jitter as packet-to-packet variation in sender timing error;
- host-local one-way latency estimate from `recv_ns - send_attempt_ns`;
- host-local latency jitter as packet-to-packet delay variation;
- an RFC 3550-style smoothed jitter estimate over host-local latency samples;
- receive spacing error from actual receive spacing minus send-attempt spacing;
- duplicate and reorder counts based on sequence numbers;
- warnings when generator lateness or local send failures make network-loss conclusions unreliable.

The latency estimate assumes the client and server are on the same host and use the same monotonic clock domain, which is the intended network-namespace simulation setup. Across different machines, the throughput/loss/reordering results still make sense, but one-way latency numbers do not unless clocks are externally synchronized.

The analyzer defaults to human-readable text. Use `--format json` or `--json` for machine-readable output:

```sh
python3 tools/analyze.py --send logs/run1.send.csv --recv logs/run1.recv.csv --json
```

JSON output uses numeric nanosecond fields for timing stats and numeric `*_bps` fields for rates. Missing metrics are `null`.

## Development Shell

Enter the development shell with:

```sh
nix develop
```

The shell provides Go, `gopls`, `golangci-lint`, Python 3, `pytest`, and `shellcheck`.
