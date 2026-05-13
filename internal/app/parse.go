package app

import (
	"fmt"
	"math"
	"strconv"
	"strings"
	"time"
)

const (
	OverrunSkipMissed = "skip-missed"
	OverrunSendLate   = "send-late"

	DefaultCSVBufferSize = "100MiB"
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

func ParseByteSize(s string) (int, error) {
	raw := strings.TrimSpace(s)
	if raw == "" {
		return 0, fmt.Errorf("byte size is required")
	}

	lower := strings.ToLower(raw)
	multiplier := float64(1)
	for _, suffix := range []struct {
		symbol     string
		multiplier float64
	}{
		{"bytes", 1},
		{"byte", 1},
		{"kib", 1024},
		{"mib", 1024 * 1024},
		{"gib", 1024 * 1024 * 1024},
		{"kb", 1000},
		{"mb", 1000 * 1000},
		{"gb", 1000 * 1000 * 1000},
		{"b", 1},
		{"k", 1000},
		{"m", 1000 * 1000},
		{"g", 1000 * 1000 * 1000},
	} {
		if strings.HasSuffix(lower, suffix.symbol) {
			multiplier = suffix.multiplier
			lower = strings.TrimSuffix(lower, suffix.symbol)
			break
		}
	}

	value, err := strconv.ParseFloat(strings.TrimSpace(lower), 64)
	if err != nil {
		return 0, fmt.Errorf("parse byte size %q: %w", s, err)
	}
	if math.IsNaN(value) || math.IsInf(value, 0) || value < 0 {
		return 0, fmt.Errorf("byte size must be non-negative")
	}

	bytes := value * multiplier
	maxInt := int64(^uint(0) >> 1)
	if bytes > float64(maxInt) {
		return 0, fmt.Errorf("byte size is too large")
	}
	if bytes != math.Trunc(bytes) {
		return 0, fmt.Errorf("byte size must resolve to whole bytes")
	}

	return int(bytes), nil
}
