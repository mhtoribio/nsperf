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
