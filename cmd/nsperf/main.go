package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"syscall"
	"time"

	"nsperf/internal/app"
	"nsperf/internal/clock"
)

func main() {
	if err := run(os.Args[1:], os.Stdout, os.Stderr); err != nil {
		fmt.Fprintf(os.Stderr, "nsperf: %v\n", err)
		os.Exit(1)
	}
}

func run(args []string, stdout, stderr io.Writer) error {
	if len(args) == 0 {
		writeUsage(stderr)
		return fmt.Errorf("missing command")
	}

	switch args[0] {
	case "server":
		return runServer(args[1:], stderr)
	case "client":
		return runClient(args[1:], stderr)
	case "clock":
		now, err := clock.NowNS()
		if err != nil {
			return err
		}
		_, _ = fmt.Fprintln(stdout, now)
		return nil
	case "-h", "--help", "help":
		writeUsage(stdout)
		return nil
	default:
		writeUsage(stderr)
		return fmt.Errorf("unknown command %q", args[0])
	}
}

func runServer(args []string, stderr io.Writer) error {
	cfg := app.ServerConfig{}
	fs := flag.NewFlagSet("nsperf server", flag.ContinueOnError)
	fs.SetOutput(stderr)
	fs.StringVar(&cfg.Bind, "bind", "0.0.0.0", "UDP bind address")
	fs.IntVar(&cfg.Port, "port", 5201, "UDP bind port")
	fs.StringVar(&cfg.Out, "out", "nsperf.recv.csv", "receive CSV log path")
	fs.BoolVar(&cfg.Quiet, "quiet", false, "suppress progress logs")
	if err := fs.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return nil
		}
		return err
	}
	if fs.NArg() != 0 {
		return fmt.Errorf("unexpected server argument %q", fs.Arg(0))
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	return app.RunServer(ctx, cfg, stderr)
}

func runClient(args []string, stderr io.Writer) error {
	cfg := app.ClientConfig{}
	duration := "10s"
	lateTolerance := "10ms"

	fs := flag.NewFlagSet("nsperf client", flag.ContinueOnError)
	fs.SetOutput(stderr)
	fs.StringVar(&cfg.Dst, "dst", "", "destination address")
	fs.IntVar(&cfg.Port, "port", 5201, "destination UDP port")
	fs.StringVar(&cfg.Bitrate, "bitrate", "", "target bitrate in bits/s, e.g. 10M")
	fs.StringVar(&duration, "duration", duration, "send duration, e.g. 30s")
	fs.IntVar(&cfg.Length, "length", 1200, "UDP datagram length in bytes")
	fs.Int64Var(&cfg.StartMonoNS, "start-mono-ns", 0, "absolute CLOCK_MONOTONIC start timestamp in nanoseconds")
	fs.StringVar(&cfg.RunID, "run-id", "", "run identifier written to logs")
	fs.StringVar(&cfg.FlowID, "flow-id", "flow-0", "flow identifier written to logs")
	fs.StringVar(&cfg.Out, "out", "nsperf.send.csv", "send CSV log path")
	fs.StringVar(&cfg.OverrunPolicy, "overrun-policy", app.OverrunSkipMissed, "overrun policy: skip-missed or send-late")
	fs.StringVar(&lateTolerance, "late-tolerance", lateTolerance, "generator lateness tolerated by skip-missed before skipping, e.g. 10ms or 0s")
	fs.BoolVar(&cfg.Quiet, "quiet", false, "suppress progress logs")
	if err := fs.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return nil
		}
		return err
	}
	if fs.NArg() != 0 {
		return fmt.Errorf("unexpected client argument %q", fs.Arg(0))
	}

	parsedDuration, err := app.ParseDuration(duration)
	if err != nil {
		return fmt.Errorf("parse --duration: %w", err)
	}
	cfg.Duration = parsedDuration

	parsedLateTolerance, err := time.ParseDuration(lateTolerance)
	if err != nil {
		return fmt.Errorf("parse --late-tolerance: %w", err)
	}
	if parsedLateTolerance < 0 {
		return fmt.Errorf("--late-tolerance must be non-negative")
	}
	cfg.LateTolerance = parsedLateTolerance

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	return app.RunClient(ctx, cfg, stderr)
}

func writeUsage(w io.Writer) {
	_, _ = fmt.Fprintln(w, `Usage:
  nsperf server [options]
  nsperf client [options]
  nsperf clock

Examples:
  nsperf server --bind 0.0.0.0 --port 5201 --out logs/run1.recv.csv
  nsperf client --dst 10.0.0.2 --port 5201 --bitrate 10M --duration 30s --length 1200 --run-id run1 --flow-id flow-a --out logs/run1.send.csv
  nsperf clock`)
}
