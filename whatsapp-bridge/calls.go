package main

// Call log. The phone shares its call history with linked devices: every finished call arrives as a
// call_log app state mutation (and older calls in history sync), with start time, duration, result
// and participants. The bridge keeps one row per call. Only this metadata exists; a linked device
// never receives the audio or video of a call.

import (
	"context"
	"encoding/json"
	"net/http"
	"sync"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/appstate"
	"go.mau.fi/whatsmeow/proto/waSyncAction"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
)

func (store *MessageStore) initCalls() error {
	_, err := store.db.Exec(`
		CREATE TABLE IF NOT EXISTS calls (
			call_id TEXT PRIMARY KEY,
			chat_jid TEXT,
			group_jid TEXT,
			creator TEXT,
			is_incoming BOOLEAN,
			is_video BOOLEAN,
			result TEXT,
			start_time TIMESTAMP,
			duration_seconds INTEGER,
			participants TEXT
		);
	`)
	return err
}

type CallParticipant struct {
	JID    string `json:"jid"`
	Result string `json:"result"`
}

// callStart reads the record's start time, which WhatsApp sends in Unix seconds.
func callStart(record *waSyncAction.CallLogRecord) time.Time {
	return time.Unix(record.GetStartTime(), 0)
}

// callChat is the chat a call belongs to: the group, or the other person of a 1:1 call.
func callChat(client *whatsmeow.Client, record *waSyncAction.CallLogRecord) string {
	if record.GetGroupJID() != "" {
		return record.GetGroupJID()
	}
	if record.GetIsIncoming() {
		return record.GetCallCreatorJID()
	}
	for _, p := range record.GetParticipants() {
		jid, err := types.ParseJID(p.GetUserJID())
		if err != nil || isOwnJID(client, jid) {
			continue
		}
		return jid.ToNonAD().String()
	}
	return record.GetCallCreatorJID()
}

func isOwnJID(client *whatsmeow.Client, jid types.JID) bool {
	if client.Store.ID != nil && jid.User == client.Store.ID.User {
		return true
	}
	return !client.Store.LID.IsEmpty() && jid.User == client.Store.LID.User
}

// StoreCallLog keeps a call; a later record of the same call (one that ended) replaces it.
func (store *MessageStore) StoreCallLog(client *whatsmeow.Client, record *waSyncAction.CallLogRecord) error {
	participants := make([]CallParticipant, 0, len(record.GetParticipants()))
	for _, p := range record.GetParticipants() {
		participants = append(participants, CallParticipant{JID: p.GetUserJID(), Result: p.GetCallResult().String()})
	}
	encoded, err := json.Marshal(participants)
	if err != nil {
		return err
	}
	_, err = store.db.Exec(
		`INSERT OR REPLACE INTO calls
		(call_id, chat_jid, group_jid, creator, is_incoming, is_video, result, start_time, duration_seconds, participants)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		record.GetCallID(), callChat(client, record), record.GetGroupJID(), record.GetCallCreatorJID(),
		record.GetIsIncoming(), record.GetIsVideo(), record.GetCallResult().String(),
		callStart(record), record.GetDuration(), string(encoded),
	)
	return err
}

func storeCallLogs(client *whatsmeow.Client, store *MessageStore, records []*waSyncAction.CallLogRecord, logger waLog.Logger) {
	for _, record := range records {
		if record.GetCallID() == "" {
			continue
		}
		if err := store.StoreCallLog(client, record); err != nil {
			logger.Warnf("Failed to store call %s: %v", record.GetCallID(), err)
		}
	}
}

// seenIndexes counts app state mutations per index during a resync, so the response shows what the
// phone shares when no call_log arrives.
var seenIndexes struct {
	sync.Mutex
	counts map[string]int
}

// handleCallLogAppState stores a call_log app state mutation; other mutations are ignored.
func handleCallLogAppState(client *whatsmeow.Client, store *MessageStore, evt *events.AppState, logger waLog.Logger) {
	seenIndexes.Lock()
	if seenIndexes.counts != nil && len(evt.Index) > 0 {
		seenIndexes.counts[evt.Index[0]]++
	}
	seenIndexes.Unlock()
	if record := evt.GetCallLogAction().GetCallLogRecord(); record != nil {
		storeCallLogs(client, store, []*waSyncAction.CallLogRecord{record}, logger)
	}
}

func (store *MessageStore) countCalls() int {
	var n int
	store.db.QueryRow("SELECT COUNT(*) FROM calls").Scan(&n)
	return n
}

type CallResyncResponse struct {
	Success bool           `json:"success"`
	Message string         `json:"message"`
	Calls   int            `json:"calls"`
	Indexes map[string]int `json:"indexes"`
	Errors  []string       `json:"errors,omitempty"`
}

// registerCallRoutes adds POST /api/calls/resync: it fetches every app state snapshot again, so the
// call log of before this bridge stored calls comes in as well.
func registerCallRoutes(client *whatsmeow.Client, store *MessageStore) {
	http.HandleFunc("/api/calls/resync", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if !client.IsConnected() {
			writeJSON(w, false, CallResyncResponse{Message: "Not connected to WhatsApp"})
			return
		}
		before := store.countCalls()
		resp := CallResyncResponse{Success: true}
		seenIndexes.Lock()
		seenIndexes.counts = map[string]int{}
		seenIndexes.Unlock()
		// A full sync emits no events unless asked to. The critical patches (contacts, blocks) hold
		// no calls and are left alone, so whatsmeow keeps mass-inserting contacts as usual.
		client.EmitAppStateEventsOnFullSync = true
		for _, name := range []appstate.WAPatchName{appstate.WAPatchRegularHigh, appstate.WAPatchRegular, appstate.WAPatchRegularLow} {
			if err := client.FetchAppState(context.Background(), name, true, false); err != nil {
				resp.Errors = append(resp.Errors, string(name)+": "+err.Error())
			}
		}
		client.EmitAppStateEventsOnFullSync = false
		resp.Calls = store.countCalls()
		seenIndexes.Lock()
		resp.Indexes, seenIndexes.counts = seenIndexes.counts, nil
		seenIndexes.Unlock()
		resp.Message = "Call log resynced"
		if resp.Calls == before && len(resp.Errors) > 0 {
			resp.Success = false
		}
		writeJSON(w, resp.Success, resp)
	})
}
