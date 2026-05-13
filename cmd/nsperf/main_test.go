package main

import (
	"io"
	"strings"
	"testing"
)

func TestRunClientRejectsNegativeLateTolerance(t *testing.T) {
	err := runClient([]string{"--late-tolerance", "-1ns"}, io.Discard)
	if err == nil {
		t.Fatal("runClient accepted negative --late-tolerance")
	}
	if !strings.Contains(err.Error(), "--late-tolerance must be non-negative") {
		t.Fatalf("runClient returned %q, want --late-tolerance error", err)
	}
}
