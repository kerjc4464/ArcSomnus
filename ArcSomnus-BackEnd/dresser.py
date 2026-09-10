"""Dresser：每角色的专属着装顾问。输入全量（完整Soul+全衣柜），输出JSON决策。"""
import json

import llm
import store

SYS_TMPL = """你是【{soul_id}】的专属着装顾问，只管一件事：她现在该穿什么。
<soul>
{soul}
</soul>
原则（推荐，不是规则）：
- 按场合、天气、她的性格和审美挑；衣柜里没有合适的就说"保持现状"
- 允许选择"不穿"，这是一个合法选项，不扣分
- 只输出JSON，不许抒情：
{{"change": true/false, "outfit_id": "衣柜id/保持现状填null/不穿填none",
"wearing_notes": "怎么穿", "reason": "一句话理由"}}"""


def decide(soul_id, soul, scene, current, wardrobe, context, last=None):
    items = []
    for w in wardrobe:
        items.append("- %(id)s %(name)s：%(desc)s%(occ)s" % {
            "id": w["id"], "name": w["name"], "desc": w["description"] or "",
            "occ": ("，适合" + w["occasions"]) if w.get("occasions") else ""})
    user = ("【当前】%s\n【当前穿着】%s\n【衣柜清单】\n%s\n【近N条聊天】\n%s\n【上次决策】%s" % (
        scene or "（未知）", current or "（未知）",
        "\n".join(items) if items else "（衣柜是空的）",
        context or "（无）", last or "（无）"))
    text = llm.chat(
        [{"role": "system", "content": SYS_TMPL.format(soul_id=soul_id, soul=soul)},
         {"role": "user", "content": user}],
        scene="dresser", temperature=0.7, max_tokens=512, json_mode=True)
    d = llm.parse_json_loose(text)
    return {
        "change": bool(d.get("change", False)),
        "outfit_id": d.get("outfit_id"),
        "wearing_notes": str(d.get("wearing_notes", "")),
        "reason": str(d.get("reason", "")),
    }


def suggestion_text(decision, wardrobe):
    if not decision.get("change") or not decision.get("outfit_id"):
        return ""
    name, desc = "", ""
    for w in wardrobe:
        if str(w["id"]) == str(decision["outfit_id"]):
            name, desc = w["name"], w["description"] or ""
            break
    if not name:
        return ""
    notes = decision.get("wearing_notes") or ""
    return ("【穿衣建议】Dresser建议换上「%s」，理由：%s\n%s\n穿法：%s" % (
        name, decision.get("reason") or "", desc, notes)).strip()


def get_wardrobe(soul_id):
    conn = store.db()
    try:
        rows = conn.execute(
            "SELECT id, name, description, ref_url, occasions, is_current, locked"
            " FROM wardrobe WHERE soul_id=? ORDER BY id", (soul_id,)).fetchall()
        return [{"id": r[0], "name": r[1], "description": r[2], "ref_url": r[3],
                 "occasions": r[4], "is_current": bool(r[5]), "locked": bool(r[6])}
                for r in rows]
    finally:
        conn.close()


def current_outfit(soul_id):
    for w in get_wardrobe(soul_id):
        if w["is_current"]:
            return w
    return None
