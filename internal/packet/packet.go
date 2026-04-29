package packet

import (
	"encoding/binary"
	"errors"
	"fmt"
	"hash/fnv"
)

const (
	Magic       uint32 = 0x4e535046 // "NSPF"
	Version     uint16 = 1
	HeaderLen          = 56
	MaxDatagram        = 65507
)

var (
	ErrShortPacket        = errors.New("short packet")
	ErrBadMagic           = errors.New("bad magic")
	ErrUnsupportedVersion = errors.New("unsupported version")
	ErrBadHeaderLength    = errors.New("bad header length")
)

type Header struct {
	RunIDHash     uint64
	FlowIDHash    uint64
	Sequence      uint64
	ScheduledNS   int64
	SendAttemptNS int64
	PayloadLen    uint32
	Flags         uint32
}

func HashID(id string) uint64 {
	h := fnv.New64a()
	_, _ = h.Write([]byte(id))
	return h.Sum64()
}

func BuildDatagram(length int, h Header) ([]byte, error) {
	if length < HeaderLen {
		return nil, fmt.Errorf("datagram length %d is smaller than header length %d", length, HeaderLen)
	}
	if length > MaxDatagram {
		return nil, fmt.Errorf("datagram length %d exceeds maximum UDP payload %d", length, MaxDatagram)
	}

	buf := make([]byte, length)
	h.PayloadLen = uint32(length - HeaderLen)
	if err := Encode(buf, h); err != nil {
		return nil, err
	}
	return buf, nil
}

func Encode(buf []byte, h Header) error {
	if len(buf) < HeaderLen {
		return fmt.Errorf("%w: got %d bytes, need %d", ErrShortPacket, len(buf), HeaderLen)
	}

	binary.BigEndian.PutUint32(buf[0:4], Magic)
	binary.BigEndian.PutUint16(buf[4:6], Version)
	binary.BigEndian.PutUint16(buf[6:8], HeaderLen)
	binary.BigEndian.PutUint64(buf[8:16], h.RunIDHash)
	binary.BigEndian.PutUint64(buf[16:24], h.FlowIDHash)
	binary.BigEndian.PutUint64(buf[24:32], h.Sequence)
	binary.BigEndian.PutUint64(buf[32:40], uint64(h.ScheduledNS))
	binary.BigEndian.PutUint64(buf[40:48], uint64(h.SendAttemptNS))
	binary.BigEndian.PutUint32(buf[48:52], h.PayloadLen)
	binary.BigEndian.PutUint32(buf[52:56], h.Flags)
	return nil
}

func Decode(buf []byte) (Header, error) {
	if len(buf) < HeaderLen {
		return Header{}, fmt.Errorf("%w: got %d bytes, need %d", ErrShortPacket, len(buf), HeaderLen)
	}
	if got := binary.BigEndian.Uint32(buf[0:4]); got != Magic {
		return Header{}, fmt.Errorf("%w: 0x%08x", ErrBadMagic, got)
	}
	if got := binary.BigEndian.Uint16(buf[4:6]); got != Version {
		return Header{}, fmt.Errorf("%w: %d", ErrUnsupportedVersion, got)
	}
	if got := binary.BigEndian.Uint16(buf[6:8]); got != HeaderLen {
		return Header{}, fmt.Errorf("%w: %d", ErrBadHeaderLength, got)
	}

	payloadLen := binary.BigEndian.Uint32(buf[48:52])
	if int(payloadLen)+HeaderLen > len(buf) {
		return Header{}, fmt.Errorf("payload length %d exceeds datagram size %d", payloadLen, len(buf))
	}

	return Header{
		RunIDHash:     binary.BigEndian.Uint64(buf[8:16]),
		FlowIDHash:    binary.BigEndian.Uint64(buf[16:24]),
		Sequence:      binary.BigEndian.Uint64(buf[24:32]),
		ScheduledNS:   int64(binary.BigEndian.Uint64(buf[32:40])),
		SendAttemptNS: int64(binary.BigEndian.Uint64(buf[40:48])),
		PayloadLen:    payloadLen,
		Flags:         binary.BigEndian.Uint32(buf[52:56]),
	}, nil
}
