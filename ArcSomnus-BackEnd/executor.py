"""通用 agentic loop（第二刀离线用，第一刀先放着）。
JSON模式：模型输出 {"tool": name, "args": {...}} 或 {"finish": "..."}。"""
import json
import uuid

import llm
import store
import tools

MAX_STEPS = 20


def run(soul_id, soul, task, budget_tokens=100000, timeout_s=600):
    import time
    run_id = uuid.uuid4().hex[:12]
    conn = store.db()
    try:
        conn.execute("INSERT INTO runs (run_id, soul_id, trigger, status, started_at)"
                     " VALUES (?,?,?, 'running', ?)", (run_id, soul_id, task[:200], store.now()))
        conn.commit()
    finally:
        conn.close()
    letters = 0
    history = [{"role": "system", "content": "你是%s。\n可用工具：\n%s\n每次只输出一个JSON：{\"tool\":名,\"args\":{...}} 或 {\"finish\": \"收工小结\"}。" % (soul, tools.tools_prompt())},
               {"role": "user", "content": task}]
    deadline = time.time() + timeout_s
    for _ in range(MAX_STEPS):
        if time.time() > deadline:
            break
        try:
            text = llm.chat(history, scene="agent", temperature=0.7, max_tokens=4096,
                            json_mode=True, timeout=120)
        except Exception as e:
            _log(run_id, "llm", {}, "error: %s" % e)
            break
        d = llm.parse_json_loose(text)
        if "finish" in d:
            _done(run_id, "done")
            return {"run_id": run_id, "status": "done", "result": d.get("finish")}
        name, args = d.get("tool"), d.get("args", {})
        if not name:
            _done(run_id, "done")
            return {"run_id": run_id, "status": "done", "result": text[:500]}
        if name == "send_letter":
            letters += 1
            if letters > 2:
                history.append({"role": "user", "content": "发信额度用完（每轮最多2封），干别的。"})
                continue
        res = tools.call_tool(name, args, run_id)
        _log(run_id, name, args, json.dumps(res, ensure_ascii=False)[:2000])
        history.append({"role": "assistant", "content": json.dumps(d, ensure_ascii=False)})
        history.append({"role": "user", "content": "工具返回：%s。继续。" % json.dumps(res, ensure_ascii=False)[:3000]})
    _done(run_id, "budget")
    return {"run_id": run_id, "status": "budget"}


def _log(run_id, tool, args, result):
    conn = store.db()
    try:
        conn.execute("INSERT INTO tool_calls (run_id, tool, args, result, created_at)"
                     " VALUES (?,?,?,?,?)",
                     (run_id, tool, json.dumps(args, ensure_ascii=False)[:2000], result, store.now()))
        conn.commit()
    finally:
        conn.close()


def _done(run_id, status):
    conn = store.db()
    try:
        conn.execute("UPDATE runs SET status=? WHERE run_id=?", (status, run_id))
        conn.commit()
    finally:
        conn.close()
