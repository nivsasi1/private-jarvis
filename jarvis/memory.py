"""Personal long-term memory — SQLite store with semantic recall.

Facts are embedded with Ollama's nomic-embed-text for meaning-based search;
if embeddings aren't available it falls back to keyword matching, so memory
always works. The brain auto-recalls relevant facts each turn and can also
call remember()/recall() explicitly as tools.
"""

import datetime
import sqlite3
import threading
from pathlib import Path

import numpy as np
import requests

DB_PATH = Path(__file__).resolve().parent.parent / "jarvis_memory.db"


class Memory:
    def __init__(self, cfg):
        self.url = cfg.llm.url
        self.embed_model = getattr(cfg, "embed_model", "nomic-embed-text")
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS mem("
            "id INTEGER PRIMARY KEY, text TEXT, emb BLOB, created TEXT)")
        self.db.commit()
        self.lock = threading.Lock()

    def _embed(self, text: str):
        r = requests.post(f"{self.url}/api/embeddings",
                          json={"model": self.embed_model, "prompt": text}, timeout=15)
        r.raise_for_status()
        return np.asarray(r.json()["embedding"], dtype=np.float32)

    def remember(self, text: str) -> str:
        text = text.strip()
        if not text:
            return "אין מה לזכור"
        emb = None
        try:
            emb = self._embed(text).tobytes()
        except Exception:
            pass  # keyword-only fallback
        with self.lock:
            self.db.execute("INSERT INTO mem(text, emb, created) VALUES (?,?,?)",
                            (text, emb, datetime.datetime.now().isoformat()))
            self.db.commit()
        return "שמרתי בזיכרון"

    def recall(self, query: str, k: int = 4):
        with self.lock:
            rows = self.db.execute("SELECT text, emb FROM mem").fetchall()
        if not rows:
            return []
        try:
            q = self._embed(query)
            qn = q / (np.linalg.norm(q) + 1e-9)
            scored = []
            for text, emb in rows:
                if emb:
                    v = np.frombuffer(emb, dtype=np.float32)
                    scored.append((float(qn @ (v / (np.linalg.norm(v) + 1e-9))), text))
            if scored:
                scored.sort(reverse=True)
                return [t for s, t in scored[:k] if s > 0.35]
        except Exception:
            pass
        ql = [w for w in query.lower().split() if len(w) > 1]
        return [t for t, _ in rows if any(w in t.lower() for w in ql)][:k]

    def count(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM mem").fetchone()[0]

    def consolidate(self, threshold: float = 0.9) -> int:
        """Drop near-duplicate facts (cosine ≥ threshold), keeping the newest,
        so memory doesn't bloat with restatements of the same thing."""
        with self.lock:
            rows = self.db.execute(
                "SELECT id, emb FROM mem WHERE emb IS NOT NULL ORDER BY created DESC"
            ).fetchall()
        vecs = [(i, np.frombuffer(e, dtype=np.float32)) for i, e in rows]
        vecs = [(i, v / (np.linalg.norm(v) + 1e-9)) for i, v in vecs]
        remove = set()
        for a in range(len(vecs)):
            if vecs[a][0] in remove:
                continue
            for b in range(a + 1, len(vecs)):     # b is older (rows are newest-first)
                if vecs[b][0] in remove:
                    continue
                if float(vecs[a][1] @ vecs[b][1]) >= threshold:
                    remove.add(vecs[b][0])
        if remove:
            with self.lock:
                self.db.executemany("DELETE FROM mem WHERE id=?", [(r,) for r in remove])
                self.db.commit()
            print(f"[memory] consolidated — removed {len(remove)} duplicate facts")
        return len(remove)
