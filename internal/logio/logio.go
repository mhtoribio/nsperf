package logio

import (
	"bufio"
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
	csv        *csv.Writer
	buf        *bufio.Writer
	sendRecord []string
	recvRecord []string
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

func NewBufferedWriter(w io.Writer, size int) *Writer {
	if size <= 0 {
		return NewWriter(w)
	}

	buf := bufio.NewWriterSize(w, size)
	return &Writer{
		csv: csv.NewWriter(buf),
		buf: buf,
	}
}

func (w *Writer) Flush() error {
	w.csv.Flush()
	csvErr := w.csv.Error()
	if w.buf != nil {
		if err := w.buf.Flush(); err != nil && csvErr == nil {
			return err
		}
	}
	return csvErr
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

	record := w.sendRecord
	if record == nil {
		record = make([]string, len(SendHeader))
		w.sendRecord = record
	}
	record[0] = row.Schema
	record[1] = row.RunID
	record[2] = row.FlowID
	record[3] = strconv.FormatUint(row.Seq, 10)
	record[4] = row.Status
	record[5] = strconv.FormatInt(row.ScheduledNS, 10)
	record[6] = sendAttemptNS
	record[7] = sendDoneNS
	record[8] = row.SendError
	record[9] = lateByNS
	record[10] = bytes
	return w.csv.Write(record)
}

func (w *Writer) WriteRecv(row RecvRow) error {
	if row.Schema == "" {
		row.Schema = SchemaV1
	}
	record := w.recvRecord
	if record == nil {
		record = make([]string, len(RecvHeader))
		w.recvRecord = record
	}
	record[0] = row.Schema
	record[1] = formatUintOrEmpty(row.RunIDHash, row.DecodeError)
	record[2] = formatUintOrEmpty(row.FlowIDHash, row.DecodeError)
	record[3] = formatUintOrEmpty(row.Seq, row.DecodeError)
	record[4] = formatIntOrEmpty(row.ScheduledNS, row.DecodeError)
	record[5] = formatIntOrEmpty(row.SendAttemptNS, row.DecodeError)
	record[6] = strconv.FormatInt(row.RecvNS, 10)
	record[7] = strconv.Itoa(row.Bytes)
	record[8] = row.RemoteAddr
	record[9] = row.DecodeError
	return w.csv.Write(record)
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
