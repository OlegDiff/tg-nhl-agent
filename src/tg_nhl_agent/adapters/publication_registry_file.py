# src/tg_nhl_agent/adapters/publication_registry_file.py
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from ..contracts import AppError, PublicationRecord, Severity, SourceSystem


class FilePublicationRegistry:
    """File-based idempotency registry (v1).

    Storage layout:
      <state_path>/publications/<YYYY-MM-DD>.json
    """

    def __init__(self, state_path: Path) -> None:
        self.state_path = Path(state_path)
        self.pub_dir = self.state_path / "publications"

    def _path_for(self, run_date_msk) -> Path:
        return self.pub_dir / f"{run_date_msk.isoformat()}.json"

    def get(self, run_date_msk) -> Tuple[Optional[PublicationRecord], List[AppError]]:
        errors: List[AppError] = []
        try:
            path = self._path_for(run_date_msk)
            if not path.exists():
                return None, []

            data = json.loads(path.read_text(encoding="utf-8"))

            published = bool(data.get("published", False))
            tg_message_id = data.get("tg_message_id")
            published_at_utc_raw = data.get("published_at_utc")

            published_at_utc = None
            if published_at_utc_raw:
                try:
                    published_at_utc = datetime.fromisoformat(published_at_utc_raw)
                except Exception:
                    errors.append(
                        AppError(
                            code="STATE_INVALID_DATETIME",
                            source=SourceSystem.STORAGE,
                            severity=Severity.WARNING,
                            message="Не удалось распарсить published_at_utc в state-файле.",
                            details={"path": str(path)},
                        )
                    )

            rec = PublicationRecord(
                run_date_msk=run_date_msk,
                published=published,
                tg_message_id=tg_message_id,
                published_at_utc=published_at_utc,
            )
            return rec, errors

        except Exception as e:
            errors.append(
                AppError(
                    code="STATE_READ_ERROR",
                    source=SourceSystem.STORAGE,
                    severity=Severity.WARNING,
                    message="Не удалось прочитать state-файл идемпотентности.",
                    details={"error": repr(e), "run_date_msk": run_date_msk.isoformat()},
                )
            )
            return None, errors

    def set_published(self, record: PublicationRecord) -> List[AppError]:
        errors: List[AppError] = []
        try:
            self.pub_dir.mkdir(parents=True, exist_ok=True)
            path = self._path_for(record.run_date_msk)
            tmp = path.with_suffix(".json.tmp")

            payload = asdict(record)
            payload["run_date_msk"] = record.run_date_msk.isoformat()
            payload["published_at_utc"] = (
                record.published_at_utc.isoformat() if record.published_at_utc else None
            )

            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)

            return []

        except Exception as e:
            errors.append(
                AppError(
                    code="STATE_WRITE_ERROR",
                    source=SourceSystem.STORAGE,
                    severity=Severity.WARNING,
                    message="Не удалось записать state-файл идемпотентности.",
                    details={"error": repr(e), "run_date_msk": record.run_date_msk.isoformat()},
                )
            )
            return errors
