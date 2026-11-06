package main

// Poll results. A vote arrives encrypted and names its choices only as SHA-256 hashes of the
// option texts, so the bridge keeps every poll's options (polls) to decode votes, and the latest
// vote per voter (poll_votes): a voter who changes their mind replaces their earlier vote.
// Votes are decoded the moment they arrive; one that arrives before its poll is known is lost.

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
)

func (store *MessageStore) initPolls() error {
	_, err := store.db.Exec(`
		CREATE TABLE IF NOT EXISTS polls (
			id TEXT,
			chat_jid TEXT,
			question TEXT,
			options TEXT,
			multiple BOOLEAN,
			created_at TIMESTAMP,
			PRIMARY KEY (id, chat_jid)
		);
		CREATE TABLE IF NOT EXISTS poll_votes (
			poll_id TEXT,
			chat_jid TEXT,
			voter TEXT,
			options TEXT,
			voted_at TIMESTAMP,
			PRIMARY KEY (poll_id, chat_jid, voter)
		);
	`)
	return err
}

// StorePoll keeps a poll's question and options (as JSON) so its votes can be decoded later.
func (store *MessageStore) StorePoll(id, chatJID, question string, options []string, multiple bool, createdAt time.Time) error {
	encoded, err := json.Marshal(options)
	if err != nil {
		return err
	}
	_, err = store.db.Exec(
		"INSERT OR IGNORE INTO polls (id, chat_jid, question, options, multiple, created_at) VALUES (?, ?, ?, ?, ?, ?)",
		id, chatJID, question, string(encoded), multiple, createdAt,
	)
	return err
}

func (store *MessageStore) pollOptions(id, chatJID string) ([]string, error) {
	var encoded string
	if err := store.db.QueryRow("SELECT options FROM polls WHERE id = ? AND chat_jid = ?", id, chatJID).Scan(&encoded); err != nil {
		return nil, err
	}
	var options []string
	err := json.Unmarshal([]byte(encoded), &options)
	return options, err
}

// StorePollVote records a voter's current choice; an empty choice means they withdrew their vote.
func (store *MessageStore) StorePollVote(pollID, chatJID, voter string, options []string, votedAt time.Time) error {
	encoded, err := json.Marshal(options)
	if err != nil {
		return err
	}
	_, err = store.db.Exec(
		"INSERT OR REPLACE INTO poll_votes (poll_id, chat_jid, voter, options, voted_at) VALUES (?, ?, ?, ?, ?)",
		pollID, chatJID, voter, string(encoded), votedAt,
	)
	return err
}

func pollCreation(m *waE2E.Message) *waE2E.PollCreationMessage {
	for _, creation := range []*waE2E.PollCreationMessage{
		m.GetPollCreationMessage(), m.GetPollCreationMessageV2(), m.GetPollCreationMessageV3(),
		m.GetPollCreationMessageV5(), m.GetPollCreationMessageV6(),
	} {
		if creation != nil {
			return creation
		}
	}
	return nil
}

// handlePollMessage stores poll creations (also as a readable "[poll]" message) and decodes votes.
// It returns true when it handled the message, so handleMessage stores nothing more.
func handlePollMessage(client *whatsmeow.Client, messageStore *MessageStore, msg *events.Message, logger waLog.Logger) bool {
	chatJID := msg.Info.Chat.String()

	if creation := pollCreation(msg.Message); creation != nil {
		options := make([]string, 0, len(creation.GetOptions()))
		for _, option := range creation.GetOptions() {
			options = append(options, option.GetOptionName())
		}
		if err := messageStore.StorePoll(msg.Info.ID, chatJID, creation.GetName(), options, creation.GetSelectableOptionsCount() != 1, msg.Info.Timestamp); err != nil {
			logger.Warnf("Failed to store poll: %v", err)
		}
		text := fmt.Sprintf("[poll] %s: %s", creation.GetName(), strings.Join(options, " / "))
		if err := messageStore.StoreMessage(msg.Info.ID, chatJID, msg.Info.Sender.User, text, msg.Info.Timestamp, msg.Info.IsFromMe, "", "", "", nil, nil, nil, 0); err != nil {
			logger.Warnf("Failed to store poll message: %v", err)
		}
		return true
	}

	update := msg.Message.GetPollUpdateMessage()
	if update == nil {
		return false
	}
	pollID := update.GetPollCreationMessageKey().GetID()
	vote, err := client.DecryptPollVote(context.Background(), msg)
	if err != nil {
		logger.Warnf("Failed to decrypt vote on poll %s: %v", pollID, err)
		return true
	}
	options, err := messageStore.pollOptions(pollID, chatJID)
	if err != nil {
		logger.Warnf("Vote on unknown poll %s: %v", pollID, err)
		return true
	}
	byHash := map[string]string{}
	for i, hash := range whatsmeow.HashPollOptions(options) {
		byHash[hex.EncodeToString(hash)] = options[i]
	}
	selected := []string{}
	for _, hash := range vote.GetSelectedOptions() {
		if name, ok := byHash[hex.EncodeToString(hash)]; ok {
			selected = append(selected, name)
		}
	}
	if err := messageStore.StorePollVote(pollID, chatJID, msg.Info.Sender.User, selected, msg.Info.Timestamp); err != nil {
		logger.Warnf("Failed to store vote: %v", err)
		return true
	}
	fmt.Printf("[%s] vote %s on poll %s: %s\n", msg.Info.Timestamp.Format("2006-01-02 15:04:05"), msg.Info.Sender.User, pollID, strings.Join(selected, ", "))
	return true
}
