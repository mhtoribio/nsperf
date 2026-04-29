package app

import (
	"fmt"
	"strconv"
	"strings"
	"time"
)

const (
	OverrunSkipMissed = "skip-missed"
	OverrunSendLate   = "send-late"
)

func ParseBitrate(s string) (uint64, error) {
	raw := strings.TrimSpace(s)
	if raw == "" {
		return 0, fmt.Errorf("bitrate is required")
	}

	lower := strings.ToLower(raw)
	for _, suffix := range []string{"bits/s", "bit/s", "bps", "b/s"} {
		lower = strings.TrimSuffix(lower, suffix)
	}

	multiplier := float64(1)
	for _, suffix := range []struct {
		symbol     string
		multiplier float64
	}{
		{"k", 1_000},
		{"m", 1_000_000},
		{"g", 1_000_000_000},
	} {
		if strings.HasSuffix(lower, suffix.symbol) {
			multiplier = suffix.multiplier
			lower = strings.TrimSuffix(lower, suffix.symbol)
			break
		}
	}

	value, err := strconv.ParseFloat(strings.TrimSpace(lower), 64)
	if err != nil {
		return 0, fmt.Errorf("parse bitrate %q: %w", s, err)
	}
	if value <= 0 {
		return 0, fmt.Errorf("bitrate must be positive")
	}

	bitsPerSecond := value * multiplier
	if bitsPerSecond < 1 {
		return 0, fmt.Errorf("bitrate must be at least 1 bit/s")
	}

	return uint64(bitsPerSecond), nil
}

func IntervalNS(datagramBytes int, bitrateBitsPerSecond uint64) (int64, error) {
	if datagramBytes <= 0 {
		return 0, fmt.Errorf("datagram length must be positive")
	}
	if bitrateBitsPerSecond == 0 {
		return 0, fmt.Errorf("bitrate must be positive")
	}

	bitsPerDatagram := uint64(datagramBytes) * 8
	ns := (bitsPerDatagram*1_000_000_000 + bitrateBitsPerSecond/2) / bitrateBitsPerSecond
	if ns == 0 {
		return 0, fmt.Errorf("bitrate is too high for nanosecond scheduling at datagram length %d", datagramBytes)
	}

	return int64(ns), nil
}

func ParseDuration(s string) (time.Duration, error) {
	d, err := time.ParseDuration(s)
	if err != nil {
		return 0, err
	}
	if d <= 0 {
		return 0, fmt.Errorf("duration must be positive")
	}
	return d, nil
}
