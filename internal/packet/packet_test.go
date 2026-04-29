package packet

import (
	"errors"
	"testing"
)

func TestBuildDecodeRoundTrip(t *testing.T) {
	want := Header{
		RunIDHash:     HashID("run-a"),
		FlowIDHash:    HashID("flow-a"),
		Sequence:      42,
		ScheduledNS:   1_000_000,
		SendAttemptNS: 1_000_125,
		Flags:         7,
	}

	buf, err := BuildDatagram(128, want)
	if err != nil {
		t.Fatalf("BuildDatagram: %v", err)
	}

	got, err := Decode(buf)
	if err != nil {
		t.Fatalf("Decode: %v", err)
	}

	if got.RunIDHash != want.RunIDHash ||
		got.FlowIDHash != want.FlowIDHash ||
		got.Sequence != want.Sequence ||
		got.ScheduledNS != want.ScheduledNS ||
		got.SendAttemptNS != want.SendAttemptNS ||
		got.Flags != want.Flags ||
		got.PayloadLen != 72 {
		t.Fatalf("decoded header mismatch:\n got: %#v\nwant: %#v with payload length 72", got, want)
	}
}

func TestDecodeRejectsInvalidPacket(t *testing.T) {
	_, err := Decode([]byte{1, 2, 3})
	if !errors.Is(err, ErrShortPacket) {
		t.Fatalf("expected ErrShortPacket, got %v", err)
	}

	buf, err := BuildDatagram(HeaderLen, Header{})
	if err != nil {
		t.Fatalf("BuildDatagram: %v", err)
	}
	buf[0] = 0

	_, err = Decode(buf)
	if !errors.Is(err, ErrBadMagic) {
		t.Fatalf("expected ErrBadMagic, got %v", err)
	}
}

func TestEncodeRejectsShortBuffer(t *testing.T) {
	err := Encode(make([]byte, HeaderLen-1), Header{})
	if !errors.Is(err, ErrShortPacket) {
		t.Fatalf("expected ErrShortPacket, got %v", err)
	}
}
