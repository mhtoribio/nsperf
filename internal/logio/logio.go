package logio

import (
	"encoding/csv"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
)

const SchemaV1 = "nsperf-v1"

const (
	SendStatusSent      = "sent"
	SendStatusSendError = "send_error"
	SendStatusSkipped   = "skipped"
)

var SendHeader = []string{
	"schema",
	"run_id",
	"flow_id",
	"seq",
	"status",
	"scheduled_ns",
	"send_attempt_ns",
	"send_done_ns",
	"send_error",
	"late_by_ns",
	"bytes",
}

var RecvHeader = []string{
	"schema",
	"run_id_hash",
	"flow_id_hash",
	"seq",
	"scheduled_ns",
	"send_attempt_ns",
	"recv_ns",
	"bytes",
	"remote_addr",
	"decode_error",
}

type SendRow struct {
	Schema        string
	RunID         string
	FlowID        string
	Seq           uint64
	Status        string
	ScheduledNS   int64
	SendAttemptNS int64
	SendDoneNS    int64
	SendError     string
	LateByNS      int64
	Bytes         int
}

type RecvRow struct {
	Schema        string
	RunIDHash     uint64
	FlowIDHash    uint64
	Seq           uint64
	ScheduledNS   int64
	SendAttemptNS int64
	RecvNS        int64
	Bytes         int
	RemoteAddr    string
	DecodeError   string
}

type Writer struct {
	csv *csv.Writer
}

func Create(path string) (*os.File, error) {
	if path == "" {
		return nil, fmt.Errorf("output path is required")
	}
	if path == "-" {
		return os.Stdout, nil
	}

	dir := filepath.Dir(path)
	if dir != "." {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return nil, err
		}
	}

	return os.Create(path)
}

func NewWriter(w io.Writer) *Writer {
	return &Writer{csv: csv.NewWriter(w)}
}

func (w *Writer) Flush() error {
	w.csv.Flush()
	return w.csv.Error()
}

func (w *Writer) WriteSendHeader() error {
	return w.csv.Write(SendHeader)
}

func (w *Writer) WriteRecvHeader() error {
	return w.csv.Write(RecvHeader)
}

func (w *Writer) WriteSend(row SendRow) error {
	if row.Schema == "" {
		row.Schema = SchemaV1
	}
	if row.Status == "" {
		row.Status = SendStatusSent
		if row.SendError != "" {
			row.Status = SendStatusSendError
		}
	}

	sendAttemptNS := strconv.FormatInt(row.SendAttemptNS, 10)
	sendDoneNS := strconv.FormatInt(row.SendDoneNS, 10)
	lateByNS := strconv.FormatInt(row.LateByNS, 10)
	bytes := strconv.Itoa(row.Bytes)
	if row.Status == SendStatusSkipped {
		sendAttemptNS = ""
		sendDoneNS = ""
		bytes = ""
	}

	return w.csv.Write([]string{
		row.Schema,
		row.RunID,
		row.FlowID,
		strconv.FormatUint(row.Seq, 10),
		row.Status,
		strconv.FormatInt(row.ScheduledNS, 10),
		sendAttemptNS,
		sendDoneNS,
		row.SendError,
		lateByNS,
		bytes,
	})
}

func (w *Writer) WriteRecv(row RecvRow) error {
	if row.Schema == "" {
		row.Schema = SchemaV1
	}
	return w.csv.Write([]string{
		row.Schema,
		formatUintOrEmpty(row.RunIDHash, row.DecodeError),
		formatUintOrEmpty(row.FlowIDHash, row.DecodeError),
		formatUintOrEmpty(row.Seq, row.DecodeError),
		formatIntOrEmpty(row.ScheduledNS, row.DecodeError),
		formatIntOrEmpty(row.SendAttemptNS, row.DecodeError),
		strconv.FormatInt(row.RecvNS, 10),
		strconv.Itoa(row.Bytes),
		row.RemoteAddr,
		row.DecodeError,
	})
}

func formatUintOrEmpty(v uint64, decodeError string) string {
	if decodeError != "" {
		return ""
	}
	return strconv.FormatUint(v, 10)
}

func formatIntOrEmpty(v int64, decodeError string) string {
	if decodeError != "" {
		return ""
	}
	return strconv.FormatInt(v, 10)
}
