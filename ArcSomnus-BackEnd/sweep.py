"""离线扫表：每 tick 一次，有待办的 soul 各跑一次 executor，产出写日记。
信两阶段：loop 里只写 pending，全部跑完才统一真发。"""
import logging

import executor
import mail
import store

log = logging.getLogger("ArcSomnus.sweep")

EMAIL_KEYS = ["email_method", "smtp_server", "smtp_port", "smtp_user", "smtp_pass",
              "resend_key", "resend_from", "target_email"]


def _agent_n():
    try:
        n = int(store.config_get("agent_context_n", "15") or 15)
    except Exception:
        n = 15
    return max(1, min(n, 100))


def sweep_once():
    results = []
    for soul_id in store.soul_ids():
        todos = store.todo_events(soul_id)
        if not todos:
            continue
        soul = store.load_soul(soul_id)
        if not soul:
            continue
        lines = store.snapshot_latest(todos[0].get("chat_file", ""))[-_agent_n():]
        task = ("你的待办事件：\n%s\n\n最近聊天：\n%s\n\n"
                "决定做哪件（可以全不做，直接finish）。能用工具就用工具，"
                "值得记住的写进finish小结。" % (
                    "\n".join("- " + t["summary"] for t in todos),
                    "\n".join(lines[-15:]) if lines else "（无快照）"))
        try:
            res = executor.run(soul_id, soul, task)
        except Exception as e:
            log.warning("[sweep] %s failed: %s", soul_id, e)
            continue
        store.events_mark(res["run_id"], [t["event_id"] for t in todos])
        store.diary_add(soul_id, "run %s（%s）：%s" % (
            res["run_id"], res["status"], res.get("result", "") or "无小结"), res["run_id"])
        results.append({"soul": soul_id, "run": res["run_id"], "status": res["status"]})
    cfg = {k: store.config_get(k) for k in EMAIL_KEYS}
    if cfg.get("target_email"):
        try:
            sent, failed = mail.flush_pending(cfg)
            results.append({"flush": {"sent": sent, "failed": failed}})
        except Exception as e:
            log.warning("[sweep] flush failed: %s", e)
    return results
