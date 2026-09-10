import json
import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

import store
import llm
import dresser
import events
import scheduler
import sweep

logging.getLogger("uvicorn.access").addFilter(
    type("F", (logging.Filter,), {"filter": lambda s, r: "/api/heartbeat" not in r.getMessage()})())

app = FastAPI(title="ArcSomnus-BackEnd")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

BASE = os.path.dirname(os.path.abspath(__file__))
SOULS_DIR = store.SOULS_DIR
os.makedirs(SOULS_DIR, exist_ok=True)
store.init_db()
scheduler.start()


def load_soul(soul_id):
    return store.load_soul(soul_id)


@app.get("/")
def index():
    return {"status": "ArcSomnus Backend is running", "port": 9003}


@app.post("/api/heartbeat")
async def heartbeat():
    return {"status": "ok"}


@app.get("/api/config")
async def get_config():
    keys = ["api_url", "api_key", "model", "temperature", "max_tokens", "reasoning",
            "llm_dresser_api_url", "llm_dresser_api_key", "llm_dresser_model",
            "llm_dresser_temperature", "llm_dresser_max_tokens", "llm_dresser_reasoning",
            "llm_extract_api_url", "llm_extract_api_key", "llm_extract_model",
            "llm_extract_temperature", "llm_extract_max_tokens", "llm_extract_reasoning",
            "llm_agent_api_url", "llm_agent_api_key", "llm_agent_model",
            "llm_agent_temperature", "llm_agent_max_tokens", "llm_agent_reasoning",
            "extract_context_n", "agent_context_n",
            "memory_enabled", "memory_letters_n", "memory_diary_n", "memory_max_chars",
            "tick_minutes", "searxng_url",
            "email_method", "target_email", "smtp_server", "smtp_port", "smtp_user",
            "resend_key", "resend_from",
            "dresser_enabled", "dresser_always", "dresser_rounds", "dresser_context_n"]
    return {k: store.config_get(k) for k in keys}


class ConfigIn(BaseModel):
    key: str
    value: str = ""


@app.post("/api/config")
async def set_config(body: ConfigIn):
    store.config_set(body.key, body.value)
    return {"status": "ok"}


class TestIn(BaseModel):
    scene: str = "agent"


@app.post("/api/test_llm")
async def test_llm(body: TestIn = None):
    scene = ((body.scene if body else "") or "agent").strip() or "agent"
    if scene == "global":
        scene = "agent"
    if scene not in ("dresser", "extract", "agent"):
        scene = "agent"
    try:
        text = llm.chat([{"role": "user", "content": "回一个字：好"}],
                        scene=scene, temperature=0, max_tokens=16, timeout=60)
        base = (store.config_get("llm_%s_api_url" % scene)
                or store.config_get("api_url"))
        model = (store.config_get("llm_%s_model" % scene)
                 or store.config_get("model"))
        return {"status": "ok", "reply": text[:200],
                "session": llm.SESSION_ID[:8],
                "scene": scene, "base": base, "model": model}
    except Exception as e:
        return {"status": "error", "error": str(e)[:500], "scene": scene}


class DecideIn(BaseModel):
    soul_id: str
    scene: str = ""
    context: str = ""
    last: str = ""


@app.post("/api/dresser/decide")
async def decide(body: DecideIn):
    soul = load_soul(body.soul_id)
    if not soul:
        return {"status": "error", "error": "soul not found: %s" % body.soul_id}
    ward = dresser.get_wardrobe(body.soul_id)
    if any(w["locked"] and w["is_current"] for w in ward):
        cur = dresser.current_outfit(body.soul_id) or {}
        return {"status": "ok", "locked": True,
                "decision": {"change": False, "outfit_id": None,
                             "wearing_notes": "", "reason": "已锁定"},
                "suggestion": "", "current": cur}
    try:
        d = dresser.decide(body.soul_id, soul, body.scene,
                           (dresser.current_outfit(body.soul_id) or {}).get("description", ""),
                           ward, body.context, body.last)
    except Exception as e:
        return {"status": "error", "error": str(e)[:500]}
    return {"status": "ok", "locked": False, "decision": d,
            "suggestion": dresser.suggestion_text(d, ward),
            "current": dresser.current_outfit(body.soul_id)}


@app.get("/api/wardrobe")
async def wardrobe_list(soul_id: str = ""):
    return {"status": "ok", "items": dresser.get_wardrobe(soul_id)}


class WardrobeIn(BaseModel):
    soul_id: str
    name: str = ""
    description: str = ""
    ref_url: str = ""
    occasions: str = ""


@app.post("/api/wardrobe")
async def wardrobe_add(body: WardrobeIn):
    conn = store.db()
    try:
        cur = conn.execute(
            "INSERT INTO wardrobe (soul_id, name, description, ref_url, occasions, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (body.soul_id, body.name, body.description, body.ref_url,
             body.occasions, store.now()))
        conn.commit()
        return {"status": "ok", "id": cur.lastrowid}
    finally:
        conn.close()


class WardrobePatch(BaseModel):
    is_current: Optional[bool] = None
    locked: Optional[bool] = None


@app.patch("/api/wardrobe/{wid}")
async def wardrobe_patch(wid: int, body: WardrobePatch):
    conn = store.db()
    try:
        row = conn.execute("SELECT soul_id FROM wardrobe WHERE id=?", (wid,)).fetchone()
        if not row:
            return {"status": "error", "error": "not found"}
        soul = row[0]
        if body.is_current:
            prev = conn.execute(
                "SELECT id FROM wardrobe WHERE soul_id=? AND is_current=1", (soul,)).fetchone()
            if prev:
                store.config_set("wardrobe_prev_" + soul, str(prev[0]))
            conn.execute("UPDATE wardrobe SET is_current=0 WHERE soul_id=?", (soul,))
            conn.execute("UPDATE wardrobe SET is_current=1 WHERE id=?", (wid,))
        if body.locked is not None:
            conn.execute("UPDATE wardrobe SET locked=? WHERE id=?",
                         (1 if body.locked else 0, wid))
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


@app.post("/api/wardrobe/revert")
async def wardrobe_revert(body: dict):
    soul = (body or {}).get("soul_id", "")
    prev = store.config_get("wardrobe_prev_" + soul)
    if not prev:
        return {"status": "error", "error": "no previous outfit"}
    conn = store.db()
    try:
        conn.execute("UPDATE wardrobe SET is_current=0 WHERE soul_id=?", (soul,))
        conn.execute("UPDATE wardrobe SET is_current=1 WHERE id=?", (int(prev),))
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


class SnapshotIn(BaseModel):
    soul_id: str = ""
    chat_file: str = ""
    messages: List[str] = []


@app.post("/api/context/snapshot")
async def snapshot(body: SnapshotIn):
    store.snapshot_upsert(body.chat_file, body.messages)
    return {"status": "ok"}


@app.get("/api/context/snapshot")
async def snapshot_preview(chat_file: str = ""):
    """测试用：看快照里到底存了几条、最新几条是什么。"""
    msgs = store.snapshot_latest(chat_file)
    return {"status": "ok", "count": len(msgs),
            "preview": (msgs or [])[-5:], "chat_file": chat_file}


@app.get("/api/souls")
async def souls():
    return {"status": "ok", "souls": _soul_files()}


def _soul_files():
    try:
        return sorted(f for f in os.listdir(SOULS_DIR)
                      if os.path.isfile(os.path.join(SOULS_DIR, f)))
    except Exception:
        return []


class ExtractIn(BaseModel):
    chat_file: str = ""
    soul_ids: List[str] = []
    context: List[str] = []


@app.post("/api/events/extract")
async def events_extract(body: ExtractIn):
    try:
        n = events.extract(body.chat_file, body.soul_ids, body.context)
        return {"status": "ok", "added": n}
    except Exception as e:
        return {"status": "error", "error": str(e)[:300]}


@app.get("/api/events")
async def events_list(soul_id: str = "", status: str = "todo"):
    conn = store.db()
    try:
        q = "SELECT event_id, soul_id, summary, status, created_at FROM events WHERE 1=1"
        args: list = []
        if soul_id:
            q += " AND soul_id=?"
            args.append(soul_id)
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY event_id DESC LIMIT 50"
        rows = conn.execute(q, args).fetchall()
        return {"status": "ok", "items": [
            {"event_id": r[0], "soul_id": r[1], "summary": r[2],
             "status": r[3], "created_at": r[4]} for r in rows]}
    finally:
        conn.close()


@app.post("/api/sweep/now")
async def sweep_now():
    """手动触发一次扫表（验证用，不用等 30 分钟）。"""
    try:
        return {"status": "ok", "results": sweep.sweep_once()}
    except Exception as e:
        return {"status": "error", "error": str(e)[:500]}


@app.get("/api/tick")
async def tick_cfg():
    return {"status": "ok", "minutes": store.config_get("tick_minutes", "30")}


@app.get("/api/letters")
async def letters(soul_id: str = ""):
    conn = store.db()
    try:
        q = "SELECT id, soul_id, subject, content, status, created_at FROM letters"
        args: list = []
        if soul_id:
            q += " WHERE soul_id=?"
            args.append(soul_id)
        q += " ORDER BY id DESC LIMIT 50"
        rows = conn.execute(q, args).fetchall()
        return {"status": "ok", "items": [
            {"id": r[0], "soul_id": r[1], "subject": r[2], "content": r[3],
             "status": r[4], "created_at": r[5]} for r in rows]}
    finally:
        conn.close()


@app.get("/api/memory")
async def memory(soul_id: str = ""):
    """记忆回注：最近 N 封信 + N 篇日记拼成一块 prompt 文本，前端 per-soul 注入。"""
    if (store.config_get("memory_enabled", "1") or "1") in ("0", "false", "off", "no"):
        return {"status": "ok", "text": "", "letters": [], "diary": []}
    try:
        ln = max(0, min(int(store.config_get("memory_letters_n", "3") or 3), 10))
    except Exception:
        ln = 3
    try:
        dn = max(0, min(int(store.config_get("memory_diary_n", "3") or 3), 10))
    except Exception:
        dn = 3
    try:
        cap = max(300, min(int(store.config_get("memory_max_chars", "1500") or 1500), 6000))
    except Exception:
        cap = 1500
    conn = store.db()
    try:
        letters, diary = [], []
        if soul_id and ln:
            rows = conn.execute(
                "SELECT subject, content FROM letters WHERE soul_id=? AND status IN ('sent','pending')"
                " ORDER BY id DESC LIMIT ?", (soul_id, ln)).fetchall()
            letters = [{"subject": r[0], "content": (r[1] or "")[:500]} for r in rows]
        if soul_id and dn:
            rows = conn.execute(
                "SELECT content FROM diary WHERE soul_id=? ORDER BY id DESC LIMIT ?",
                (soul_id, dn)).fetchall()
            diary = [{"content": (r[0] or "")[:500]} for r in rows]
    finally:
        conn.close()
    parts = []
    for l in reversed(letters):
        parts.append("[信]%s：%s" % (l["subject"] or "无题", l["content"]))
    for d in reversed(diary):
        parts.append("[记]%s" % d["content"])
    text = ""
    if parts:
        text = "【离线记忆·%s】这是你离线时自己写下的，记得它们：\n%s" % (soul_id, "\n".join(parts))
        text = text[:cap]
    return {"status": "ok", "text": text, "letters": letters, "diary": diary}


@app.get("/api/diary")
async def diary(soul_id: str = ""):
    conn = store.db()
    try:
        q = "SELECT id, soul_id, content, created_at FROM diary"
        args: list = []
        if soul_id:
            q += " WHERE soul_id=?"
            args.append(soul_id)
        q += " ORDER BY id DESC LIMIT 50"
        rows = conn.execute(q, args).fetchall()
        return {"status": "ok", "items": [
            {"id": r[0], "soul_id": r[1], "content": r[2], "created_at": r[3]} for r in rows]}
    finally:
        conn.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9003)
