import os
import threading
import time
from typing import List, Dict, Any, Optional
from mcp.server.fastmcp import FastMCP
from whatsapp import (
    search_contacts as whatsapp_search_contacts,
    list_messages as whatsapp_list_messages,
    list_chats as whatsapp_list_chats,
    get_chat as whatsapp_get_chat,
    get_direct_chat_by_contact as whatsapp_get_direct_chat_by_contact,
    get_contact_chats as whatsapp_get_contact_chats,
    get_last_interaction as whatsapp_get_last_interaction,
    get_message_context as whatsapp_get_message_context,
    send_message as whatsapp_send_message,
    send_file as whatsapp_send_file,
    send_poll as whatsapp_send_poll,
    get_poll_results as whatsapp_get_poll_results,
    send_audio_message as whatsapp_audio_voice_message,
    download_media as whatsapp_download_media,
    reply_to_message as whatsapp_reply_to_message,
    send_reaction as whatsapp_send_reaction,
    edit_message as whatsapp_edit_message,
    delete_message as whatsapp_delete_message,
    get_group_info as whatsapp_get_group_info,
    send_album as whatsapp_send_album,
    list_calls as whatsapp_list_calls,
    resync_calls as whatsapp_resync_calls,
)

# Initialize FastMCP server
mcp = FastMCP("whatsapp")

@mcp.tool()
def search_contacts(query: str) -> List[Dict[str, Any]]:
    """Search WhatsApp contacts (the address book and direct chats) by name or phone number.

    Each result has `jid`, the JID to send to, plus the phone number and LID when known.

    Args:
        query: Search term to match against contact names or phone numbers
    """
    contacts = whatsapp_search_contacts(query)
    return contacts

@mcp.tool()
def list_messages(
    after: Optional[str] = None,
    before: Optional[str] = None,
    sender_phone_number: Optional[str] = None,
    chat_jid: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_context: bool = True,
    context_before: int = 1,
    context_after: int = 1
) -> List[Dict[str, Any]]:
    """Get WhatsApp messages matching specified criteria with optional context.
    
    Args:
        after: Optional ISO-8601 formatted string to only return messages after this date
        before: Optional ISO-8601 formatted string to only return messages before this date
        sender_phone_number: Optional phone number to filter messages by sender
        chat_jid: Optional chat JID to filter messages by chat
        query: Optional search term to filter messages by content
        limit: Maximum number of messages to return (default 20)
        page: Page number for pagination (default 0)
        include_context: Whether to include messages before and after matches (default True)
        context_before: Number of messages to include before each match (default 1)
        context_after: Number of messages to include after each match (default 1)
    """
    messages = whatsapp_list_messages(
        after=after,
        before=before,
        sender_phone_number=sender_phone_number,
        chat_jid=chat_jid,
        query=query,
        limit=limit,
        page=page,
        include_context=include_context,
        context_before=context_before,
        context_after=context_after
    )
    return messages

@mcp.tool()
def list_chats(
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_last_message: bool = True,
    sort_by: str = "last_active"
) -> List[Dict[str, Any]]:
    """Get WhatsApp chats matching specified criteria. Chats show contact names; `send_jid` is the JID to send to.
    
    Args:
        query: Optional search term to filter chats by name or JID
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
        include_last_message: Whether to include the last message in each chat (default True)
        sort_by: Field to sort results by, either "last_active" or "name" (default "last_active")
    """
    chats = whatsapp_list_chats(
        query=query,
        limit=limit,
        page=page,
        include_last_message=include_last_message,
        sort_by=sort_by
    )
    return chats

@mcp.tool()
def get_chat(chat_jid: str, include_last_message: bool = True) -> Dict[str, Any]:
    """Get WhatsApp chat metadata by JID.
    
    Args:
        chat_jid: The JID of the chat to retrieve
        include_last_message: Whether to include the last message (default True)
    """
    chat = whatsapp_get_chat(chat_jid, include_last_message)
    return chat

@mcp.tool()
def get_direct_chat_by_contact(sender_phone_number: str) -> Dict[str, Any]:
    """Get WhatsApp chat metadata by sender phone number.
    
    Args:
        sender_phone_number: The phone number to search for
    """
    chat = whatsapp_get_direct_chat_by_contact(sender_phone_number)
    return chat

@mcp.tool()
def get_contact_chats(jid: str, limit: int = 20, page: int = 0) -> List[Dict[str, Any]]:
    """Get all WhatsApp chats involving the contact.
    
    Args:
        jid: The contact's JID to search for
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
    """
    chats = whatsapp_get_contact_chats(jid, limit, page)
    return chats

@mcp.tool()
def get_last_interaction(jid: str) -> str:
    """Get most recent WhatsApp message involving the contact.
    
    Args:
        jid: The JID of the contact to search for
    """
    message = whatsapp_get_last_interaction(jid)
    return message

@mcp.tool()
def get_message_context(
    message_id: str,
    before: int = 5,
    after: int = 5
) -> Dict[str, Any]:
    """Get context around a specific WhatsApp message.
    
    Args:
        message_id: The ID of the message to get context for
        before: Number of messages to include before the target message (default 5)
        after: Number of messages to include after the target message (default 5)
    """
    context = whatsapp_get_message_context(message_id, before, after)
    return context

@mcp.tool()
def send_message(
    recipient: str,
    message: str
) -> Dict[str, Any]:
    """Send a WhatsApp message to a person or group. For group chats use the JID.

    Args:
        recipient: The recipient - either a phone number with country code but no + or other symbols,
                 or a JID (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        message: The message text to send
    
    Returns:
        A dictionary containing success status and a status message
    """
    # Validate input
    if not recipient:
        return {
            "success": False,
            "message": "Recipient must be provided"
        }
    
    # Call the whatsapp_send_message function with the unified recipient parameter
    success, status_message = whatsapp_send_message(recipient, message)
    return {
        "success": success,
        "message": status_message
    }

@mcp.tool()
def send_poll(
    recipient: str,
    question: str,
    options: List[str],
    multiple: bool = False
) -> Dict[str, Any]:
    """Send a WhatsApp poll to a person or group. For group chats use the JID.

    Args:
        recipient: A phone number with country code but no + or other symbols, or a JID
                 (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        question: The poll question
        options: 2 to 12 distinct answer options
        multiple: True lets people pick more than one option (default: one)

    Returns:
        A dictionary containing success status and a status message
    """
    success, status_message = whatsapp_send_poll(recipient, question, options, multiple)
    return {"success": success, "message": status_message}

@mcp.tool()
def get_poll_results(chat_jid: str, poll_id: Optional[str] = None) -> Dict[str, Any]:
    """Get a WhatsApp poll's results: votes and voters per option.

    Args:
        chat_jid: The chat the poll is in (a group JID like "123456789@g.us")
        poll_id: Optional message ID of the poll; default the latest poll in the chat

    Returns:
        The question, each option with its vote count and voters, and the number of voters.
        Only votes cast after the bridge learned of the poll are known.
    """
    return whatsapp_get_poll_results(chat_jid, poll_id)

@mcp.tool()
def list_calls(
    after: Optional[str] = None,
    before: Optional[str] = None,
    chat_jid: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """List WhatsApp voice and video calls from the phone's call log, newest first.

    Args:
        after: Optional ISO-8601 time; only calls that started at or after it
        before: Optional ISO-8601 time; only calls that started before it
        chat_jid: Optional chat JID (a person or a group) to filter by
        query: Optional text matched against the chat name, caller and participants
        limit: Maximum number of calls to return (default 50)

    Returns:
        Per call: chat and caller names, group or not, direction, voice or video, result
        (CONNECTED, MISSED, REJECTED, ...), start time, duration and participants.
        Only call metadata exists; calls are not recorded.
    """
    return whatsapp_list_calls(after, before, chat_jid, query, limit)

@mcp.tool()
def resync_calls() -> Dict[str, Any]:
    """Fetch the phone's whole call log again, for calls from before the bridge stored them.

    Returns:
        Success, the number of stored calls afterwards, and any errors per app state patch.
    """
    return whatsapp_resync_calls()

@mcp.tool()
def send_file(recipient: str, media_path: str, caption: Optional[str] = None) -> Dict[str, Any]:
    """Send a file such as a picture, raw audio, video or document via WhatsApp to the specified recipient. For group messages use the JID.
    
    Args:
        recipient: The recipient - either a phone number with country code but no + or other symbols,
                 or a JID (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        media_path: The absolute path to the media file to send (image, video, document)
        caption: Optional text shown with the image, video or document
    
    Returns:
        A dictionary containing success status and a status message
    """
    
    # Call the whatsapp_send_file function
    success, status_message = whatsapp_send_file(recipient, media_path, caption)
    return {
        "success": success,
        "message": status_message
    }

@mcp.tool()
def send_album(recipient: str, media_paths: List[str], caption: Optional[str] = None) -> Dict[str, Any]:
    """Send several images or videos as one WhatsApp album, in the given order. For group messages use the JID.

    Args:
        recipient: A phone number with country code but no + or other symbols, or a JID
                 (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        media_paths: Absolute paths of 2 or more images or videos, in the order to show them
        caption: Optional text, shown on the first item
    """
    success, status_message = whatsapp_send_album(recipient, media_paths, caption)
    return {"success": success, "message": status_message}

@mcp.tool()
def send_audio_message(recipient: str, media_path: str) -> Dict[str, Any]:
    """Send any audio file as a WhatsApp audio message to the specified recipient. For group messages use the JID. If it errors due to ffmpeg not being installed, use send_file instead.
    
    Args:
        recipient: The recipient - either a phone number with country code but no + or other symbols,
                 or a JID (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        media_path: The absolute path to the audio file to send (will be converted to Opus .ogg if it's not a .ogg file)
    
    Returns:
        A dictionary containing success status and a status message
    """
    success, status_message = whatsapp_audio_voice_message(recipient, media_path)
    return {
        "success": success,
        "message": status_message
    }

@mcp.tool()
def reply_to_message(message_id: str, message: str, chat_jid: Optional[str] = None) -> Dict[str, Any]:
    """Reply to a WhatsApp message: send text in its chat that quotes it.

    Args:
        message_id: The id of the message to quote (list_messages shows it)
        message: The reply text
        chat_jid: The chat of the message; found from the message id when left out
    """
    success, status_message = whatsapp_reply_to_message(message_id, message, chat_jid)
    return {"success": success, "message": status_message}

@mcp.tool()
def send_reaction(message_id: str, emoji: str, chat_jid: Optional[str] = None) -> Dict[str, Any]:
    """React to a WhatsApp message with an emoji.

    Args:
        message_id: The id of the message to react to (list_messages shows it)
        emoji: One emoji, e.g. "👍"; an empty string removes our reaction
        chat_jid: The chat of the message; found from the message id when left out
    """
    success, status_message = whatsapp_send_reaction(message_id, emoji, chat_jid)
    return {"success": success, "message": status_message}

@mcp.tool()
def edit_message(message_id: str, new_text: str, chat_jid: Optional[str] = None) -> Dict[str, Any]:
    """Edit the text of a WhatsApp message we sent, within 20 minutes of sending it.

    Args:
        message_id: The id of our message (send tools and list_messages show it)
        new_text: The new text
        chat_jid: The chat of the message; found from the message id when left out
    """
    success, status_message = whatsapp_edit_message(message_id, new_text, chat_jid)
    return {"success": success, "message": status_message}

@mcp.tool()
def delete_message(message_id: str, chat_jid: Optional[str] = None) -> Dict[str, Any]:
    """Delete a WhatsApp message we sent, for everyone. This cannot be undone.

    Args:
        message_id: The id of our message (send tools and list_messages show it)
        chat_jid: The chat of the message; found from the message id when left out
    """
    success, status_message = whatsapp_delete_message(message_id, chat_jid)
    return {"success": success, "message": status_message}

@mcp.tool()
def get_group_info(group_jid: str) -> Dict[str, Any]:
    """Get a WhatsApp group's name, description and participants (with names, admins marked).

    Args:
        group_jid: The group's JID, ending in @g.us
    """
    return whatsapp_get_group_info(group_jid)

@mcp.tool()
def download_media(message_id: str, chat_jid: str) -> Dict[str, Any]:
    """Download media from a WhatsApp message and get the local file path.
    
    Args:
        message_id: The ID of the message containing the media
        chat_jid: The JID of the chat containing the message
    
    Returns:
        A dictionary containing success status, a status message, and the file path if successful
    """
    file_path = whatsapp_download_media(message_id, chat_jid)
    
    if file_path:
        return {
            "success": True,
            "message": "Media downloaded successfully",
            "file_path": file_path
        }
    else:
        return {
            "success": False,
            "message": "Failed to download media"
        }

def exit_with_client() -> None:
    """Exit when the process that started us goes away.

    Over ssh the stdio server outlived its session: when the client closed, the server was
    re-parented instead of ending, and they piled up (147 of them, 7.5 GB on luka-mini).
    Watching the parent ends it within a few seconds of the client.
    """
    parent = os.getppid()

    def watch() -> None:
        while True:
            time.sleep(5)
            if os.getppid() != parent:
                os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


if __name__ == "__main__":
    exit_with_client()
    mcp.run(transport='stdio')