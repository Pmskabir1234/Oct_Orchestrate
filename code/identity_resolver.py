"""
Identity & Alias Graph Resolver for Ghost Data Deletion Audit Agent.
Reconciles current user identity snapshots with historical mutation audit logs
to build unified identity profiles containing all active and historical identifiers
(emails, phones, names, SHA-256 hashes, and user IDs).

Adheres strictly to the specification in docs/SPECIFICATION.md and docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

logger = logging.getLogger("IdentityResolver")


def normalize_email(email: str) -> str:
    """Normalize email address: trim whitespace and lowercase."""
    return email.strip().lower() if email else ""


def normalize_phone(phone: str) -> str:
    """
    Extract digits from phone number for resilient matching across international
    formatting conventions (+33 6 24 34 80 16 -> 33624348016).
    """
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    return digits


def compute_sha256(val: str) -> str:
    """Compute SHA-256 hexadecimal digest of UTF-8 encoded string."""
    return hashlib.sha256(val.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class UserIdentityRecord:
    """Represents a single record from user_identities.csv."""
    user_id: str
    name: str
    email: str
    phone: str
    email_hash: str
    updated_at: str


@dataclass(frozen=True)
class AuditMutationRecord:
    """Represents a single field mutation record from user_audit_log.csv."""
    audit_id: str
    user_id: str
    event_time: str
    field_name: str
    old_value: str
    new_value: str


@dataclass
class UserIdentityProfile:
    """
    Unified user identity graph consolidating current snapshot state and historical
    audit mutations into an integrated profile.
    """
    user_id: str
    current_name: str
    current_email: str
    current_phone: str
    current_email_hash: str
    updated_at: str
    historical_emails: Set[str] = field(default_factory=set)
    historical_phones: Set[str] = field(default_factory=set)
    historical_names: Set[str] = field(default_factory=set)
    historical_hashes: Set[str] = field(default_factory=set)
    audit_mutation_ids: List[str] = field(default_factory=list)

    @property
    def all_emails(self) -> Set[str]:
        """Set of all normalized lowercase emails (current and historical)."""
        emails = {normalize_email(self.current_email)}
        for e in self.historical_emails:
            norm = normalize_email(e)
            if norm:
                emails.add(norm)
        emails.discard("")
        return emails

    @property
    def all_phones(self) -> Set[str]:
        """Set of all raw phone numbers (current and historical)."""
        phones = {self.current_phone.strip()} if self.current_phone else set()
        for p in self.historical_phones:
            if p and p.strip():
                phones.add(p.strip())
        return phones

    @property
    def all_phone_digits(self) -> Set[str]:
        """Set of normalized digit strings for all phones."""
        digits = {normalize_phone(p) for p in self.all_phones}
        digits.discard("")
        return digits

    @property
    def all_names(self) -> Set[str]:
        """Set of all names (current and historical)."""
        names = {self.current_name.strip()} if self.current_name else set()
        for n in self.historical_names:
            if n and n.strip():
                names.add(n.strip())
        return names

    @property
    def all_hashes(self) -> Set[str]:
        """
        Set of all known email hashes: current hash, historical hashes from audit log,
        plus SHA-256 computed on every known email variant.
        """
        hashes = set()
        if self.current_email_hash:
            hashes.add(self.current_email_hash.strip().lower())
        for h in self.historical_hashes:
            if h and h.strip():
                hashes.add(h.strip().lower())
        for e in self.all_emails:
            hashes.add(compute_sha256(e))
        hashes.discard("")
        return hashes

    @property
    def all_identifiers(self) -> Set[str]:
        """
        Comprehensive set of all string identifiers associated with this user
        (user_id, emails, phones, names, and hashes).
        """
        return (
            {self.user_id}
            | self.all_emails
            | self.all_phones
            | self.all_names
            | self.all_hashes
        )

    def contains_email(self, email: str) -> bool:
        """Check if email matches current or historical emails (case-insensitive)."""
        return normalize_email(email) in self.all_emails

    def contains_phone(self, phone: str) -> bool:
        """Check if phone matches by exact string or normalized digits."""
        if not phone:
            return False
        clean = phone.strip()
        if clean in self.all_phones:
            return True
        norm = normalize_phone(phone)
        return bool(norm and norm in self.all_phone_digits)

    def contains_hash(self, hash_val: str) -> bool:
        """Check if hash matches current, historical, or derived email hashes."""
        if not hash_val:
            return False
        return hash_val.strip().lower() in self.all_hashes

    def matches_text(self, text: str) -> bool:
        """Check if given text contains any user identifier (user_id, email, phone, or hash)."""
        if not text:
            return False
        lower_text = text.lower()
        if self.user_id.lower() in lower_text:
            return True
        for e in self.all_emails:
            if e in lower_text:
                return True
        for h in self.all_hashes:
            if h in lower_text:
                return True
        for p in self.all_phones:
            if p in text:
                return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        """Serialize profile to JSON-serializable dictionary."""
        return {
            "user_id": self.user_id,
            "current_name": self.current_name,
            "current_email": self.current_email,
            "current_phone": self.current_phone,
            "current_email_hash": self.current_email_hash,
            "updated_at": self.updated_at,
            "historical_emails": sorted(list(self.historical_emails)),
            "historical_phones": sorted(list(self.historical_phones)),
            "historical_names": sorted(list(self.historical_names)),
            "historical_hashes": sorted(list(self.historical_hashes)),
            "audit_mutation_ids": self.audit_mutation_ids,
        }


def find_default_dataset_dir() -> Path:
    """
    Dynamically locate the dataset directory by checking current directory,
    nested subprojects, or parent paths.
    """
    candidates = [
        Path.cwd() / "dataset",
        Path.cwd() / "Oct_Orchestrate" / "dataset",
        Path.cwd().parent / "dataset",
        Path.cwd().parent / "Oct_Orchestrate" / "dataset",
    ]
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "user_identities.csv").is_file():
            return candidate.resolve()
    # Fallback to current working directory dataset
    return (Path.cwd() / "dataset").resolve()


class IdentityResolver:
    """
    Enterprise-grade identity resolution engine.
    Indexes user_identities.csv and user_audit_log.csv into O(1) hash maps,
    reconciling current identity snapshots with historical audit mutations.
    """

    def __init__(
        self,
        dataset_dir: Optional[Union[str, Path]] = None,
        identities_path: Optional[Union[str, Path]] = None,
        audit_log_path: Optional[Union[str, Path]] = None,
    ):
        if dataset_dir is not None:
            self.dataset_dir = Path(dataset_dir)
        else:
            self.dataset_dir = find_default_dataset_dir()

        self.identities_path = (
            Path(identities_path)
            if identities_path is not None
            else self.dataset_dir / "user_identities.csv"
        )
        self.audit_log_path = (
            Path(audit_log_path)
            if audit_log_path is not None
            else self.dataset_dir / "user_audit_log.csv"
        )

        # Primary storage: user_id -> UserIdentityProfile
        self.profiles_by_user_id: Dict[str, UserIdentityProfile] = {}

        # O(1) Reverse Indexes
        self._email_to_user_id: Dict[str, str] = {}
        self._phone_to_user_id: Dict[str, str] = {}
        self._phone_digits_to_user_id: Dict[str, str] = {}
        self._hash_to_user_id: Dict[str, str] = {}

        # Load data on instantiation
        self._load_identities()
        self._load_audit_logs()

    def _load_identities(self) -> None:
        """Parse user_identities.csv and populate current identity profiles."""
        if not self.identities_path.exists():
            logger.warning("Identities file not found: %s", self.identities_path)
            return

        with open(self.identities_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                uid = row["user_id"].strip()
                name = row.get("name", "").strip()
                email = row.get("email", "").strip()
                phone = row.get("phone", "").strip()
                email_hash = row.get("email_hash", "").strip().lower()
                updated_at = row.get("updated_at", "").strip()

                profile = UserIdentityProfile(
                    user_id=uid,
                    current_name=name,
                    current_email=email,
                    current_phone=phone,
                    current_email_hash=email_hash,
                    updated_at=updated_at,
                )

                self.profiles_by_user_id[uid] = profile

                # Index email
                norm_email = normalize_email(email)
                if norm_email:
                    self._email_to_user_id[norm_email] = uid

                # Index phone
                if phone:
                    self._phone_to_user_id[phone] = uid
                    norm_p = normalize_phone(phone)
                    if norm_p:
                        self._phone_digits_to_user_id[norm_p] = uid

                # Index hash
                if email_hash:
                    self._hash_to_user_id[email_hash] = uid

    def _load_audit_logs(self) -> None:
        """
        Parse user_audit_log.csv and attach historical email/phone/name/hash mutations
        to existing user profiles.
        """
        if not self.audit_log_path.exists():
            logger.warning("Audit log file not found: %s", self.audit_log_path)
            return

        with open(self.audit_log_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                audit_id = row.get("audit_id", "").strip()
                uid = row.get("user_id", "").strip()
                field_name = row.get("field_name", "").strip().lower()
                old_val = row.get("old_value", "").strip()
                new_val = row.get("new_value", "").strip()

                profile = self.profiles_by_user_id.get(uid)
                if not profile:
                    # User not in identities snapshot, skip or log
                    continue

                if audit_id:
                    profile.audit_mutation_ids.append(audit_id)

                if field_name == "email":
                    for val in (old_val, new_val):
                        if val:
                            profile.historical_emails.add(val)
                            norm_val = normalize_email(val)
                            if norm_val:
                                self._email_to_user_id[norm_val] = uid
                                computed_h = compute_sha256(norm_val)
                                self._hash_to_user_id[computed_h] = uid

                elif field_name == "phone":
                    for val in (old_val, new_val):
                        if val:
                            profile.historical_phones.add(val)
                            self._phone_to_user_id[val] = uid
                            norm_p = normalize_phone(val)
                            if norm_p:
                                self._phone_digits_to_user_id[norm_p] = uid

                elif field_name == "email_hash":
                    for val in (old_val, new_val):
                        if val:
                            h_clean = val.lower()
                            profile.historical_hashes.add(h_clean)
                            self._hash_to_user_id[h_clean] = uid

                elif field_name == "name":
                    for val in (old_val, new_val):
                        if val:
                            profile.historical_names.add(val)

    def resolve_by_email(self, email: str) -> Optional[UserIdentityProfile]:
        """
        Resolve user profile by email address (case-insensitive, whitespace-trimmed).
        Matches against current registered email as well as all historical emails.
        """
        if not email:
            return None
        norm = normalize_email(email)
        uid = self._email_to_user_id.get(norm)
        if uid:
            return self.profiles_by_user_id.get(uid)
        return None

    def resolve_by_phone(self, phone: str) -> Optional[UserIdentityProfile]:
        """
        Resolve user profile by phone number.
        Matches exact string formatting or stripped normalized digits.
        """
        if not phone:
            return None
        clean = phone.strip()
        uid = self._phone_to_user_id.get(clean)
        if uid:
            return self.profiles_by_user_id.get(uid)

        digits = normalize_phone(phone)
        if digits:
            uid = self._phone_digits_to_user_id.get(digits)
            if uid:
                return self.profiles_by_user_id.get(uid)
        return None

    def resolve_by_hash(self, hash_val: str) -> Optional[UserIdentityProfile]:
        """Resolve user profile by SHA-256 email hash (case-insensitive)."""
        if not hash_val:
            return None
        clean = hash_val.strip().lower()
        uid = self._hash_to_user_id.get(clean)
        if uid:
            return self.profiles_by_user_id.get(uid)
        return None

    def resolve_by_user_id(self, user_id: str) -> Optional[UserIdentityProfile]:
        """Resolve user profile by exact user_id (e.g. 'user_01')."""
        if not user_id:
            return None
        return self.profiles_by_user_id.get(user_id.strip())

    def resolve(self, query: str) -> Optional[UserIdentityProfile]:
        """
        Polymorphic resolution query: tries user_id, email, hash, and phone.
        Returns matching UserIdentityProfile or None.
        """
        if not query:
            return None
        clean = query.strip()

        # Try user_id directly
        if clean in self.profiles_by_user_id:
            return self.profiles_by_user_id[clean]

        # Try email
        if "@" in clean:
            by_email = self.resolve_by_email(clean)
            if by_email:
                return by_email

        # Try SHA-256 hex string (length 64)
        if len(clean) == 64 and all(c in "0123456789abcdefABCDEF" for c in clean):
            by_hash = self.resolve_by_hash(clean)
            if by_hash:
                return by_hash

        # Try phone
        by_phone = self.resolve_by_phone(clean)
        if by_phone:
            return by_phone

        # Fallback attempts
        return self.resolve_by_email(clean) or self.resolve_by_user_id(clean)

    def get_all_profiles(self) -> List[UserIdentityProfile]:
        """Return list of all registered UserIdentityProfiles."""
        return list(self.profiles_by_user_id.values())

    def __len__(self) -> int:
        return len(self.profiles_by_user_id)

    def summary(self) -> Dict[str, int]:
        """Returns diagnostic metrics about indexed identity graphs."""
        total_hist_emails = sum(len(p.historical_emails) for p in self.profiles_by_user_id.values())
        total_hist_phones = sum(len(p.historical_phones) for p in self.profiles_by_user_id.values())
        total_hist_hashes = sum(len(p.historical_hashes) for p in self.profiles_by_user_id.values())

        return {
            "total_users": len(self.profiles_by_user_id),
            "indexed_unique_emails": len(self._email_to_user_id),
            "indexed_unique_phones": len(self._phone_to_user_id),
            "indexed_unique_hashes": len(self._hash_to_user_id),
            "users_with_historical_emails": sum(
                1 for p in self.profiles_by_user_id.values() if p.historical_emails
            ),
            "total_historical_emails": total_hist_emails,
            "total_historical_phones": total_hist_phones,
            "total_historical_hashes": total_hist_hashes,
        }
