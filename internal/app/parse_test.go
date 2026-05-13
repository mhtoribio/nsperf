package app

import "testing"

func TestParseBitrate(t *testing.T) {
	tests := map[string]uint64{
		"100":        100,
		"10K":        10_000,
		"1.5M":       1_500_000,
		"2gbps":      2_000_000_000,
		"250 kbit/s": 250_000,
	}

	for input, want := range tests {
		got, err := ParseBitrate(input)
		if err != nil {
			t.Fatalf("ParseBitrate(%q): %v", input, err)
		}
		if got != want {
			t.Fatalf("ParseBitrate(%q) = %d, want %d", input, got, want)
		}
	}
}

func TestIntervalNS(t *testing.T) {
	got, err := IntervalNS(1200, 10_000_000)
	if err != nil {
		t.Fatalf("IntervalNS: %v", err)
	}
	if got != 960_000 {
		t.Fatalf("IntervalNS = %d, want 960000", got)
	}
}

func TestParseByteSize(t *testing.T) {
	tests := map[string]int{
		"0":        0,
		"4096":     4096,
		"64KiB":    64 * 1024,
		"100MiB":   100 * 1024 * 1024,
		"1GiB":     1024 * 1024 * 1024,
		"100MB":    100 * 1000 * 1000,
		"1.5MiB":   1536 * 1024,
		"128 byte": 128,
	}

	for input, want := range tests {
		got, err := ParseByteSize(input)
		if err != nil {
			t.Fatalf("ParseByteSize(%q): %v", input, err)
		}
		if got != want {
			t.Fatalf("ParseByteSize(%q) = %d, want %d", input, got, want)
		}
	}
}

func TestParseByteSizeRejectsNegative(t *testing.T) {
	if _, err := ParseByteSize("-1"); err == nil {
		t.Fatal("ParseByteSize accepted a negative value")
	}
}

func TestParseByteSizeRejectsFractionalByte(t *testing.T) {
	if _, err := ParseByteSize("0.5"); err == nil {
		t.Fatal("ParseByteSize accepted a fractional byte value")
	}
}
