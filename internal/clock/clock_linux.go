package clock

import (
	"fmt"
	"syscall"
	"unsafe"
)

const clockMonotonic = 1

// NowNS returns CLOCK_MONOTONIC in nanoseconds.
func NowNS() (int64, error) {
	var ts syscall.Timespec
	_, _, errno := syscall.Syscall(syscall.SYS_CLOCK_GETTIME, uintptr(clockMonotonic), uintptr(unsafe.Pointer(&ts)), 0)
	if errno != 0 {
		return 0, fmt.Errorf("clock_gettime(CLOCK_MONOTONIC): %w", errno)
	}

	return ts.Sec*1_000_000_000 + ts.Nsec, nil
}
