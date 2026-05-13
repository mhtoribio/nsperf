package app

import (
	"testing"
	"time"
)

func TestShouldSkipMissedSlot(t *testing.T) {
	intervalNS := int64(time.Millisecond)
	toleranceNS := int64(10 * time.Millisecond)

	tests := []struct {
		name            string
		latenessNS      int64
		lateToleranceNS int64
		want            bool
	}{
		{
			name:            "zero tolerance before interval",
			latenessNS:      intervalNS - 1,
			lateToleranceNS: 0,
			want:            false,
		},
		{
			name:            "zero tolerance at interval",
			latenessNS:      intervalNS,
			lateToleranceNS: 0,
			want:            true,
		},
		{
			name:            "tolerance before threshold",
			latenessNS:      intervalNS + toleranceNS - 1,
			lateToleranceNS: toleranceNS,
			want:            false,
		},
		{
			name:            "tolerance at threshold",
			latenessNS:      intervalNS + toleranceNS,
			lateToleranceNS: toleranceNS,
			want:            true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := shouldSkipMissedSlot(tt.latenessNS, intervalNS, tt.lateToleranceNS)
			if got != tt.want {
				t.Fatalf("shouldSkipMissedSlot(%d, %d, %d) = %v, want %v", tt.latenessNS, intervalNS, tt.lateToleranceNS, got, tt.want)
			}
		})
	}
}
