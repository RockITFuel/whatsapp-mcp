"""Names for WhatsApp JIDs, read from whatsmeow's own store (whatsapp.db).

Chats and senders are stored as bare numbers, and since WhatsApp's switch to LIDs most of
them are LIDs ("193045911867616@lid") that say nothing to a reader. whatsmeow keeps the
address book (whatsmeow_contacts, keyed by phone number) and the LID -> phone number map
(whatsmeow_lid_map), so a name is LID -> phone number -> contact: full name, else first
name, else the name the person set themselves (push name).
"""
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

STORE_DIR = os.environ.get("WHATSAPP_STORE_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "whatsapp-bridge", "store"
)
WHATSAPP_DB_PATH = os.path.join(STORE_DIR, "whatsapp.db")

USER_SERVER = "s.whatsapp.net"
LID_SERVER = "lid"


@dataclass
class Person:
    """One person, with the JID to send to (their phone number JID when we know it)."""
    jid: str
    name: Optional[str]
    phone_number: Optional[str]
    lid: Optional[str]
    is_me: bool = False


def _split(ident: str):
    """'31612345678:12@s.whatsapp.net' -> ('31612345678', 's.whatsapp.net'); a bare number has no server."""
    user, _, server = ident.strip().partition("@")
    return user.split(":")[0], (server or None)


class Directory:
    def __init__(self, lid_to_pn: Dict[str, str], names: Dict[str, str], me_pn: Optional[str], me_lid: Optional[str]):
        self.lid_to_pn = lid_to_pn
        self.pn_to_lid = {pn: lid for lid, pn in lid_to_pn.items()}
        self.names = names  # keyed by "<pn>@s.whatsapp.net" or "<lid>@lid", like whatsmeow_contacts
        self.me_pn = me_pn
        self.me_lid = me_lid

    def resolve(self, ident: Optional[str]) -> Optional[Person]:
        """A person for a bare number, phone JID or LID JID; None for groups, newsletters and broadcasts."""
        if not ident:
            return None
        user, server = _split(ident)
        if server not in (None, USER_SERVER, LID_SERVER) or not user.isdigit():
            return None
        if server == LID_SERVER or (server is None and user in self.lid_to_pn):
            lid, pn = user, self.lid_to_pn.get(user)
        else:
            pn, lid = user, self.pn_to_lid.get(user)
        name = (pn and self.names.get(f"{pn}@{USER_SERVER}")) or (lid and self.names.get(f"{lid}@{LID_SERVER}")) or None
        is_me = bool((pn and pn == self.me_pn) or (lid and lid == self.me_lid))
        if is_me and not name:
            name = "Me"
        jid = f"{pn}@{USER_SERVER}" if pn else f"{lid}@{LID_SERVER}"
        return Person(jid=jid, name=name, phone_number=pn, lid=lid, is_me=is_me)

    def name(self, ident: Optional[str]) -> Optional[str]:
        person = self.resolve(ident)
        return person.name if person else None

    def sender_forms(self, ident: str) -> List[str]:
        """Every way messages.db may have stored this person as a sender (bare pn, bare LID, LID JID, ...)."""
        person = self.resolve(ident)
        if not person:
            return [ident]
        forms = {ident}
        if person.phone_number:
            forms |= {person.phone_number, f"{person.phone_number}@{USER_SERVER}"}
        if person.lid:
            forms |= {person.lid, f"{person.lid}@{LID_SERVER}"}
        return sorted(forms)

    def search(self, query: str) -> List[Person]:
        """Contacts whose name contains the query, or whose phone number or LID contains its digits."""
        needle = query.strip().lower()
        digits = "".join(ch for ch in needle if ch.isdigit())
        found: Dict[str, Person] = {}
        for key, name in self.names.items():
            person = self.resolve(key)
            if not person:
                continue
            numbers = [n for n in (person.phone_number, person.lid) if n]
            if (needle and needle in name.lower()) or (digits and any(digits in n for n in numbers)):
                found[person.jid] = person
        return sorted(found.values(), key=lambda p: (p.name or "").lower())


def _load() -> Directory:
    if not os.path.exists(WHATSAPP_DB_PATH):
        return Directory({}, {}, None, None)
    # Read-only: the bridge owns this database and keeps writing to it.
    conn = sqlite3.connect(f"file:{WHATSAPP_DB_PATH}?mode=ro", uri=True)
    try:
        lid_to_pn = {lid: pn for lid, pn in conn.execute("SELECT lid, pn FROM whatsmeow_lid_map")}
        names: Dict[str, str] = {}
        for jid, full, first, push, business in conn.execute(
            "SELECT their_jid, full_name, first_name, push_name, business_name FROM whatsmeow_contacts"
        ):
            name = next((n for n in (full, first, push, business) if n), None)
            if name:
                names[jid] = name
        me_pn = me_lid = None
        row = conn.execute("SELECT jid, lid FROM whatsmeow_device LIMIT 1").fetchone()
        if row:
            me_pn = _split(row[0])[0]
            me_lid = _split(row[1])[0] if row[1] else None
        return Directory(lid_to_pn, names, me_pn, me_lid)
    except sqlite3.Error:
        return Directory({}, {}, None, None)
    finally:
        conn.close()


_cache: Optional[Directory] = None
_cache_at = 0.0


def directory() -> Directory:
    """The address book, reloaded at most every 30 seconds: one tool call formats many messages."""
    global _cache, _cache_at
    if _cache is None or time.monotonic() - _cache_at > 30:
        _cache, _cache_at = _load(), time.monotonic()
    return _cache
