package app

import (
	"context"
	"fmt"
	"io"
	"net"
	"os"
	"strconv"

	"nsperf/internal/clock"
	"nsperf/internal/logio"
	"nsperf/internal/packet"
)

type ServerConfig struct {
	Bind          string
	Port          int
	Out           string
	CSVBufferSize int
	Quiet         bool
}

func RunServer(ctx context.Context, cfg ServerConfig, stderr io.Writer) error {
	if cfg.Bind == "" {
		cfg.Bind = "0.0.0.0"
	}
	if cfg.Port <= 0 || cfg.Port > 65535 {
		return fmt.Errorf("--port must be in range 1..65535")
	}
	if cfg.Out == "" {
		return fmt.Errorf("--out is required")
	}
	if cfg.CSVBufferSize < 0 {
		return fmt.Errorf("--csv-buffer-size must be non-negative")
	}

	out, err := logio.Create(cfg.Out)
	if err != nil {
		return err
	}
	if out != os.Stdout {
		defer func() {
			_ = out.Close()
		}()
	}

	logw := newLogWriter(out, cfg.CSVBufferSize)
	if err := logw.WriteRecvHeader(); err != nil {
		return err
	}
	defer func() {
		_ = logw.Flush()
	}()

	bindAddr, err := net.ResolveUDPAddr("udp", net.JoinHostPort(cfg.Bind, strconv.Itoa(cfg.Port)))
	if err != nil {
		return err
	}
	conn, err := net.ListenUDP("udp", bindAddr)
	if err != nil {
		return err
	}
	defer func() {
		_ = conn.Close()
	}()

	go func() {
		<-ctx.Done()
		_ = conn.Close()
	}()

	if !cfg.Quiet {
		_, _ = fmt.Fprintf(stderr, "server listening on %s\n", conn.LocalAddr())
	}

	buf := make([]byte, 65535)
	var received, decodeErrors uint64
	for {
		n, remote, err := conn.ReadFromUDP(buf)
		recvNS, clockErr := clock.NowNS()
		if clockErr != nil {
			return clockErr
		}
		if err != nil {
			if ctx.Err() != nil {
				if flushErr := logw.Flush(); flushErr != nil {
					return flushErr
				}
				if !cfg.Quiet {
					_, _ = fmt.Fprintf(stderr, "server stopped received=%d decode_errors=%d\n", received, decodeErrors)
				}
				return nil
			}
			return err
		}

		row := logio.RecvRow{
			RecvNS:     recvNS,
			Bytes:      n,
			RemoteAddr: remote.String(),
		}
		h, err := packet.Decode(buf[:n])
		if err != nil {
			row.DecodeError = err.Error()
			decodeErrors++
		} else {
			row.RunIDHash = h.RunIDHash
			row.FlowIDHash = h.FlowIDHash
			row.Seq = h.Sequence
			row.ScheduledNS = h.ScheduledNS
			row.SendAttemptNS = h.SendAttemptNS
		}

		if err := logw.WriteRecv(row); err != nil {
			return err
		}
		received++
	}
}
