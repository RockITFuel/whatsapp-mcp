import sqlite3
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, List, Tuple
import os.path
import requests
import json
import audio
from contacts import STORE_DIR, directory

MESSAGES_DB_PATH = os.path.join(STORE_DIR, 'messages.db')
WHATSAPP_API_BASE_URL = "http://localhost:8080/api"

@dataclass
class Message:
    timestamp: datetime
    sender: str
    content: str
    is_from_me: bool
    chat_jid: str
    id: str
    chat_name: Optional[str] = None
    media_type: Optional[str] = None
    filename: Optional[str] = None

@dataclass
class Chat:
    jid: str
    name: Optional[str]
    last_message_time: Optional[datetime]
    last_message: Optional[str] = None
    last_sender: Optional[str] = None
    last_is_from_me: Optional[bool] = None
    last_sender_name: Optional[str] = None
    # The JID to send to: a person's phone number JID when known, the group JID for groups.
    send_jid: Optional[str] = None

    @property
    def is_group(self) -> bool:
        """Determine if chat is a group based on JID pattern."""
        return self.jid.endswith("@g.us")

@dataclass
class Contact:
    phone_number: Optional[str]
    name: Optional[str]
    jid: str  # the JID to send to
    lid: Optional[str] = None

@dataclass
class MessageContext:
    message: Message
    before: List[Message]
    after: List[Message]

def chat_display_name(jid: str, stored_name: Optional[str]) -> Optional[str]:
    """The stored chat name, unless it is only a number: then the contact's name."""
    if stored_name and not stored_name.isdigit() and stored_name != jid:
        return stored_name
    return directory().name(jid) or stored_name


def chat_from_row(row) -> Chat:
    """A Chat from (jid, name, last_message_time, last_message, last_sender, last_is_from_me), with names resolved."""
    person = directory().resolve(row[0])
    return Chat(
        jid=row[0],
        name=chat_display_name(row[0], row[1]),
        last_message_time=datetime.fromisoformat(row[2]) if row[2] else None,
        last_message=row[3],
        last_sender=row[4],
        last_is_from_me=row[5],
        last_sender_name=("Me" if row[5] else get_sender_name(row[4])) if row[4] else None,
        send_jid=person.jid if person else row[0],
    )


def get_sender_name(sender_jid: str) -> str:
    # The address book knows LIDs and phone numbers; the chats table only knows chats we have.
    name = directory().name(sender_jid)
    if name:
        return name
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        cursor = conn.cursor()
        
        # First try matching by exact JID
        cursor.execute("""
            SELECT name
            FROM chats
            WHERE jid = ?
            LIMIT 1
        """, (sender_jid,))
        
        result = cursor.fetchone()
        
        if result and result[0]:
            return result[0]
        else:
            return sender_jid
        
    except sqlite3.Error as e:
        print(f"Database error while getting sender name: {e}")
        return sender_jid
    finally:
        if 'conn' in locals():
            conn.close()

def format_message(message: Message, show_chat_info: bool = True) -> None:
    """Print a single message with consistent formatting."""
    output = ""
    
    if show_chat_info and message.chat_name:
        output += f"[{message.timestamp:%Y-%m-%d %H:%M:%S}] Chat: {message.chat_name} "
    else:
        output += f"[{message.timestamp:%Y-%m-%d %H:%M:%S}] "
        
    # Every line carries its message id: replying, reacting, editing and deleting all take it.
    content_prefix = f"[id: {message.id}] "
    if hasattr(message, 'media_type') and message.media_type:
        name = f" {message.filename}" if message.filename else ""
        content_prefix = f"[{message.media_type}{name} - Message ID: {message.id} - Chat JID: {message.chat_jid}] "
    
    try:
        sender_name = get_sender_name(message.sender) if not message.is_from_me else "Me"
        output += f"From: {sender_name}: {content_prefix}{message.content}\n"
    except Exception as e:
        print(f"Error formatting message: {e}")
    return output

def format_messages_list(messages: List[Message], show_chat_info: bool = True) -> None:
    output = ""
    if not messages:
        output += "No messages to display."
        return output
    
    for message in messages:
        output += format_message(message, show_chat_info)
    return output

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
) -> List[Message]:
    """Get messages matching the specified criteria with optional context."""
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        cursor = conn.cursor()
        
        # Build base query
        query_parts = ["SELECT messages.timestamp, messages.sender, chats.name, messages.content, messages.is_from_me, chats.jid, messages.id, messages.media_type, messages.filename FROM messages"]
        query_parts.append("JOIN chats ON messages.chat_jid = chats.jid")
        where_clauses = []
        params = []
        
        # Add filters
        if after:
            try:
                after = datetime.fromisoformat(after)
            except ValueError:
                raise ValueError(f"Invalid date format for 'after': {after}. Please use ISO-8601 format.")
            
            where_clauses.append("messages.timestamp > ?")
            params.append(after)

        if before:
            try:
                before = datetime.fromisoformat(before)
            except ValueError:
                raise ValueError(f"Invalid date format for 'before': {before}. Please use ISO-8601 format.")
            
            where_clauses.append("messages.timestamp < ?")
            params.append(before)

        if sender_phone_number:
            forms = directory().sender_forms(sender_phone_number)
            where_clauses.append(f"messages.sender IN ({','.join('?' * len(forms))})")
            params.extend(forms)
            
        if chat_jid:
            where_clauses.append("messages.chat_jid = ?")
            params.append(chat_jid)
            
        if query:
            where_clauses.append("LOWER(messages.content) LIKE LOWER(?)")
            params.append(f"%{query}%")
            
        if where_clauses:
            query_parts.append("WHERE " + " AND ".join(where_clauses))
            
        # Add pagination
        offset = page * limit
        query_parts.append("ORDER BY messages.timestamp DESC")
        query_parts.append("LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        
        cursor.execute(" ".join(query_parts), tuple(params))
        messages = cursor.fetchall()
        
        result = []
        for msg in messages:
            message = Message(
                timestamp=datetime.fromisoformat(msg[0]),
                sender=msg[1],
                chat_name=chat_display_name(msg[5], msg[2]),
                content=msg[3],
                is_from_me=msg[4],
                chat_jid=msg[5],
                id=msg[6],
                media_type=msg[7],
                filename=msg[8]
            )
            result.append(message)
            
        if include_context and result:
            # Add context for each message
            messages_with_context = []
            for msg in result:
                context = get_message_context(msg.id, context_before, context_after)
                messages_with_context.extend(context.before)
                messages_with_context.append(context.message)
                messages_with_context.extend(context.after)
            
            return format_messages_list(messages_with_context, show_chat_info=True)
            
        # Format and display messages without context
        return format_messages_list(result, show_chat_info=True)    
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def get_message_context(
    message_id: str,
    before: int = 5,
    after: int = 5
) -> MessageContext:
    """Get context around a specific message."""
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        cursor = conn.cursor()
        
        # Get the target message first
        cursor.execute("""
            SELECT messages.timestamp, messages.sender, chats.name, messages.content, messages.is_from_me, chats.jid, messages.id, messages.chat_jid, messages.media_type, messages.filename
            FROM messages
            JOIN chats ON messages.chat_jid = chats.jid
            WHERE messages.id = ?
        """, (message_id,))
        msg_data = cursor.fetchone()
        
        if not msg_data:
            raise ValueError(f"Message with ID {message_id} not found")
            
        target_message = Message(
            timestamp=datetime.fromisoformat(msg_data[0]),
            sender=msg_data[1],
            chat_name=chat_display_name(msg_data[5], msg_data[2]),
            content=msg_data[3],
            is_from_me=msg_data[4],
            chat_jid=msg_data[5],
            id=msg_data[6],
            media_type=msg_data[8],
            filename=msg_data[9]
        )
        
        # Get messages before
        cursor.execute("""
            SELECT messages.timestamp, messages.sender, chats.name, messages.content, messages.is_from_me, chats.jid, messages.id, messages.media_type, messages.filename
            FROM messages
            JOIN chats ON messages.chat_jid = chats.jid
            WHERE messages.chat_jid = ? AND messages.timestamp < ?
            ORDER BY messages.timestamp DESC
            LIMIT ?
        """, (msg_data[7], msg_data[0], before))
        
        before_messages = []
        for msg in cursor.fetchall():
            before_messages.append(Message(
                timestamp=datetime.fromisoformat(msg[0]),
                sender=msg[1],
                chat_name=chat_display_name(msg[5], msg[2]),
                content=msg[3],
                is_from_me=msg[4],
                chat_jid=msg[5],
                id=msg[6],
                media_type=msg[7],
                filename=msg[8]
            ))
        
        # Get messages after
        cursor.execute("""
            SELECT messages.timestamp, messages.sender, chats.name, messages.content, messages.is_from_me, chats.jid, messages.id, messages.media_type, messages.filename
            FROM messages
            JOIN chats ON messages.chat_jid = chats.jid
            WHERE messages.chat_jid = ? AND messages.timestamp > ?
            ORDER BY messages.timestamp ASC
            LIMIT ?
        """, (msg_data[7], msg_data[0], after))
        
        after_messages = []
        for msg in cursor.fetchall():
            after_messages.append(Message(
                timestamp=datetime.fromisoformat(msg[0]),
                sender=msg[1],
                chat_name=chat_display_name(msg[5], msg[2]),
                content=msg[3],
                is_from_me=msg[4],
                chat_jid=msg[5],
                id=msg[6],
                media_type=msg[7],
                filename=msg[8]
            ))
        
        return MessageContext(
            message=target_message,
            before=before_messages,
            after=after_messages
        )
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        raise
    finally:
        if 'conn' in locals():
            conn.close()


def list_chats(
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_last_message: bool = True,
    sort_by: str = "last_active"
) -> List[Chat]:
    """Get chats matching the specified criteria."""
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        cursor = conn.cursor()
        
        # Build base query
        query_parts = ["""
            SELECT 
                chats.jid,
                chats.name,
                chats.last_message_time,
                {last_columns}
            FROM chats
        """.format(last_columns=(
            "messages.content as last_message, messages.sender as last_sender, "
            "messages.is_from_me as last_is_from_me"
            if include_last_message
            else "NULL as last_message, NULL as last_sender, NULL as last_is_from_me"
        ))]
        
        if include_last_message:
            query_parts.append("""
                LEFT JOIN messages ON chats.jid = messages.chat_jid 
                AND chats.last_message_time = messages.timestamp
            """)
            
        where_clauses = []
        params = []
        
        if query:
            where_clauses.append("(LOWER(chats.name) LIKE LOWER(?) OR chats.jid LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
            
        if where_clauses:
            query_parts.append("WHERE " + " AND ".join(where_clauses))
            
        # Add sorting
        order_by = "chats.last_message_time DESC" if sort_by == "last_active" else "chats.name"
        query_parts.append(f"ORDER BY {order_by}")
        
        # Add pagination
        offset = (page ) * limit
        query_parts.append("LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        
        cursor.execute(" ".join(query_parts), tuple(params))
        chats = cursor.fetchall()
        
        result = []
        for chat_data in chats:
            chat = chat_from_row(chat_data)
            result.append(chat)
            
        return result
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def search_contacts(query: str) -> List[Contact]:
    """Search the address book and the direct chats by name or phone number.

    The address book (whatsmeow_contacts) holds people we never chatted with, and the chats table
    holds people who are not in the address book; both count. Each result has the JID to send to.
    """
    found = {}
    for person in directory().search(query):
        found[person.jid] = Contact(phone_number=person.phone_number, name=person.name, jid=person.jid, lid=person.lid)
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        cursor = conn.cursor()
        search_pattern = '%' + query + '%'
        cursor.execute("""
            SELECT DISTINCT jid, name
            FROM chats
            WHERE
                (LOWER(name) LIKE LOWER(?) OR LOWER(jid) LIKE LOWER(?))
                AND jid NOT LIKE '%@g.us'
            ORDER BY name, jid
            LIMIT 50
        """, (search_pattern, search_pattern))
        for jid, name in cursor.fetchall():
            person = directory().resolve(jid)
            if person is None:
                continue
            if person.jid not in found:
                found[person.jid] = Contact(
                    phone_number=person.phone_number,
                    name=chat_display_name(jid, name),
                    jid=person.jid,
                    lid=person.lid,
                )
    except sqlite3.Error as e:
        print(f"Database error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()
    return list(found.values())[:50]


def get_contact_chats(jid: str, limit: int = 20, page: int = 0) -> List[Chat]:
    """Get all chats involving the contact.
    
    Args:
        jid: The contact's JID to search for
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
    """
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        cursor = conn.cursor()
        
        forms = directory().sender_forms(jid)
        placeholders = ','.join('?' * len(forms))
        cursor.execute("""
            SELECT DISTINCT
                c.jid,
                c.name,
                c.last_message_time,
                m.content as last_message,
                m.sender as last_sender,
                m.is_from_me as last_is_from_me
            FROM chats c
            JOIN messages m ON c.jid = m.chat_jid
            WHERE m.sender IN ({forms}) OR c.jid IN ({forms})
            ORDER BY c.last_message_time DESC
            LIMIT ? OFFSET ?
        """.format(forms=placeholders), (*forms, *forms, limit, page * limit))
        
        chats = cursor.fetchall()
        
        result = []
        for chat_data in chats:
            chat = chat_from_row(chat_data)
            result.append(chat)
            
        return result
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def get_last_interaction(jid: str) -> str:
    """Get most recent message involving the contact."""
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        cursor = conn.cursor()
        
        forms = directory().sender_forms(jid)
        placeholders = ','.join('?' * len(forms))
        cursor.execute("""
            SELECT 
                m.timestamp,
                m.sender,
                c.name,
                m.content,
                m.is_from_me,
                c.jid,
                m.id,
                m.media_type,
                m.filename
            FROM messages m
            JOIN chats c ON m.chat_jid = c.jid
            WHERE m.sender IN ({forms}) OR c.jid IN ({forms})
            ORDER BY m.timestamp DESC
            LIMIT 1
        """.format(forms=placeholders), (*forms, *forms))
        
        msg_data = cursor.fetchone()
        
        if not msg_data:
            return None
            
        message = Message(
            timestamp=datetime.fromisoformat(msg_data[0]),
            sender=msg_data[1],
            chat_name=chat_display_name(msg_data[5], msg_data[2]),
            content=msg_data[3],
            is_from_me=msg_data[4],
            chat_jid=msg_data[5],
            id=msg_data[6],
            media_type=msg_data[7],
            filename=msg_data[8]
        )
        
        return format_message(message)
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()


def get_chat(chat_jid: str, include_last_message: bool = True) -> Optional[Chat]:
    """Get chat metadata by JID."""
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        cursor = conn.cursor()
        
        query = """
            SELECT 
                c.jid,
                c.name,
                c.last_message_time,
                {last_columns}
            FROM chats c
        """.format(last_columns=(
            "m.content as last_message, m.sender as last_sender, m.is_from_me as last_is_from_me"
            if include_last_message
            else "NULL as last_message, NULL as last_sender, NULL as last_is_from_me"
        ))
        
        if include_last_message:
            query += """
                LEFT JOIN messages m ON c.jid = m.chat_jid 
                AND c.last_message_time = m.timestamp
            """
            
        query += " WHERE c.jid = ?"
        
        cursor.execute(query, (chat_jid,))
        chat_data = cursor.fetchone()
        
        if not chat_data:
            return None
            
        return chat_from_row(chat_data)
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()


def get_direct_chat_by_contact(sender_phone_number: str) -> Optional[Chat]:
    """Get chat metadata by sender phone number."""
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        cursor = conn.cursor()
        
        forms = directory().sender_forms(sender_phone_number)
        placeholders = ','.join('?' * len(forms))
        cursor.execute("""
            SELECT 
                c.jid,
                c.name,
                c.last_message_time,
                m.content as last_message,
                m.sender as last_sender,
                m.is_from_me as last_is_from_me
            FROM chats c
            LEFT JOIN messages m ON c.jid = m.chat_jid 
                AND c.last_message_time = m.timestamp
            WHERE (c.jid IN ({forms}) OR c.jid LIKE ?) AND c.jid NOT LIKE '%@g.us'
            ORDER BY c.jid IN ({forms}) DESC, c.last_message_time DESC
            LIMIT 1
        """.format(forms=placeholders), (*forms, f"%{sender_phone_number}%", *forms))
        
        chat_data = cursor.fetchone()
        
        if not chat_data:
            return None
            
        return chat_from_row(chat_data)
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()

def send_message(recipient: str, message: str) -> Tuple[bool, str]:
    try:
        # Validate input
        if not recipient:
            return False, "Recipient must be provided"
        
        url = f"{WHATSAPP_API_BASE_URL}/send"
        payload = {
            "recipient": recipient,
            "message": message,
        }
        
        response = requests.post(url, json=payload)
        
        # Check if the request was successful
        if response.status_code == 200:
            result = response.json()
            return result.get("success", False), result.get("message", "Unknown response")
        else:
            return False, f"Error: HTTP {response.status_code} - {response.text}"
            
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except json.JSONDecodeError:
        return False, f"Error parsing response: {response.text}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"

def send_poll(recipient: str, question: str, options: List[str], multiple: bool = False) -> Tuple[bool, str]:
    try:
        if not recipient:
            return False, "Recipient must be provided"
        payload = {"recipient": recipient, "question": question, "options": options, "multiple": multiple}
        response = requests.post(f"{WHATSAPP_API_BASE_URL}/poll", json=payload)
        try:
            result = response.json()
        except json.JSONDecodeError:
            return False, f"Error: HTTP {response.status_code} - {response.text}"
        return result.get("success", False), result.get("message", "Unknown response")
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"

def send_file(recipient: str, media_path: str, caption: Optional[str] = None) -> Tuple[bool, str]:
    try:
        # Validate input
        if not recipient:
            return False, "Recipient must be provided"
        
        if not media_path:
            return False, "Media path must be provided"
        
        if not os.path.isfile(media_path):
            return False, f"Media file not found: {media_path}"
        
        url = f"{WHATSAPP_API_BASE_URL}/send"
        # The bridge uses "message" as the caption of a media message (voice notes have none).
        payload = {
            "recipient": recipient,
            "media_path": media_path,
            "message": caption or "",
        }
        
        response = requests.post(url, json=payload)
        
        # Check if the request was successful
        if response.status_code == 200:
            result = response.json()
            return result.get("success", False), result.get("message", "Unknown response")
        else:
            return False, f"Error: HTTP {response.status_code} - {response.text}"
            
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except json.JSONDecodeError:
        return False, f"Error parsing response: {response.text}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"

def send_audio_message(recipient: str, media_path: str) -> Tuple[bool, str]:
    try:
        # Validate input
        if not recipient:
            return False, "Recipient must be provided"
        
        if not media_path:
            return False, "Media path must be provided"
        
        if not os.path.isfile(media_path):
            return False, f"Media file not found: {media_path}"

        if not media_path.endswith(".ogg"):
            try:
                media_path = audio.convert_to_opus_ogg_temp(media_path)
            except Exception as e:
                return False, f"Error converting file to opus ogg. You likely need to install ffmpeg: {str(e)}"
        
        url = f"{WHATSAPP_API_BASE_URL}/send"
        payload = {
            "recipient": recipient,
            "media_path": media_path
        }
        
        response = requests.post(url, json=payload)
        
        # Check if the request was successful
        if response.status_code == 200:
            result = response.json()
            return result.get("success", False), result.get("message", "Unknown response")
        else:
            return False, f"Error: HTTP {response.status_code} - {response.text}"
            
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except json.JSONDecodeError:
        return False, f"Error parsing response: {response.text}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"

def download_media(message_id: str, chat_jid: str) -> Optional[str]:
    """Download media from a message and return the local file path.
    
    Args:
        message_id: The ID of the message containing the media
        chat_jid: The JID of the chat containing the message
    
    Returns:
        The local file path if download was successful, None otherwise
    """
    try:
        url = f"{WHATSAPP_API_BASE_URL}/download"
        payload = {
            "message_id": message_id,
            "chat_jid": chat_jid
        }
        
        response = requests.post(url, json=payload)
        
        if response.status_code == 200:
            result = response.json()
            if result.get("success", False):
                path = result.get("path")
                print(f"Media downloaded successfully: {path}")
                return path
            else:
                print(f"Download failed: {result.get('message', 'Unknown error')}")
                return None
        else:
            print(f"Error: HTTP {response.status_code} - {response.text}")
            return None
            
    except requests.RequestException as e:
        print(f"Request error: {str(e)}")
        return None
    except json.JSONDecodeError:
        print(f"Error parsing response: {response.text}")
        return None
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return None


def _bridge_post(path: str, payload: dict) -> Tuple[bool, str]:
    """POST to the bridge; it answers {"success", "message"} for successes and failures alike."""
    try:
        response = requests.post(f"{WHATSAPP_API_BASE_URL}/{path}", json=payload)
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    try:
        result = response.json()
    except ValueError:
        return False, f"Error: HTTP {response.status_code} - {response.text}"
    return result.get("success", False), result.get("message", "Unknown response")


def chat_of_message(message_id: str) -> Optional[str]:
    """The chat a stored message is in, so tools that act on a message need only its id."""
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        row = conn.execute(
            "SELECT chat_jid FROM messages WHERE id = ? ORDER BY timestamp DESC LIMIT 1", (message_id,)
        ).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        if 'conn' in locals():
            conn.close()


def reply_to_message(message_id: str, message: str, chat_jid: Optional[str] = None) -> Tuple[bool, str]:
    """Send text in the message's chat, quoting the message."""
    chat_jid = chat_jid or chat_of_message(message_id)
    if not chat_jid:
        return False, f"Message {message_id} not found"
    return _bridge_post("send", {"recipient": chat_jid, "message": message, "reply_to": message_id})


def send_reaction(message_id: str, emoji: str, chat_jid: Optional[str] = None) -> Tuple[bool, str]:
    """React with an emoji; an empty emoji removes our reaction."""
    return _bridge_post("react", {"message_id": message_id, "chat_jid": chat_jid or "", "emoji": emoji})


def edit_message(message_id: str, new_text: str, chat_jid: Optional[str] = None) -> Tuple[bool, str]:
    """Replace the text of a message we sent."""
    return _bridge_post("edit", {"message_id": message_id, "chat_jid": chat_jid or "", "message": new_text})


def delete_message(message_id: str, chat_jid: Optional[str] = None) -> Tuple[bool, str]:
    """Delete a message we sent, for everyone."""
    return _bridge_post("revoke", {"message_id": message_id, "chat_jid": chat_jid or ""})
