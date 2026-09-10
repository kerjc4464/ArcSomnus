"""工具注册表：{name, description, schema, handler}，和 MCP tool 定义一一对应。
以后套 MCP 壳只需要包这一层，LLM 侧零改动。"""
import time

import store

def _searxng_url():
    import os
    return (store.config_get("searxng_url", "")
            or os.environ.get("SOMNUS_SEARXNG", "")
            or "http://127.0.0.1:9004").rstrip("/")


def _ok(data):
    return {"ok": True, "data": data}


def _err(msg):
    return {"ok": False, "error": msg}


def search(query, run_id=""):
    import requests
    try:
        r = requests.get(_searxng_url() + "/search",
                         params={"q": query, "format": "json"},
                         timeout=30)
        r.raise_for_status()
        out = []
        for it in r.json().get("results", [])[:8]:
            out.append({"title": it.get("title", ""),
                        "url": it.get("url", ""),
                        "snippet": it.get("content", "")[:500]})
        return _ok(out)
    except Exception as e:
        return _err("search failed: %s" % e)


def fetch(url, run_id=""):
    import requests
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "ArcSomnus/1.0"})
        r.raise_for_status()
        text = r.text
        # 粗洗：去标签，截 2000 字（第二刀换 trafilatura）
        import re
        text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()[:2000]
        return _ok({"url": url, "markdown": text})
    except Exception as e:
        return _err("fetch failed: %s" % e)


def read_context(soul_id="", limit=20, run_id=""):
    conn = store.db()
    try:
        rows = conn.execute(
            "SELECT messages FROM context_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if not rows:
            return _ok({"messages": []})
        import json
        msgs = json.loads(rows[0] or "[]")
        return _ok({"messages": msgs[-int(limit or 20):]})
    except Exception as e:
        return _err("read_context failed: %s" % e)
    finally:
        conn.close()


def save_asset(soul_id="", name="", description="", ref_url="", occasions="", run_id=""):
    conn = store.db()
    try:
        conn.execute(
            "INSERT INTO wardrobe (soul_id, name, description, ref_url, occasions, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (soul_id, name, description, ref_url, occasions, store.now()))
        conn.commit()
        return _ok({"saved": name})
    except Exception as e:
        return _err("save_asset failed: %s" % e)
    finally:
        conn.close()


def send_letter(soul_id="", subject="", fact="", thought="", source="", run_id=""):
    """两阶段：只写库标 pending，loop 正常结束才真发（第二刀接 SMTP）。"""
    content = "【事实】%s\n【想法】%s\n【来源】%s" % (fact, thought, source)
    conn = store.db()
    try:
        cur = conn.execute(
            "INSERT INTO letters (soul_id, subject, content, status, run_id, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (soul_id, subject, content, "pending", run_id, store.now()))
        conn.commit()
        return _ok({"letter_id": cur.lastrowid, "status": "pending"})
    except Exception as e:
        return _err("send_letter failed: %s" % e)
    finally:
        conn.close()


TOOLS = [
    {"name": "search", "description": "联网搜索，返回标题+链接+摘要",
     "schema": {"query": "搜索关键词"}, "handler": search},
    {"name": "fetch", "description": "抓网页，洗成纯文本（截2000字）",
     "schema": {"url": "网页地址"}, "handler": fetch},
    {"name": "read_context", "description": "读存档的最近聊天快照",
     "schema": {"soul_id": "角色名", "limit": "条数"}, "handler": read_context},
    {"name": "save_asset", "description": "存一件衣服/描述进衣柜",
     "schema": {"soul_id": "", "name": "", "description": "", "ref_url": "", "occasions": ""},
     "handler": save_asset},
    {"name": "send_letter", "description": "写一封信（先存待发送，循环结束才真发）",
     "schema": {"soul_id": "", "subject": "", "fact": "事实", "thought": "想法", "source": "来源"},
     "handler": send_letter},
]


def tools_prompt():
    lines = []
    for t in TOOLS:
        lines.append("- %s(%s)：%s" % (t["name"], ", ".join("%s" % k for k in t["schema"]), t["description"]))
    return "\n".join(lines)


def call_tool(name, args, run_id=""):
    for t in TOOLS:
        if t["name"] == name:
            args = dict(args or {})
            args["run_id"] = run_id
            try:
                return t["handler"](**{k: v for k, v in args.items()
                                       if k in t["handler"].__code__.co_varnames})
            except Exception as e:
                return _err("%s crashed: %s" % (name, e))
    return _err("unknown tool: %s" % name)
