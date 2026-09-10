"""事件提取器：EXtreme eventExtract.js 的后端版，但输出只剩一句话摘要。
前端每条消息调一次，机械预过滤先杀垃圾，过了才花 LLM。"""
import json
import re

import llm
import store

GREET = re.compile(r"^(早安?|晚安?|你好|您好|在[吗嘛]|嗯+|哦+|啊+|哈+|嘿+|hi|hello|hey|ok|好的|谢谢|晚[安好])?[！!。…～~、,\s]*$", re.I)


def mechanical_skip(text):
    t = re.sub(r"\s+", "", text or "")
    if len(t) <= 5:
        return True
    if GREET.match(t):
        return True
    return False


SYS = """你是事件记录员。从【本轮对话】给每个角色提炼一句话事件（发生了什么/想要什么/没聊完什么）。
只输出JSON：{{"events": [{{"soul": "角色名", "summary": "一句话"}}]}}。
水话（寒暄/复读）就不要给那个人写事件。角色列表：{souls}"""


def _ctx_n():
    try:
        n = int(store.config_get("extract_context_n", "12") or 12)
    except Exception:
        n = 12
    return max(1, min(n, 100))


def extract(chat_file, soul_ids, context_lines):
    """返回实际写入的条数。"""
    if not soul_ids or not context_lines:
        return 0
    last = context_lines[-1] if context_lines else ""
    if mechanical_skip(last.split(":", 1)[-1]):
        return 0
    try:
        text = llm.chat(
            [{"role": "system", "content": SYS.format(souls="、".join(soul_ids))},
             {"role": "user", "content": "\n".join((context_lines or [])[-_ctx_n():])}],
            scene="extract", temperature=0.3, max_tokens=512, json_mode=True, timeout=90)
    except Exception:
        return 0
    d = llm.parse_json_loose(text)
    n = 0
    for ev in d.get("events", []) or []:
        soul = str(ev.get("soul", "")).strip()
        summary = str(ev.get("summary", "")).strip()
        if soul in soul_ids and summary:
            store.event_add(soul, chat_file, summary)
            n += 1
    return n
