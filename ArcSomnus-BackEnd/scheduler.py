"""30min 心跳：扫 events 待办，有活就让对应 soul 跑一次。"""
import logging
import threading

import store

log = logging.getLogger("ArcSomnus.scheduler")
_stop = threading.Event()


def _loop():
    import sweep
    while True:
        try:
            mins = float(store.config_get("tick_minutes", "30") or 30)
        except Exception:
            mins = 30
        if _stop.wait(max(60, mins * 60)):
            break
        try:
            res = sweep.sweep_once()
            if res:
                log.info("[scheduler] sweep done: %s", res)
        except Exception as e:
            log.warning("[scheduler] sweep failed: %s", e)


def start():
    t = threading.Thread(target=_loop, daemon=True, name="somnus-scheduler")
    t.start()
    return t
