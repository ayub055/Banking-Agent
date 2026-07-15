"""Audit logging for pipeline queries."""

import json
import os
from datetime import datetime
from pathlib import Path
from schemas.response import AuditLog


class AuditLogger:
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.log_file = self.log_dir / f"audit_{datetime.now().strftime('%Y%m%d')}.jsonl"

    def log(self, audit: AuditLog):
        entry = audit.model_dump()
        entry["timestamp"] = entry["timestamp"].isoformat()
        entry["parsed_intent"] = {
            k: v.value if hasattr(v, 'value') else v
            for k, v in entry["parsed_intent"].items()
        }

        with open(self.log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def get_logs(self, date: str = None) -> list:
        if date:
            log_file = self.log_dir / f"audit_{date}.jsonl"
        else:
            log_file = self.log_file

        if not log_file.exists():
            return []

        logs = []
        with open(log_file) as f:
            for line in f:
                if line.strip():
                    logs.append(json.loads(line))
        return logs
