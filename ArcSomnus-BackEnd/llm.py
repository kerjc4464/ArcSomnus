"""Somnus 后端唯一上游出口：所有 LLM 请求经这里发。

默认渠道 OpenCode Go（OpenAI 兼容路由），强制要求：
- Authorization: Bearer <key>
- x-opencode-session: <本进程固定 uuid，重启才换，命中提示词缓存>
- 规范 User-Agent（不套用通用 UA）
"""
import os
import time
import uuid
import json
import sqlite3

import requests

import store

SESSION_ID = uuid.uuid4().hex
UA = "ArcSomnus/1.0 (+local-agent; contact: owner)"


def _cfg(key, default=""):
    v = store.config_get(key)
    return v if v not in (None, "") else os.environ.get("SOMNUS_" + key.upper(), default)


def _scene_cfg(scene, key, default=""):
    v = store.config_get("llm_%s_%s" % (scene, key))
    return v if v not in (None, "") else _cfg(key, default)


def _build_body(model, messages, temperature, max_tokens, reasoning, json_mode):
    """EXtreme buildBody 同款推理兼容：推理模型不发 temperature，max 走 max_completion_tokens。"""
    is_reasoning = str(reasoning or "none").lower() not in ("none", "0", "")
    body = {"model": model, "messages": messages}
    if not is_reasoning and temperature not in (None, ""):
        try:
            body["temperature"] = float(temperature)
        except Exception:
            pass
    try:
        mt = int(max_tokens)
        if mt > 0:
            body["max_completion_tokens" if is_reasoning else "max_tokens"] = mt
    except Exception:
        pass
    if is_reasoning:
        body["reasoning_effort"] = str(reasoning).lower()
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    return body


def chat(messages, scene="agent", temperature=None, max_tokens=None, model=None,
         reasoning=None, json_mode=False, timeout=120):
    """发一次 chat/completions，返回文本。mock 模式直接回写死的 JSON。
    scene: dresser / extract / agent —— 每个调用点独立配 model/temperature/max_tokens/reasoning。"""
    if os.environ.get("SOMNUS_LLM", "") == "mock":
        return _mock(messages)
    base = (_scene_cfg(scene, "api_url", "") or _cfg("api_url", "https://opencode.ai/zen/go/v1")).rstrip("/")
    body = _build_body(
        model or _scene_cfg(scene, "model", ""),
        messages,
        temperature if temperature is not None else _scene_cfg(scene, "temperature", "0.7"),
        max_tokens or _scene_cfg(scene, "max_tokens", "2048"),
        (reasoning if reasoning is not None
         else _scene_cfg(scene, "reasoning", "none")),
        json_mode)
    key = (_scene_cfg(scene, "api_key", "") or _cfg("api_key", ""))
    headers = {
        "Content-Type": "application/json",
        "User-Agent": UA,
        "x-opencode-session": SESSION_ID,
    }
    if key:
        headers["Authorization"] = "Bearer " + key
    r = requests.post(base + "/chat/completions", json=body, headers=headers, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]


def parse_json_loose(text):
    """EXtreme 同款思路：硬解析，各种包浆 JSON 都能扒出来。"""
    if not text:
        return {}
    s = text.strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    a, b = s.find("{"), s.rfind("}")
    if 0 <= a < b:
        try:
            return json.loads(s[a:b + 1])
        except Exception:
            return {}
    return {}


def _mock(messages):
    sys = messages[0].get("content", "") if messages else ""
    if "着装顾问" in sys:
        return json.dumps({
            "change": False, "outfit_id": None,
            "wearing_notes": "", "reason": "mock：保持现状",
        }, ensure_ascii=False)
    if "事件记录员" in sys:
        import re
        m = re.search(r"角色列表：(.+)", sys)
        first = (m.group(1).split("、")[0].strip() if m else "soul")
        return json.dumps({"events": [{"soul": first, "summary": "mock事件：祭典穿什么还没定"}]},
                          ensure_ascii=False)
    last = messages[-1]["content"] if messages else ""
    return json.dumps({"finish": "mock收工：" + last[:80]}, ensure_ascii=False)
