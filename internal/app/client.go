package app

import (
	"context"
	"fmt"
	"io"
	"net"
	"os"
	"strconv"
	"time"

	"nsperf/internal/clock"
	"nsperf/internal/logio"
	"nsperf/internal/packet"
)

type ClientConfig struct {
	Dst           string
	Port          int
	Bitrate       string
	Duration      time.Duration
	Length        int
	StartMonoNS   int64
	RunID         string
	FlowID        string
	Out           string
	CSVBufferSize int
	OverrunPolicy string
	LateTolerance time.Duration
	Quiet         bool
}

func RunClient(ctx context.Context, cfg ClientConfig, stderr io.Writer) error {
	if cfg.Dst == "" {
		return fmt.Errorf("--dst is required")
	}
	if cfg.Port <= 0 || cfg.Port > 65535 {
		return fmt.Errorf("--port must be in range 1..65535")
	}
	if cfg.Length < packet.HeaderLen || cfg.Length > packet.MaxDatagram {
		return fmt.Errorf("--length must be in range %d..%d", packet.HeaderLen, packet.MaxDatagram)
	}
	if cfg.Duration <= 0 {
		return fmt.Errorf("--duration must be positive")
	}
	if cfg.LateTolerance < 0 {
		return fmt.Errorf("--late-tolerance must be non-negative")
	}
	if cfg.Out == "" {
		return fmt.Errorf("--out is required")
	}
	if cfg.CSVBufferSize < 0 {
		return fmt.Errorf("--csv-buffer-size must be non-negative")
	}
	if cfg.OverrunPolicy == "" {
		cfg.OverrunPolicy = OverrunSkipMissed
	}
	if cfg.OverrunPolicy != OverrunSkipMissed && cfg.OverrunPolicy != OverrunSendLate {
		return fmt.Errorf("unsupported --overrun-policy %q", cfg.OverrunPolicy)
	}
	if cfg.RunID == "" {
		cfg.RunID = defaultRunID()
	}
	if cfg.FlowID == "" {
		cfg.FlowID = "flow-0"
	}

	bitrate, err := ParseBitrate(cfg.Bitrate)
	if err != nil {
		return err
	}
	intervalNS, err := IntervalNS(cfg.Length, bitrate)
	if err != nil {
		return err
	}
	lateToleranceNS := cfg.LateTolerance.Nanoseconds()

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
	if err := logw.WriteSendHeader(); err != nil {
		return err
	}
	defer func() {
		_ = logw.Flush()
	}()

	endpoint := net.JoinHostPort(cfg.Dst, strconv.Itoa(cfg.Port))
	dstAddr, err := net.ResolveUDPAddr("udp", endpoint)
	if err != nil {
		return fmt.Errorf("resolve UDP destination %s: %w", endpoint, err)
	}
	conn, err := net.ListenUDP(udpNetworkFor(dstAddr), nil)
	if err != nil {
		return fmt.Errorf("open UDP sender socket: %w", err)
	}
	defer func() {
		_ = conn.Close()
	}()

	startNS := cfg.StartMonoNS
	if startNS == 0 {
		startNS, err = clock.NowNS()
		if err != nil {
			return err
		}
	}
	endNS := startNS + cfg.Duration.Nanoseconds()
	runHash := packet.HashID(cfg.RunID)
	flowHash := packet.HashID(cfg.FlowID)
	datagram := make([]byte, cfg.Length)
	header := packet.Header{
		RunIDHash:  runHash,
		FlowIDHash: flowHash,
		PayloadLen: uint32(cfg.Length - packet.HeaderLen),
	}

	if !cfg.Quiet {
		_, _ = fmt.Fprintf(stderr, "client run_id=%s flow_id=%s dst=%s bitrate=%d length=%d interval_ns=%d late_tolerance_ns=%d start_mono_ns=%d\n",
			cfg.RunID, cfg.FlowID, endpoint, bitrate, cfg.Length, intervalNS, lateToleranceNS, startNS)
	}

	var attempted, failed, skipped uint64
	sleeper := monotonicSleeper{}
	defer sleeper.Stop()
	for seq, scheduledNS := uint64(0), startNS; scheduledNS < endNS; {
		if err := sleeper.SleepUntil(ctx, scheduledNS); err != nil {
			return err
		}

		for cfg.OverrunPolicy == OverrunSkipMissed {
			nowNS, err := clock.NowNS()
			if err != nil {
				return err
			}
			if !shouldSkipMissedSlot(nowNS-scheduledNS, intervalNS, lateToleranceNS) {
				break
			}

			for scheduledNS < endNS && shouldSkipMissedSlot(nowNS-scheduledNS, intervalNS, lateToleranceNS) {
				if err := logw.WriteSend(logio.SendRow{
					RunID:       cfg.RunID,
					FlowID:      cfg.FlowID,
					Seq:         seq,
					Status:      logio.SendStatusSkipped,
					ScheduledNS: scheduledNS,
					LateByNS:    nowNS - scheduledNS,
				}); err != nil {
					return err
				}
				skipped++
				seq++
				scheduledNS += intervalNS
			}
			if scheduledNS >= endNS {
				break
			}
		}
		if scheduledNS >= endNS {
			break
		}

		nowNS, err := clock.NowNS()
		if err != nil {
			return err
		}

		sendAttemptNS := nowNS
		header.Sequence = seq
		header.ScheduledNS = scheduledNS
		header.SendAttemptNS = sendAttemptNS
		if err := packet.Encode(datagram, header); err != nil {
			return err
		}

		n, writeErr := conn.WriteToUDP(datagram, dstAddr)
		sendDoneNS, err := clock.NowNS()
		if err != nil {
			return err
		}

		sendError := ""
		if writeErr != nil {
			sendError = writeErr.Error()
			failed++
		} else if n != len(datagram) {
			sendError = fmt.Sprintf("short write %d/%d", n, len(datagram))
			failed++
		}
		attempted++

		if err := logw.WriteSend(logio.SendRow{
			RunID:         cfg.RunID,
			FlowID:        cfg.FlowID,
			Seq:           seq,
			Status:        sendStatus(sendError),
			ScheduledNS:   scheduledNS,
			SendAttemptNS: sendAttemptNS,
			SendDoneNS:    sendDoneNS,
			SendError:     sendError,
			LateByNS:      sendAttemptNS - scheduledNS,
			Bytes:         n,
		}); err != nil {
			return err
		}

		seq++
		scheduledNS += intervalNS
	}

	if err := logw.Flush(); err != nil {
		return err
	}
	if !cfg.Quiet {
		_, _ = fmt.Fprintf(stderr, "client complete attempted=%d failed=%d skipped=%d\n", attempted, failed, skipped)
	}
	return nil
}

func udpNetworkFor(addr *net.UDPAddr) string {
	if addr == nil || addr.IP == nil {
		return "udp"
	}
	if addr.IP.To4() != nil {
		return "udp4"
	}
	return "udp6"
}

func sendStatus(sendError string) string {
	if sendError != "" {
		return logio.SendStatusSendError
	}
	return logio.SendStatusSent
}

func shouldSkipMissedSlot(latenessNS, intervalNS, lateToleranceNS int64) bool {
	return latenessNS >= intervalNS+lateToleranceNS
}

type monotonicSleeper struct {
	timer *time.Timer
}

func (s *monotonicSleeper) SleepUntil(ctx context.Context, targetNS int64) error {
	nowNS, err := clock.NowNS()
	if err != nil {
		return err
	}
	if nowNS >= targetNS {
		return nil
	}

	delay := time.Duration(targetNS - nowNS)
	if s.timer == nil {
		s.timer = time.NewTimer(delay)
	} else {
		s.stopTimer()
		s.timer.Reset(delay)
	}

	select {
	case <-ctx.Done():
		s.stopTimer()
		return ctx.Err()
	case <-s.timer.C:
		return nil
	}
}

func (s *monotonicSleeper) Stop() {
	if s.timer != nil {
		s.stopTimer()
	}
}

func (s *monotonicSleeper) stopTimer() {
	if !s.timer.Stop() {
		select {
		case <-s.timer.C:
		default:
		}
	}
}

func newLogWriter(out *os.File, csvBufferSize int) *logio.Writer {
	if out == os.Stdout {
		return logio.NewWriter(out)
	}
	return logio.NewBufferedWriter(out, csvBufferSize)
}

func defaultRunID() string {
	return "run-" + time.Now().UTC().Format("20060102T150405Z")
}
