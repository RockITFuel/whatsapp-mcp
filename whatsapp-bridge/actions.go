package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"go.mau.fi/whatsmeow"
	waProto "go.mau.fi/whatsmeow/binary/proto"
	"go.mau.fi/whatsmeow/types"
	"google.golang.org/protobuf/proto"
)

// Actions on messages that already exist (quote, react, edit, revoke) find them in messages.db by id.

// storedMessage is what messages.db knows about a message: enough to address it again.
type storedMessage struct {
	ID        string
	Chat      types.JID
	Sender    string // as stored: a bare user, sometimes a full JID
	Content   string
	MediaType string
	Timestamp time.Time
	IsFromMe  bool
}

// findMessage looks a message up by id. Ids are unique in practice, but the primary key is
// (id, chat_jid), so a chat given by the caller wins when two chats share an id.
func (store *MessageStore) findMessage(id, chatHint string) (storedMessage, error) {
	var m storedMessage
	var chat string
	err := store.db.QueryRow(
		`SELECT id, chat_jid, COALESCE(sender, ''), COALESCE(content, ''), COALESCE(media_type, ''), timestamp, is_from_me
		FROM messages WHERE id = ? ORDER BY chat_jid = ? DESC, timestamp DESC LIMIT 1`,
		id, chatHint,
	).Scan(&m.ID, &chat, &m.Sender, &m.Content, &m.MediaType, &m.Timestamp, &m.IsFromMe)
	if err != nil {
		return m, fmt.Errorf("message %s not found: %v", id, err)
	}
	if m.Chat, err = types.ParseJID(chat); err != nil {
		return m, fmt.Errorf("message %s has a bad chat JID %q: %v", id, chat, err)
	}
	return m, nil
}

// senderJID is the full JID of whoever wrote the message, which a quote or a reaction must name.
// messages.db mostly keeps senders as a bare user, which can be a phone number or a LID.
func senderJID(client *whatsmeow.Client, m storedMessage) types.JID {
	if m.IsFromMe {
		if m.Chat.Server == types.HiddenUserServer && !client.Store.LID.IsEmpty() {
			return client.Store.LID.ToNonAD()
		}
		return client.Store.ID.ToNonAD()
	}
	if m.Chat.Server == types.DefaultUserServer || m.Chat.Server == types.HiddenUserServer {
		return m.Chat // in a direct chat the other side wrote it
	}
	if strings.Contains(m.Sender, "@") {
		if jid, err := types.ParseJID(m.Sender); err == nil {
			return jid.ToNonAD()
		}
	}
	// A bare user in a group: a LID if whatsmeow knows a phone number for it, else a phone number.
	asLID := types.JID{User: m.Sender, Server: types.HiddenUserServer}
	if pn, err := client.Store.LIDs.GetPNForLID(context.Background(), asLID); err == nil && !pn.IsEmpty() {
		return asLID
	}
	return types.JID{User: m.Sender, Server: types.DefaultUserServer}
}

// quoteOf is the context that makes a new message a reply to m. The quoted body is m's text,
// or its media type for a file: enough for the preview, the stanza id links to the original.
func quoteOf(client *whatsmeow.Client, m storedMessage) *waProto.ContextInfo {
	body := m.Content
	if body == "" && m.MediaType != "" {
		body = "[" + m.MediaType + "]"
	}
	return &waProto.ContextInfo{
		StanzaID:      proto.String(m.ID),
		Participant:   proto.String(senderJID(client, m).String()),
		QuotedMessage: &waProto.Message{Conversation: proto.String(body)},
	}
}

// withContext puts ctx on whatever kind of message msg is. A plain text has nowhere to carry it,
// so it becomes an extended text message, as WhatsApp itself sends replies.
func withContext(msg *waProto.Message, ctx *waProto.ContextInfo) {
	switch {
	case msg.ImageMessage != nil:
		msg.ImageMessage.ContextInfo = ctx
	case msg.VideoMessage != nil:
		msg.VideoMessage.ContextInfo = ctx
	case msg.AudioMessage != nil:
		msg.AudioMessage.ContextInfo = ctx
	case msg.DocumentMessage != nil:
		msg.DocumentMessage.ContextInfo = ctx
	case msg.ExtendedTextMessage != nil:
		msg.ExtendedTextMessage.ContextInfo = ctx
	default:
		msg.ExtendedTextMessage = &waProto.ExtendedTextMessage{Text: proto.String(msg.GetConversation()), ContextInfo: ctx}
		msg.Conversation = nil
	}
}

// MessageActionRequest addresses an existing message; Emoji and Message are used by react and edit.
type MessageActionRequest struct {
	MessageID string `json:"message_id"`
	ChatJID   string `json:"chat_jid,omitempty"`
	Emoji     string `json:"emoji,omitempty"`
	Message   string `json:"message,omitempty"`
}

// react puts an emoji under a message; an empty emoji takes our reaction away.
func react(client *whatsmeow.Client, store *MessageStore, req MessageActionRequest) (bool, string) {
	m, err := store.findMessage(req.MessageID, req.ChatJID)
	if err != nil {
		return false, err.Error()
	}
	reaction := client.BuildReaction(m.Chat, senderJID(client, m), m.ID, req.Emoji)
	if _, err := client.SendMessage(context.Background(), m.Chat, reaction); err != nil {
		return false, fmt.Sprintf("Error sending reaction: %v", err)
	}
	if req.Emoji == "" {
		return true, fmt.Sprintf("Reaction removed from %s", m.ID)
	}
	return true, fmt.Sprintf("Reacted %s to %s", req.Emoji, m.ID)
}

// editMessage replaces the text of a message we sent. WhatsApp only accepts edits of our own
// messages within its edit window, so both are checked here for a clear error instead of a silent no-op.
func editMessage(client *whatsmeow.Client, store *MessageStore, req MessageActionRequest) (bool, string) {
	m, err := store.findMessage(req.MessageID, req.ChatJID)
	if err != nil {
		return false, err.Error()
	}
	if !m.IsFromMe {
		return false, "Only our own messages can be edited"
	}
	if m.MediaType != "" {
		return false, "Only text messages can be edited"
	}
	if time.Since(m.Timestamp) > whatsmeow.EditWindow {
		return false, fmt.Sprintf("Too late to edit: WhatsApp allows edits for %s after sending", whatsmeow.EditWindow)
	}
	edit := client.BuildEdit(m.Chat, m.ID, &waProto.Message{Conversation: proto.String(req.Message)})
	if _, err := client.SendMessage(context.Background(), m.Chat, edit); err != nil {
		return false, fmt.Sprintf("Error editing message: %v", err)
	}
	if _, err := store.db.Exec("UPDATE messages SET content = ? WHERE id = ? AND chat_jid = ?", req.Message, m.ID, m.Chat.String()); err != nil {
		fmt.Printf("Failed to store edited message: %v\n", err)
	}
	return true, fmt.Sprintf("Message %s edited", m.ID)
}

// revokeMessage deletes a message we sent for everyone. Others' messages are left alone even
// where we are group admin: a revoke cannot be taken back.
func revokeMessage(client *whatsmeow.Client, store *MessageStore, req MessageActionRequest) (bool, string) {
	m, err := store.findMessage(req.MessageID, req.ChatJID)
	if err != nil {
		return false, err.Error()
	}
	if !m.IsFromMe {
		return false, "Only our own messages can be deleted"
	}
	if _, err := client.SendMessage(context.Background(), m.Chat, client.BuildRevoke(m.Chat, types.EmptyJID, m.ID)); err != nil {
		return false, fmt.Sprintf("Error deleting message: %v", err)
	}
	// Keep the row, so the history shows that something was said and taken back.
	if _, err := store.db.Exec(
		"UPDATE messages SET content = '[deleted]', media_type = '', filename = '', url = '', media_key = NULL, file_sha256 = NULL, file_enc_sha256 = NULL, file_length = 0 WHERE id = ? AND chat_jid = ?",
		m.ID, m.Chat.String(),
	); err != nil {
		fmt.Printf("Failed to store deleted message: %v\n", err)
	}
	return true, fmt.Sprintf("Message %s deleted for everyone", m.ID)
}

// postHandler decodes a JSON POST body into T and answers with what handle returns.
func postHandler[T any](handle func(T) (bool, string)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		var req T
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "Invalid request format", http.StatusBadRequest)
			return
		}
		success, message := handle(req)
		writeJSON(w, success, SendMessageResponse{Success: success, Message: message})
	}
}

func writeJSON(w http.ResponseWriter, success bool, body any) {
	w.Header().Set("Content-Type", "application/json")
	if !success {
		w.WriteHeader(http.StatusInternalServerError)
	}
	json.NewEncoder(w).Encode(body)
}

// registerActionRoutes adds the endpoints for acting on existing messages and groups.
func registerActionRoutes(client *whatsmeow.Client, store *MessageStore) {
	http.HandleFunc("/api/react", postHandler(func(req MessageActionRequest) (bool, string) {
		if req.MessageID == "" {
			return false, "message_id is required"
		}
		if !client.IsConnected() {
			return false, "Not connected to WhatsApp"
		}
		return react(client, store, req)
	}))
	http.HandleFunc("/api/edit", postHandler(func(req MessageActionRequest) (bool, string) {
		if req.MessageID == "" || req.Message == "" {
			return false, "message_id and message are required"
		}
		if !client.IsConnected() {
			return false, "Not connected to WhatsApp"
		}
		return editMessage(client, store, req)
	}))
	http.HandleFunc("/api/revoke", postHandler(func(req MessageActionRequest) (bool, string) {
		if req.MessageID == "" {
			return false, "message_id is required"
		}
		if !client.IsConnected() {
			return false, "Not connected to WhatsApp"
		}
		return revokeMessage(client, store, req)
	}))
}
