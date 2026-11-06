package main

import (
	"context"
	"fmt"
	"mime"
	"os"
	"path/filepath"
	"strings"

	"go.mau.fi/whatsmeow"
	waProto "go.mau.fi/whatsmeow/binary/proto"
	"go.mau.fi/whatsmeow/types"
	"google.golang.org/protobuf/proto"
)

// sentMedia is what we keep of a file we uploaded, so our own media sends land in messages.db
// like received ones: list_messages shows them and download_media can fetch them again.
type sentMedia struct {
	Type     string // "image", "video", "audio" or "document", as extractMediaInfo names them
	Filename string
	Upload   whatsmeow.UploadResponse
}

// mediaKind picks the WhatsApp media type and mime type from the file extension;
// anything that is not a known image, voice note or video goes as a document.
func mediaKind(path string) (whatsmeow.MediaType, string) {
	ext := strings.ToLower(strings.TrimPrefix(filepath.Ext(path), "."))
	switch ext {
	case "jpg", "jpeg":
		return whatsmeow.MediaImage, "image/jpeg"
	case "png":
		return whatsmeow.MediaImage, "image/png"
	case "gif":
		return whatsmeow.MediaImage, "image/gif"
	case "webp":
		return whatsmeow.MediaImage, "image/webp"
	case "ogg":
		return whatsmeow.MediaAudio, "audio/ogg; codecs=opus"
	case "mp4":
		return whatsmeow.MediaVideo, "video/mp4"
	case "avi":
		return whatsmeow.MediaVideo, "video/avi"
	case "mov":
		return whatsmeow.MediaVideo, "video/quicktime"
	}
	if byExt := mime.TypeByExtension("." + ext); byExt != "" {
		return whatsmeow.MediaDocument, byExt
	}
	return whatsmeow.MediaDocument, "application/octet-stream"
}

// buildMediaMessage uploads the file at path and wraps it in the message its type calls for.
// The caption goes on images, videos and documents; voice notes have none.
func buildMediaMessage(client *whatsmeow.Client, path, caption string) (*waProto.Message, sentMedia, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, sentMedia{}, fmt.Errorf("Error reading media file: %v", err)
	}
	mediaType, mimeType := mediaKind(path)
	resp, err := client.Upload(context.Background(), data, mediaType)
	if err != nil {
		return nil, sentMedia{}, fmt.Errorf("Error uploading media: %v", err)
	}
	filename := filepath.Base(path)
	msg := &waProto.Message{}
	sent := sentMedia{Filename: filename, Upload: resp}

	switch mediaType {
	case whatsmeow.MediaImage:
		sent.Type = "image"
		msg.ImageMessage = &waProto.ImageMessage{
			Caption:       proto.String(caption),
			Mimetype:      proto.String(mimeType),
			URL:           &resp.URL,
			DirectPath:    &resp.DirectPath,
			MediaKey:      resp.MediaKey,
			FileEncSHA256: resp.FileEncSHA256,
			FileSHA256:    resp.FileSHA256,
			FileLength:    &resp.FileLength,
		}
	case whatsmeow.MediaAudio:
		sent.Type = "audio"
		seconds, waveform, err := analyzeOggOpus(data)
		if err != nil {
			return nil, sentMedia{}, fmt.Errorf("Failed to analyze Ogg Opus file: %v", err)
		}
		msg.AudioMessage = &waProto.AudioMessage{
			Mimetype:      proto.String(mimeType),
			URL:           &resp.URL,
			DirectPath:    &resp.DirectPath,
			MediaKey:      resp.MediaKey,
			FileEncSHA256: resp.FileEncSHA256,
			FileSHA256:    resp.FileSHA256,
			FileLength:    &resp.FileLength,
			Seconds:       proto.Uint32(seconds),
			PTT:           proto.Bool(true),
			Waveform:      waveform,
		}
	case whatsmeow.MediaVideo:
		sent.Type = "video"
		msg.VideoMessage = &waProto.VideoMessage{
			Caption:       proto.String(caption),
			Mimetype:      proto.String(mimeType),
			URL:           &resp.URL,
			DirectPath:    &resp.DirectPath,
			MediaKey:      resp.MediaKey,
			FileEncSHA256: resp.FileEncSHA256,
			FileSHA256:    resp.FileSHA256,
			FileLength:    &resp.FileLength,
		}
	default:
		sent.Type = "document"
		msg.DocumentMessage = &waProto.DocumentMessage{
			Title:         proto.String(filename),
			FileName:      proto.String(filename),
			Caption:       proto.String(caption),
			Mimetype:      proto.String(mimeType),
			URL:           &resp.URL,
			DirectPath:    &resp.DirectPath,
			MediaKey:      resp.MediaKey,
			FileEncSHA256: resp.FileEncSHA256,
			FileSHA256:    resp.FileSHA256,
			FileLength:    &resp.FileLength,
		}
	}
	return msg, sent, nil
}

// storeOwnSend keeps a message we sent in messages.db. whatsmeow does not echo our own sends as
// events, so without this the chat history would miss everything sent through the bridge.
// media is nil for a text message; content is the text or the caption.
func storeOwnSend(client *whatsmeow.Client, store *MessageStore, chat types.JID, resp whatsmeow.SendResponse, content string, media *sentMedia) {
	if client.Store.ID == nil {
		return
	}
	// messages.chat_jid references chats: a first message to someone needs the chat row first.
	if _, err := store.db.Exec(
		`INSERT INTO chats (jid, name, last_message_time) VALUES (?, ?, ?)
		ON CONFLICT(jid) DO UPDATE SET last_message_time = excluded.last_message_time`,
		chat.String(), chat.User, resp.Timestamp,
	); err != nil {
		fmt.Printf("Failed to store chat of sent message: %v\n", err)
	}
	var err error
	if media == nil {
		err = store.StoreMessage(resp.ID, chat.String(), client.Store.ID.User, content, resp.Timestamp, true, "", "", "", nil, nil, nil, 0)
	} else {
		up := media.Upload
		err = store.StoreMessage(resp.ID, chat.String(), client.Store.ID.User, content, resp.Timestamp, true,
			media.Type, media.Filename, up.URL, up.MediaKey, up.FileSHA256, up.FileEncSHA256, up.FileLength)
	}
	if err != nil {
		fmt.Printf("Failed to store sent message: %v\n", err)
	}
}
