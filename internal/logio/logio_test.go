package logio

import (
	"bytes"
	"encoding/csv"
	"testing"
)

func TestWriteSend(t *testing.T) {
	var buf bytes.Buffer
	w := NewWriter(&buf)

	if err := w.WriteSendHeader(); err != nil {
		t.Fatalf("WriteSendHeader: %v", err)
	}
	if err := w.WriteSend(SendRow{
		RunID:         "run-a",
		FlowID:        "flow-a",
		Seq:           3,
		ScheduledNS:   10,
		SendAttemptNS: 12,
		SendDoneNS:    13,
		LateByNS:      2,
		Bytes:         1200,
	}); err != nil {
		t.Fatalf("WriteSend: %v", err)
	}
	if err := w.Flush(); err != nil {
		t.Fatalf("Flush: %v", err)
	}

	records, err := csv.NewReader(bytes.NewReader(buf.Bytes())).ReadAll()
	if err != nil {
		t.Fatalf("ReadAll: %v", err)
	}
	if len(records) != 2 {
		t.Fatalf("got %d records, want 2", len(records))
	}
	if records[1][0] != SchemaV1 || records[1][1] != "run-a" || records[1][3] != "3" || records[1][4] != SendStatusSent || records[1][10] != "1200" {
		t.Fatalf("unexpected send row: %#v", records[1])
	}
}

func TestWriteSkippedSend(t *testing.T) {
	var buf bytes.Buffer
	w := NewWriter(&buf)

	if err := w.WriteSendHeader(); err != nil {
		t.Fatalf("WriteSendHeader: %v", err)
	}
	if err := w.WriteSend(SendRow{
		RunID:       "run-a",
		FlowID:      "flow-a",
		Seq:         4,
		Status:      SendStatusSkipped,
		ScheduledNS: 20,
		LateByNS:    5,
	}); err != nil {
		t.Fatalf("WriteSend: %v", err)
	}
	if err := w.Flush(); err != nil {
		t.Fatalf("Flush: %v", err)
	}

	records, err := csv.NewReader(bytes.NewReader(buf.Bytes())).ReadAll()
	if err != nil {
		t.Fatalf("ReadAll: %v", err)
	}
	if records[1][4] != SendStatusSkipped || records[1][6] != "" || records[1][7] != "" || records[1][9] != "5" || records[1][10] != "" {
		t.Fatalf("unexpected skipped send row: %#v", records[1])
	}
}

func TestWriteRecvDecodeErrorLeavesPacketFieldsEmpty(t *testing.T) {
	var buf bytes.Buffer
	w := NewWriter(&buf)

	if err := w.WriteRecvHeader(); err != nil {
		t.Fatalf("WriteRecvHeader: %v", err)
	}
	if err := w.WriteRecv(RecvRow{
		RecvNS:      100,
		Bytes:       8,
		RemoteAddr:  "127.0.0.1:12345",
		DecodeError: "short packet",
	}); err != nil {
		t.Fatalf("WriteRecv: %v", err)
	}
	if err := w.Flush(); err != nil {
		t.Fatalf("Flush: %v", err)
	}

	records, err := csv.NewReader(bytes.NewReader(buf.Bytes())).ReadAll()
	if err != nil {
		t.Fatalf("ReadAll: %v", err)
	}
	if records[1][1] != "" || records[1][3] != "" || records[1][6] != "100" || records[1][9] != "short packet" {
		t.Fatalf("unexpected recv row: %#v", records[1])
	}
}

func TestBufferedWriterFlushesOuterBuffer(t *testing.T) {
	var buf bytes.Buffer
	w := NewBufferedWriter(&buf, 4096)

	if w.buf == nil {
		t.Fatal("NewBufferedWriter did not install an outer buffer")
	}
	if w.buf.Size() != 4096 {
		t.Fatalf("outer buffer size = %d, want 4096", w.buf.Size())
	}
	if err := w.WriteSendHeader(); err != nil {
		t.Fatalf("WriteSendHeader: %v", err)
	}

	if err := w.Flush(); err != nil {
		t.Fatalf("Flush: %v", err)
	}
	if buf.Len() == 0 {
		t.Fatal("Writer.Flush did not flush the outer buffer")
	}
}
