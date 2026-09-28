from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import TeamColor

DEFAULT_CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "teamcolors.json"

# 같은 이름이 여러 분류에 있을 때 우선순위 (예: "잉글랜드" 클럽 목록 항목 vs 국가 항목)
_CATEGORY_PRIORITY = {"club": 0, "nationality": 1, "special": 2}


def _normalize(name: str) -> str:
    return "".join(name.split()).casefold()


class TeamColorCatalog:
    def __init__(self, entries: list[TeamColor]):
        self.entries = list(entries)
        self._by_name: dict[str, list[TeamColor]] = {}
        for tc in self.entries:
            self._by_name.setdefault(_normalize(tc.name), []).append(tc)

    @classmethod
    def load(cls, path: Path = DEFAULT_CATALOG_PATH) -> TeamColorCatalog:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([TeamColor(**item) for item in raw])

    def save(self, path: Path = DEFAULT_CATALOG_PATH) -> None:
        data = [asdict(tc) for tc in sorted(self.entries, key=lambda t: (t.category, t.id, t.name))]
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    def find(self, name: str) -> list[TeamColor]:
        return sorted(self._by_name.get(_normalize(name), []), key=lambda t: _CATEGORY_PRIORITY.get(t.category, 9))

    def resolve(self, name_or_id: str | int) -> TeamColor | None:
        """Name (spacing/case-insensitive) or numeric id → entry. Clubs win over same-named nations."""
        if isinstance(name_or_id, int) or str(name_or_id).isdigit():
            tc_id = int(name_or_id)
            matches = [tc for tc in self.entries if tc.id == tc_id]
            return min(matches, key=lambda t: _CATEGORY_PRIORITY.get(t.category, 9)) if matches else None
        found = self.find(str(name_or_id))
        return found[0] if found else None
