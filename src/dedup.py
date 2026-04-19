"""
跨任务去重：history_set 持久化

基于本地 JSON 文件存储已成功录入飞书的 arXiv ID 集合。
"""
import json
from pathlib import Path
from threading import Lock
from typing import Set


class HistoryStore:
    """论文历史去重存储（线程安全）"""

    def __init__(self, path: str = "data/history.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._set: Set[str] = self._load()

    def _load(self) -> Set[str]:
        if self.path.exists():
            try:
                return set(json.loads(self.path.read_text(encoding='utf-8')))
            except Exception:
                return set()
        return set()

    def _save(self) -> None:
        self.path.write_text(
            json.dumps(sorted(self._set), ensure_ascii=False, indent=2),
            encoding='utf-8'
        )

    def contains(self, arxiv_id: str) -> bool:
        return arxiv_id in self._set

    def add(self, arxiv_id: str) -> None:
        with self._lock:
            self._set.add(arxiv_id)
            self._save()

    def __len__(self) -> int:
        return len(self._set)
