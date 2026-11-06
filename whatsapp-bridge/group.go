package main

import (
	"context"
	"fmt"
	"net/http"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
)

// GroupParticipantInfo is one member, named from the address book, with the JID to send to.
type GroupParticipantInfo struct {
	JID          string `json:"jid"`
	PhoneNumber  string `json:"phone_number,omitempty"`
	LID          string `json:"lid,omitempty"`
	Name         string `json:"name,omitempty"`
	IsAdmin      bool   `json:"is_admin"`
	IsSuperAdmin bool   `json:"is_super_admin"`
}

// GroupInfoResponse is what get_group_info shows: who is in a group and who runs it.
type GroupInfoResponse struct {
	Success          bool                   `json:"success"`
	Message          string                 `json:"message,omitempty"`
	JID              string                 `json:"jid,omitempty"`
	Name             string                 `json:"name,omitempty"`
	Description      string                 `json:"description,omitempty"`
	Owner            string                 `json:"owner,omitempty"`
	Created          time.Time              `json:"created,omitzero"`
	OnlyAdminsSend   bool                   `json:"only_admins_send"`
	OnlyAdminsEdit   bool                   `json:"only_admins_edit_info"`
	ParticipantCount int                    `json:"participant_count"`
	Participants     []GroupParticipantInfo `json:"participants,omitempty"`
}

// contactName is the address book's name for a person: full name, first name, then the name they set.
func contactName(client *whatsmeow.Client, jids ...types.JID) string {
	for _, jid := range jids {
		if jid.IsEmpty() {
			continue
		}
		contact, err := client.Store.Contacts.GetContact(context.Background(), jid.ToNonAD())
		if err != nil || !contact.Found {
			continue
		}
		for _, name := range []string{contact.FullName, contact.FirstName, contact.PushName, contact.BusinessName} {
			if name != "" {
				return name
			}
		}
	}
	return ""
}

// groupInfo asks WhatsApp for the group's current metadata rather than trusting messages.db.
func groupInfo(client *whatsmeow.Client, jidText string) GroupInfoResponse {
	jid, err := types.ParseJID(jidText)
	if err != nil || jid.Server != types.GroupServer {
		return GroupInfoResponse{Message: fmt.Sprintf("Not a group JID: %q", jidText)}
	}
	info, err := client.GetGroupInfo(context.Background(), jid)
	if err != nil {
		return GroupInfoResponse{Message: fmt.Sprintf("Error getting group info: %v", err)}
	}
	resp := GroupInfoResponse{
		Success:          true,
		JID:              info.JID.String(),
		Name:             info.Name,
		Description:      info.Topic,
		Created:          info.GroupCreated,
		OnlyAdminsSend:   info.IsAnnounce,
		OnlyAdminsEdit:   info.IsLocked,
		ParticipantCount: len(info.Participants),
	}
	owner := info.OwnerPN
	if owner.IsEmpty() {
		owner = info.OwnerJID
	}
	if !owner.IsEmpty() {
		resp.Owner = owner.String()
	}
	for _, p := range info.Participants {
		pn, lid := p.PhoneNumber, p.LID
		if pn.IsEmpty() && p.JID.Server == types.DefaultUserServer {
			pn = p.JID
		}
		if lid.IsEmpty() && p.JID.Server == types.HiddenUserServer {
			lid = p.JID
		}
		if pn.IsEmpty() && !lid.IsEmpty() {
			pn, _ = client.Store.LIDs.GetPNForLID(context.Background(), lid)
		}
		member := GroupParticipantInfo{
			JID:          p.JID.String(),
			Name:         contactName(client, pn, lid),
			IsAdmin:      p.IsAdmin || p.IsSuperAdmin,
			IsSuperAdmin: p.IsSuperAdmin,
		}
		// Send to the phone number JID when we know it, like search_contacts does.
		if !pn.IsEmpty() {
			member.PhoneNumber = pn.User
			member.JID = pn.ToNonAD().String()
		}
		if !lid.IsEmpty() {
			member.LID = lid.User
		}
		if member.Name == "" && p.DisplayName != "" {
			member.Name = p.DisplayName
		}
		resp.Participants = append(resp.Participants, member)
	}
	return resp
}

func registerGroupRoutes(client *whatsmeow.Client) {
	http.HandleFunc("/api/group", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if !client.IsConnected() {
			writeJSON(w, false, GroupInfoResponse{Message: "Not connected to WhatsApp"})
			return
		}
		resp := groupInfo(client, r.URL.Query().Get("jid"))
		writeJSON(w, resp.Success, resp)
	})
}
