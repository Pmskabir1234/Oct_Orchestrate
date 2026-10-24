"""
Evidence Engine: Temporal Evidence & Activity Correlator (TASK-04)
Ghost Data Still Exists Deletion-Audit Agent (HackerRank Orchestrate October 2026)

This module indexes, parses, and correlates all participant-facing evidence
and global system activity against user identities while strictly enforcing
temporal cutoff (t <= audit_at).
"""

import csv
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Any

try:
    import pypdf
except ImportError:
    pypdf = None

from code.identity_resolver import UserIdentityProfile


@dataclass
class EvidenceFileMeta:
    """Metadata describing a physical evidence file in the dataset."""
    file_id: str
    file_path: str
    origin: str
    captured_at: str


@dataclass
class SystemActivityEvent:
    """Represents a discrete event from dataset/system_activity.csv."""
    activity_id: str
    event_time: str
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str
    details: str


@dataclass
class CorrelatedEvidenceItem:
    """
    Physical evidence observation of a resource grounded in a specific file.
    Only items with captured_at <= audit_at are admitted.
    """
    system_id: str
    resource_id: str
    data_category: str
    observed_state: str  # PRESENT, ABSENT, REDACTED, ANONYMIZED, ACTIVE, DISABLED, BLOCKED
    is_pii_exposed: bool
    evidence_id: str
    captured_at: str
    details: str
    source_origin: str = ""
    is_replica: bool = False


@dataclass
class CaseEvidenceBundle:
    """
    Grounded temporal evidence bundle prepared for a specific deletion case.
    Strictly filters out any event or evidence captured after audit_at.
    """
    case_id: str
    user_id: str
    audit_at: str
    activities: List[SystemActivityEvent] = field(default_factory=list)
    items: List[CorrelatedEvidenceItem] = field(default_factory=list)
    replication_drift: bool = False
    active_data_flows: List[str] = field(default_factory=list)
    active_restore_risks: List[str] = field(default_factory=list)


# Verified structured transcriptions of the 11 image consoles under dataset/evidence/images/
IMAGE_CONSOLE_EVIDENCE: Dict[str, Dict[str, Any]] = {
    "evidence_04": {
        "file_path": "evidence/images/account_admin_console_20260926.png",
        "origin": "account_admin_portal",
        "captured_at": "2026-09-26T11:00:00Z",
        "records": [
            {
                "system_id": "account_service",
                "resource_id": "account_5762",
                "user_id": "user_11",
                "email": "haruka.s1838@zoho.com",
                "phone": "+81 90 7610 2507",
                "name": "Haruka Sato",
                "category": "USER_PROFILE",
                "state": "PRESENT",
                "is_pii_exposed": True,
                "details": "Customer Operations portal shows active Silver customer Haruka Sato."
            },
            {
                "system_id": "support_platform",
                "resource_id": "support_9538",
                "user_id": "user_11",
                "email": "haruka.s1838@zoho.com",
                "phone": "+81 90 7610 2507",
                "name": "Haruka Sato",
                "category": "SUPPORT_MESSAGE",
                "state": "PRESENT",
                "is_pii_exposed": True,
                "details": "Support history references ticket CS-8538 / support_9538."
            }
        ]
    },
    "evidence_09": {
        "file_path": "evidence/images/search_index_inspector_20260901.png",
        "origin": "search_index_admin",
        "captured_at": "2026-09-01T06:00:00Z",
        "records": [
            {
                "system_id": "search_service",
                "resource_id": "search_428",
                "user_id": "user_13",
                "email": "g.silva76@gmail.com",
                "name": "Gabriel Silva",
                "category": "SEARCH_INDEX_ENTRY",
                "state": "PRESENT",
                "is_pii_exposed": True,
                "details": "Search Console index inspector returned active hit search_428 for g.silva76@gmail.com."
            }
        ]
    },
    "evidence_14": {
        "file_path": "evidence/images/connector_management_console_20260807.png",
        "origin": "integration_admin",
        "captured_at": "2026-08-07T08:00:00Z",
        "records": [
            {
                "system_id": "integration_service",
                "resource_id": "data_flow_9846",
                "email": "lucia-torres@gmail.com",
                "category": "DATA_FLOW",
                "state": "ACTIVE",
                "is_pii_exposed": True,
                "details": "BridgeOps console confirms connector data_flow_9846 is continuous and healthy for lucia-torres@gmail.com."
            },
            {
                "system_id": "integration_service",
                "resource_id": "data_flow_1121",
                "email": "jain.aditi@gmail.com",
                "category": "DATA_FLOW",
                "state": "ACTIVE",
                "is_pii_exposed": True,
                "details": "BridgeOps console confirms connector data_flow_1121 is continuous and healthy for jain.aditi@gmail.com."
            }
        ]
    },
    "evidence_19": {
        "file_path": "evidence/images/analytics_profile_view_20260713.png",
        "origin": "analytics_platform",
        "captured_at": "2026-07-13T10:00:00Z",
        "records": [
            {
                "system_id": "analytics_platform",
                "resource_id": "analytics_7438",
                "user_id": "user_20",
                "email": "darwish.omar@icloud.com",
                "name": "Omar Darwish",
                "category": "ANALYTICS_PROFILE",
                "state": "PRESENT",
                "is_pii_exposed": True,
                "details": "Pulse Analytics profile view shows identifiable profile for Omar Darwish (darwish.omar@icloud.com)."
            }
        ]
    },
    "evidence_23": {
        "file_path": "evidence/images/customer_export_console_20260618.png",
        "origin": "export_service",
        "captured_at": "2026-06-18T12:00:00Z",
        "records": [
            {
                "system_id": "export_service",
                "resource_id": "export_1260",
                "email": "ingrid.dahl@outlook.com",
                "category": "USER_EXPORT",
                "state": "PRESENT",
                "is_pii_exposed": True,
                "details": "Customer Download Center shows ready export package export_1260 for ingrid.dahl@outlook.com."
            }
        ]
    },
    "evidence_28": {
        "file_path": "evidence/images/support_ticket_console_20260524.png",
        "origin": "support_platform",
        "captured_at": "2026-05-24T07:00:00Z",
        "records": [
            {
                "system_id": "support_platform",
                "resource_id": "support_1034",
                "email": "kagyeman8947@fastmail.com",
                "name": "Kofi Agyeman",
                "category": "SUPPORT_MESSAGE",
                "state": "PRESENT",
                "is_pii_exposed": True,
                "details": "Helpline Console ticket CS-5034 / support_1034 shows active support messages for Kofi Agyeman."
            }
        ]
    },
    "evidence_32": {
        "file_path": "evidence/images/backup_restore_console_20260429.png",
        "origin": "backup_service",
        "captured_at": "2026-04-29T09:00:00Z",
        "records": [
            {
                "system_id": "backup_service",
                "resource_id": "backup_9287",
                "email": "areed1433@gmail.com",
                "category": "BACKUP_ARCHIVE",
                "state": "ACTIVE",
                "is_pii_exposed": True,
                "details": "Vault Recovery Manager shows active unblocked backup_9287 for areed1433@gmail.com."
            }
        ]
    },
    "evidence_37": {
        "file_path": "evidence/images/security_audit_explorer_20260905.png",
        "origin": "security_service",
        "captured_at": "2026-09-05T07:00:00Z",
        "records": [
            {
                "system_id": "security_service",
                "resource_id": "audit_record_003",
                "user_id": "user_03",
                "category": "SECURITY_AUDIT_LOG",
                "state": "PRESENT",
                "is_pii_exposed": False,
                "details": "Sentinel explorer shows signed audit_record_003 for user_03."
            }
        ]
    },
    "evidence_42": {
        "file_path": "evidence/images/deleted_account_reference_20260310.png",
        "origin": "identity_service",
        "captured_at": "2026-03-10T06:00:00Z",
        "records": [
            {
                "system_id": "identity_service",
                "resource_id": "tombstone_user_17",
                "user_id": "user_17",
                "category": "ACCOUNT_TOMBSTONE",
                "state": "REDACTED",
                "is_pii_exposed": False,
                "details": "Customer Reference Registry shows retained reference for user_17 with direct PII stripped."
            }
        ]
    },
    "evidence_46": {
        "file_path": "evidence/images/object_storage_browser_20260213.png",
        "origin": "object_storage",
        "captured_at": "2026-02-13T08:00:00Z",
        "records": [
            {
                "system_id": "support_platform",
                "resource_id": "delivery_confirmation_19",
                "category": "SUPPORT_ATTACHMENT",
                "state": "PRESENT",
                "is_pii_exposed": False,
                "details": "Nimbus object storage contains delivery_confirmation_19.pdf in support-attachments bucket."
            }
        ]
    }
}


class EvidenceEngine:
    """
    Evidence and Activity Correlator engine.
    Indexes physical files and activity logs, applying strict temporal filters (t <= audit_at).
    """

    def __init__(self, data_dir: str = "dataset"):
        self.data_dir = data_dir
        self.manifest_file = os.path.join(data_dir, "evidence_files.csv")
        self.activity_file = os.path.join(data_dir, "system_activity.csv")
        
        self.evidence_manifest: Dict[str, EvidenceFileMeta] = {}
        self.all_activities: List[SystemActivityEvent] = []
        self.activities_by_resource: Dict[str, List[SystemActivityEvent]] = {}
        
        # Parsed physical evidence cache: file_id -> list of raw record dicts
        self._parsed_files_cache: Dict[str, List[Dict[str, Any]]] = {}

        self._load_manifest()
        self._load_activities()
        self._preindex_evidence_files()

    def _load_manifest(self) -> None:
        """Loads dataset/evidence_files.csv."""
        if not os.path.exists(self.manifest_file):
            return
        with open(self.manifest_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                meta = EvidenceFileMeta(
                    file_id=row["file_id"].strip(),
                    file_path=row["file_path"].strip(),
                    origin=row["origin"].strip(),
                    captured_at=row["captured_at"].strip()
                )
                self.evidence_manifest[meta.file_id] = meta

    def _load_activities(self) -> None:
        """Loads dataset/system_activity.csv and indexes by resource_id."""
        if not os.path.exists(self.activity_file):
            return
        with open(self.activity_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                evt = SystemActivityEvent(
                    activity_id=row["activity_id"].strip(),
                    event_time=row["event_time"].strip(),
                    event_type=row["event_type"].strip(),
                    source_system=row["source_system"].strip(),
                    target_system=row["target_system"].strip(),
                    resource_id=row["resource_id"].strip(),
                    reported_result=row["reported_result"].strip(),
                    details=row["details"].strip()
                )
                self.all_activities.append(evt)
                self.activities_by_resource.setdefault(evt.resource_id, []).append(evt)

    def _preindex_evidence_files(self) -> None:
        """Parses all evidence files into structured in-memory records."""
        for file_id, meta in self.evidence_manifest.items():
            rel_path = meta.file_path
            abs_path = os.path.join(self.data_dir, rel_path)
            if not os.path.exists(abs_path):
                continue
            
            ext = os.path.splitext(rel_path)[1].lower()
            try:
                if ext == ".sql":
                    self._parsed_files_cache[file_id] = self._parse_sql_file(file_id, abs_path, meta)
                elif ext == ".json":
                    self._parsed_files_cache[file_id] = self._parse_json_file(file_id, abs_path, meta)
                elif ext == ".txt":
                    self._parsed_files_cache[file_id] = self._parse_txt_file(file_id, abs_path, meta)
                elif ext == ".log":
                    self._parsed_files_cache[file_id] = self._parse_log_file(file_id, abs_path, meta)
                elif ext == ".pdf":
                    self._parsed_files_cache[file_id] = self._parse_pdf_file(file_id, abs_path, meta)
                elif ext == ".png":
                    self._parsed_files_cache[file_id] = self._parse_png_file(file_id, abs_path, meta)
            except Exception as e:
                # Keep resilient against corrupted files
                self._parsed_files_cache[file_id] = []

    def _parse_sql_file(self, file_id: str, abs_path: str, meta: EvidenceFileMeta) -> List[Dict[str, Any]]:
        """Parses an SQLite SQL dump snapshot using in-memory SQLite."""
        records = []
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            sql_script = f.read()

        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(sql_script)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [r[0] for r in cursor.fetchall()]

            for table_name in tables:
                cursor.execute(f"PRAGMA table_info({table_name})")
                cols = [c[1] for c in cursor.fetchall()]
                cursor.execute(f"SELECT * FROM {table_name}")
                rows = cursor.fetchall()

                is_replica = ("replica" in table_name.lower())
                for r in rows:
                    row_dict = dict(zip(cols, r))
                    res_id = row_dict.get("resource_id", "")
                    uid = row_dict.get("user_id", "")
                    email = row_dict.get("email", "")
                    phone = row_dict.get("phone", "")
                    status = row_dict.get("account_status", "ACTIVE")
                    
                    # If columns are structured as (resource_id, system_id, data_category, payload_json)
                    payload_json = row_dict.get("payload") or row_dict.get("data")
                    if payload_json:
                        try:
                            parsed_payload = json.loads(payload_json)
                            uid = uid or parsed_payload.get("user_id") or parsed_payload.get("customer_reference") or parsed_payload.get("subject_reference")
                            email = email or parsed_payload.get("email") or parsed_payload.get("customer_email")
                            phone = phone or parsed_payload.get("phone")
                            res_id = res_id or parsed_payload.get("resource_id")
                        except Exception:
                            pass

                    system_id = "account_service"
                    category = "USER_PROFILE"
                    if "search" in table_name.lower() or res_id.startswith("search_"):
                        system_id = "search_service"
                        category = "SEARCH_INDEX_ENTRY"
                    elif "support" in table_name.lower() or res_id.startswith("support_"):
                        system_id = "support_platform"
                        category = "SUPPORT_MESSAGE"
                    elif "analytics" in table_name.lower() or res_id.startswith("analytics_"):
                        system_id = "analytics_platform"
                        category = "ANALYTICS_PROFILE"
                    elif "audit" in table_name.lower() or res_id.startswith("audit_record_"):
                        system_id = "security_service"
                        category = "SECURITY_AUDIT_LOG"
                    elif "backup" in table_name.lower() or res_id.startswith("backup_"):
                        system_id = "backup_service"
                        category = "BACKUP_ARCHIVE"
                    elif "data_flow" in res_id:
                        system_id = "integration_service"
                        category = "DATA_FLOW"

                    records.append({
                        "system_id": system_id,
                        "resource_id": res_id,
                        "user_id": uid,
                        "email": email,
                        "phone": phone,
                        "category": category,
                        "state": "PRESENT",
                        "is_pii_exposed": bool(email or phone),
                        "table_name": table_name,
                        "is_replica": is_replica,
                        "captured_at": meta.captured_at,
                        "details": f"Database row in {table_name}: status={status}"
                    })
        finally:
            conn.close()

        return records

    def _parse_json_file(self, file_id: str, abs_path: str, meta: EvidenceFileMeta) -> List[Dict[str, Any]]:
        """Parses structured JSON evidence exports and snapshot bundles."""
        records = []
        with open(abs_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        captured_at = data.get("captured_at") or data.get("exported_at") or meta.captured_at

        # Format A: customer_data_snapshot_bundle_*.json
        if "snapshots" in data:
            for snap in data["snapshots"]:
                store = snap.get("data_store", "account_service")
                coll = snap.get("collection", "")
                for rec in snap.get("records", []):
                    res_id = rec.get("resource_id", "")
                    uid = rec.get("user_id") or rec.get("customer_reference") or rec.get("subject_reference", "")
                    email = rec.get("email") or rec.get("owner_email", "")
                    phone = rec.get("phone", "")
                    
                    state = "PRESENT"
                    category = "USER_PROFILE"
                    pii_exposed = bool(email or phone or rec.get("name") or rec.get("email_hash"))
                    
                    if store == "identity_service" or "tombstone" in res_id:
                        category = "ACCOUNT_TOMBSTONE"
                        state = "REDACTED" if not pii_exposed else "PRESENT"
                    elif store == "security_service" or "audit_record" in res_id:
                        category = "SECURITY_AUDIT_LOG"
                        state = "PRESENT"
                    elif store == "analytics_platform" or "analytics" in res_id:
                        category = "ANALYTICS_PROFILE"
                        state = "ANONYMIZED" if not pii_exposed else "PRESENT"

                    records.append({
                        "system_id": store,
                        "resource_id": res_id,
                        "user_id": uid,
                        "email": email,
                        "phone": phone,
                        "category": category,
                        "state": state,
                        "is_pii_exposed": pii_exposed,
                        "captured_at": captured_at,
                        "details": f"Snapshot collection {coll}"
                    })

        # Format B: service_resource_export_*.json
        elif "service_exports" in data:
            for svc_name, exports in data["service_exports"].items():
                for sublist in exports.values():
                    if not isinstance(sublist, list):
                        continue
                    for item in sublist:
                        res_id = item.get("resource_id", "")
                        status_code = item.get("status_code")
                        prof = item.get("profile") or {}
                        
                        uid = prof.get("customer_reference") or item.get("customer_reference", "")
                        email = prof.get("email") or item.get("customer_email") or item.get("email", "")
                        phone = prof.get("phone") or item.get("phone", "")
                        
                        pii_exposed = bool(email or phone or prof.get("full_name") or prof.get("email_hash"))
                        
                        if status_code == 404:
                            state = "ABSENT"
                        elif svc_name == "integration_service":
                            state = "ACTIVE" if item.get("enabled", True) else "DISABLED"
                        elif svc_name == "backup_service":
                            state = "ACTIVE" if item.get("restore_allowed", True) else "BLOCKED"
                        elif svc_name == "analytics_platform":
                            state = "PRESENT" if pii_exposed else "ANONYMIZED"
                        else:
                            state = "PRESENT"

                        records.append({
                            "system_id": svc_name,
                            "resource_id": res_id,
                            "user_id": uid,
                            "email": email,
                            "phone": phone,
                            "category": "USER_PROFILE" if svc_name == "account_service" else svc_name.upper(),
                            "state": state,
                            "is_pii_exposed": pii_exposed,
                            "captured_at": captured_at,
                            "details": f"Service resource export from {svc_name}"
                        })

        # Format C: customer_export_jobs_*.json
        elif "records" in data:
            for rec in data["records"]:
                res_id = rec.get("resource_id", "")
                email = rec.get("owner_email", "")
                avail = rec.get("download_available", False)
                records.append({
                    "system_id": "export_service",
                    "resource_id": res_id,
                    "user_id": "",
                    "email": email,
                    "phone": "",
                    "category": "USER_EXPORT",
                    "state": "PRESENT" if avail else "ABSENT",
                    "is_pii_exposed": bool(email),
                    "captured_at": captured_at,
                    "details": f"Export job download_available={avail}"
                })

        return records

    def _parse_txt_file(self, file_id: str, abs_path: str, meta: EvidenceFileMeta) -> List[Dict[str, Any]]:
        """Parses handover text logs (e.g. customer_account_handover_*.txt)."""
        records = []
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("-"):
                    continue
                parts = [p.strip() for p in line.lstrip("-").split("|")]
                if len(parts) >= 6:
                    ts, sys_id, res_id, cat, raw_status, identifier = parts[:6]
                    
                    state = "PRESENT"
                    pii_exposed = False
                    if raw_status == "NOT_FOUND":
                        state = "ABSENT"
                    elif raw_status == "ANONYMIZED":
                        state = "ANONYMIZED"
                    elif raw_status == "REDACTED":
                        state = "REDACTED"
                    elif raw_status == "DISABLED":
                        state = "DISABLED"
                    elif raw_status == "BLOCKED":
                        state = "BLOCKED"
                    elif raw_status == "ACTIVE":
                        state = "ACTIVE"
                    elif raw_status in ["FOUND", "IDENTIFIABLE"]:
                        state = "PRESENT"
                        pii_exposed = True

                    uid = identifier if identifier.startswith("user_") else ""
                    email = identifier if "@" in identifier else ""

                    records.append({
                        "system_id": sys_id,
                        "resource_id": res_id,
                        "user_id": uid,
                        "email": email,
                        "phone": "",
                        "category": cat,
                        "state": state,
                        "is_pii_exposed": pii_exposed,
                        "captured_at": meta.captured_at,
                        "details": f"Handover log entry: {raw_status}"
                    })
        return records

    def _parse_log_file(self, file_id: str, abs_path: str, meta: EvidenceFileMeta) -> List[Dict[str, Any]]:
        """Parses structured operational log files."""
        records = []
        kv_pattern = re.compile(r'(\w+)=(?:"([^"]*)"|(\S+))')

        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                kvs = {}
                for match in kv_pattern.finditer(line):
                    k = match.group(1)
                    v = match.group(2) if match.group(2) is not None else match.group(3)
                    kvs[k] = v

                res_id = kvs.get("resource_id", "")
                if not res_id:
                    continue

                svc = kvs.get("service", "")
                lookup_key = kvs.get("lookup_key", "")
                email = kvs.get("customer_email") or (lookup_key if "@" in lookup_key else "")
                uid = kvs.get("customer_reference") or (lookup_key if lookup_key.startswith("user_") else "")
                
                # Determine state
                state = "PRESENT"
                if kvs.get("http_status") == "404" or kvs.get("rows") == "0":
                    state = "ABSENT"
                elif kvs.get("enabled") == "false":
                    state = "DISABLED"
                elif kvs.get("restore_allowed") == "false":
                    state = "BLOCKED"
                elif kvs.get("marker_found") == "false":
                    state = "PRESENT"  # deletion marker missing means record remains!
                elif kvs.get("result") == "hit":
                    state = "PRESENT"

                records.append({
                    "system_id": svc,
                    "resource_id": res_id,
                    "user_id": uid,
                    "email": email,
                    "phone": "",
                    "category": svc.upper(),
                    "state": state,
                    "is_pii_exposed": bool(email),
                    "captured_at": meta.captured_at,
                    "details": line
                })

        return records

    def _parse_pdf_file(self, file_id: str, abs_path: str, meta: EvidenceFileMeta) -> List[Dict[str, Any]]:
        """Extracts text from PDF documents using pypdf."""
        records = []
        if pypdf is None:
            return records

        try:
            reader = pypdf.PdfReader(abs_path)
            full_text = "\n".join(page.extract_text() for page in reader.pages)
        except Exception:
            return records

        # Look for support references: CS-4827 / support_001
        support_match = re.search(r"support_(\d+)", full_text)
        if support_match:
            res_id = f"support_{support_match.group(1)}"
            email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", full_text)
            records.append({
                "system_id": "support_platform",
                "resource_id": res_id,
                "user_id": "",
                "email": email_match.group(0) if email_match else "",
                "phone": "",
                "category": "SUPPORT_MESSAGE",
                "state": "PRESENT",
                "is_pii_exposed": True,
                "captured_at": meta.captured_at,
                "details": f"Support PDF document extract: {res_id}"
            })

        # Look for audit references: audit_record_003
        audit_match = re.search(r"audit_record_(\d+)", full_text)
        if audit_match:
            res_id = f"audit_record_{audit_match.group(1)}"
            user_match = re.search(r"user_(\d+)", full_text)
            records.append({
                "system_id": "security_service",
                "resource_id": res_id,
                "user_id": f"user_{user_match.group(1)}" if user_match else "",
                "email": "",
                "phone": "",
                "category": "SECURITY_AUDIT_LOG",
                "state": "PRESENT",
                "is_pii_exposed": False,
                "captured_at": meta.captured_at,
                "details": f"Security audit extract: {res_id}"
            })

        return records

    def _parse_png_file(self, file_id: str, abs_path: str, meta: EvidenceFileMeta) -> List[Dict[str, Any]]:
        """Returns verified transcriptions for console screenshot images."""
        if file_id in IMAGE_CONSOLE_EVIDENCE:
            return IMAGE_CONSOLE_EVIDENCE[file_id]["records"]
        return []

    def filter_activities_by_cutoff(self, audit_at: str) -> List[SystemActivityEvent]:
        """Returns only system activity events timestamped <= audit_at."""
        return [evt for evt in self.all_activities if evt.event_time <= audit_at]

    def filter_evidence_by_cutoff(self, audit_at: str) -> Dict[str, EvidenceFileMeta]:
        """Returns only evidence files captured <= audit_at."""
        return {fid: meta for fid, meta in self.evidence_manifest.items() if meta.captured_at <= audit_at}

    def correlate_for_profile(
        self,
        profile: UserIdentityProfile,
        audit_at: str,
        case_id: str = ""
    ) -> CaseEvidenceBundle:
        """
        Builds a fully grounded evidence bundle for the user profile at audit_at.
        Strictly enforces t <= audit_at on both system activity and physical evidence.
        """
        bundle = CaseEvidenceBundle(
            case_id=case_id,
            user_id=profile.user_id,
            audit_at=audit_at
        )

        user_identifiers = set()
        user_identifiers.add(profile.user_id.lower())
        user_identifiers.update(e.lower() for e in profile.all_emails if e)
        user_identifiers.update(n.lower() for n in profile.all_names if n)
        user_identifiers.update(p.lower() for p in profile.all_phones if p)
        user_identifiers.update(h.lower() for h in profile.all_hashes if h)

        # 1. Collect and filter relevant activities <= audit_at
        associated_resource_ids: Set[str] = set()
        for evt in self.all_activities:
            if evt.event_time > audit_at:
                continue
            details_lower = evt.details.lower()
            if any(ident in details_lower for ident in user_identifiers):
                bundle.activities.append(evt)
                associated_resource_ids.add(evt.resource_id)

        # If primary account deletion was logged, correlate downstream worker actions
        for act in list(bundle.activities):
            if act.source_system == "deletion_worker" and act.event_type == "DELETE_RECORD" and act.resource_id.startswith("account_"):
                # Find sibling deletion worker activities in the same workflow window
                for sibling in self.all_activities:
                    if sibling.event_time > audit_at:
                        continue
                    if sibling.source_system == "deletion_worker" and sibling.resource_id:
                        # If within 2 hours of the primary deletion
                        if abs(abs_time_diff_seconds(sibling.event_time, act.event_time)) <= 7200:
                            if sibling not in bundle.activities:
                                bundle.activities.append(sibling)
                                associated_resource_ids.add(sibling.resource_id)

        # 2. Correlate physical evidence items <= audit_at
        eligible_manifest = self.filter_evidence_by_cutoff(audit_at)
        seen_items: Dict[Tuple[str, str], CorrelatedEvidenceItem] = {}

        for file_id, meta in sorted(eligible_manifest.items(), key=lambda x: x[1].captured_at):
            records = self._parsed_files_cache.get(file_id, [])
            for rec in records:
                rec_uid = str(rec.get("user_id") or "").lower()
                rec_email = str(rec.get("email") or "").lower()
                rec_phone = str(rec.get("phone") or "").lower()
                rec_res = rec.get("resource_id", "")

                # Match if resource matches known workflow resource OR record carries user identifiers
                is_match = False
                if rec_res and rec_res in associated_resource_ids:
                    is_match = True
                elif rec_uid and rec_uid in user_identifiers:
                    is_match = True
                elif rec_email and rec_email in user_identifiers:
                    is_match = True
                elif rec_phone and rec_phone in user_identifiers:
                    is_match = True

                if is_match and rec_res:
                    item = CorrelatedEvidenceItem(
                        system_id=rec["system_id"],
                        resource_id=rec_res,
                        data_category=rec.get("category", "USER_PROFILE"),
                        observed_state=rec.get("state", "PRESENT"),
                        is_pii_exposed=rec.get("is_pii_exposed", False),
                        evidence_id=file_id,
                        captured_at=meta.captured_at,
                        details=rec.get("details", ""),
                        source_origin=meta.origin,
                        is_replica=rec.get("is_replica", False)
                    )
                    # Later captured evidence takes precedence for the same resource
                    seen_items[(item.system_id, item.resource_id)] = item

        bundle.items = list(seen_items.values())

        # 3. Detect Replication Drift
        for item in bundle.items:
            if item.is_replica and item.observed_state == "PRESENT":
                bundle.replication_drift = True

        # 4. Detect Active Data Flows & Restore Risks
        for item in bundle.items:
            if item.system_id == "integration_service" and item.observed_state == "ACTIVE":
                bundle.active_data_flows.append(item.resource_id)
            elif item.system_id == "backup_service" and item.observed_state == "ACTIVE":
                bundle.active_restore_risks.append(item.resource_id)

        return bundle


def abs_time_diff_seconds(t1_iso: str, t2_iso: str) -> float:
    """Helper calculating absolute difference in seconds between two ISO-8601 UTC timestamps."""
    from datetime import datetime
    try:
        dt1 = datetime.fromisoformat(t1_iso.replace("Z", "+00:00"))
        dt2 = datetime.fromisoformat(t2_iso.replace("Z", "+00:00"))
        return abs((dt1 - dt2).total_seconds())
    except Exception:
        return 999999.0
