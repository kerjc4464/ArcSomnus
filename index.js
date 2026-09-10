// ArcSomnus - ST 前端：模块化面板 + Dresser 前置 hook
// 模块一 Dresser：收到消息后，每角色并发问后端拿穿衣建议，注入给下一次生成。
import { extension_settings, getContext } from "../../../extensions.js";
import { eventSource, event_types, setExtensionPrompt, extension_prompt_roles, extension_prompt_types, saveSettingsDebounced, getCurrentChatId } from "../../../../script.js";

const extensionName = "ArcSomnus";
const PROMPT_PREFIX = "ArcSomnus-Dresser-";
const MEMORY_PREFIX = "ArcSomnus-Memory-";

const defaultSettings = {
    enabled: true,            // 模块总开关（Dresser 是第一个模块）
    backendUrl: "",           // 空=自动按页面 host 配 :9003
    dresser_always: false,    // true=每轮都跑，false=门控
    dresser_rounds: 5,        // 门控：距上次决策超 N 轮才跑
    dresser_context_n: 12,    // Dresser 注入条数（≤快照数）
    snapshot_n: 40,           // 通用快照：每轮存近 N 条到后端（上限100）
    memory_enabled: true,     // 记忆回注总开关（日记+信件进上下文）
    lastDecideRound: {},      // soul -> chat length
};

function settings() {
    extension_settings[extensionName] = Object.assign({}, defaultSettings, extension_settings[extensionName] || {});
    return extension_settings[extensionName];
}

function backendBase() {
    const s = settings();
    let u = String(s.backendUrl || "").trim();
    if (!u) {
        const host = window.location.hostname;
        const h = (!host || host === "" || host === "127.0.0.1" || host === "localhost") ? "127.0.0.1" : host;
        return `http://${h}:9003`;
    }
    return u.replace(/\/+$/, "");
}

async function api(path, method = "GET", body = undefined) {
    const r = await fetch(backendBase() + path, {
        method,
        headers: { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
}

const TRIGGER_RE = /衣|穿|换|脱|裸|睡衣|裙|斗篷|制服|泳装|内衣/i;

function recentText(n) {
    try {
        const chat = getContext().chat || [];
        return chat.slice(-n).map(m => `${m.name || "?"}: ${(m.mes || "").replace(/<[^>]+>/g, "").slice(0, 300)}`);
    } catch { return []; }
}

async function maybeDresser() {
    const s = settings();
    if (!s.enabled) return;
    let souls = [];
    try {
        const d = await api("/api/souls");
        souls = (d.souls || []).map(f => f.replace(/\.[^.]+$/, ""));
    } catch (e) { console.warn("[ArcSomnus] souls 拉取失败", e); return; }
    if (!souls.length) return;

    // 通用快照与各场景注入解耦：快照存全量，各家按需截断
    const snapN = Math.max(10, Math.min(s.snapshot_n || 40, 100));
    const snapLines = recentText(snapN);
    if (!snapLines.length) return;
    const dresserN = Math.max(1, Math.min(s.dresser_context_n || 12, snapN));
    const dresserLines = snapLines.slice(-dresserN);
    const ctx = dresserLines.join("\n");
    const mentioned = TRIGGER_RE.test(ctx) || TRIGGER_RE.test(snapLines.join("\n"));

    await Promise.all(souls.map(async (soul) => {
        try {
            const lastAt = s.lastDecideRound[soul] ?? -1e9;
            const round = (getContext().chat || []).length;
            if (!s.dresser_always && !mentioned && (round - lastAt) < s.dresser_rounds) return;
            const d = await api("/api/dresser/decide", "POST", {
                soul_id: soul,
                scene: new Date().toLocaleString("zh-CN"),
                context: ctx,
            });
            s.lastDecideRound[soul] = round;
            saveSettingsDebounced();
            const key = PROMPT_PREFIX + soul;
            const val = (d.status === "ok" && d.suggestion) ? d.suggestion : "";
            setExtensionPrompt(key, val, extension_prompt_types.BEFORE_PROMPT, 0, false, extension_prompt_roles.SYSTEM);
        } catch (e) { console.warn("[ArcSomnus] dresser 失败", soul, e); }
    }));

    // 记忆回注：日记+信件拼好直接进上下文（per-soul，可开关）
    if (s.memory_enabled !== false) {
        await Promise.all(souls.map(async (soul) => {
            try {
                const m = await api("/api/memory?soul_id=" + encodeURIComponent(soul));
                setExtensionPrompt(MEMORY_PREFIX + soul, (m && m.text) || "",
                    extension_prompt_types.BEFORE_PROMPT, 0, false, extension_prompt_roles.SYSTEM);
            } catch {}
        }));
    }

    // 快照：存全量 snapLines（不再被 dresser_context_n 卡成 12 条）
    let chatFile = "";
    try { chatFile = getCurrentChatId() || ""; } catch {}
    try {
        await api("/api/context/snapshot", "POST", { soul_id: "", chat_file: chatFile, messages: snapLines });
    } catch {}
    // 事件提取：发全量，后端按 extract_context_n 自己截
    try {
        await api("/api/events/extract", "POST", { chat_file: chatFile, soul_ids: souls, context: snapLines });
    } catch (e) { console.warn("[ArcSomnus] extract 失败", e); }
}

// ---------- 面板 ----------
function esc(s) { return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }

async function renderWardrobe(soul, box) {
    box.innerHTML = "加载中…";
    try {
        const d = await api("/api/wardrobe?soul_id=" + encodeURIComponent(soul));
        const items = d.items || [];
        if (!items.length) { box.innerHTML = "衣柜是空的，先给她添几件。"; return; }
        box.innerHTML = items.map(w => `
            <div class="arcsomnus-cloth" data-id="${w.id}">
                <b>${esc(w.name)}</b>${w.is_current ? '<span class="arcsomnus-badge">穿着</span>' : ""}${w.locked ? '<span class="arcsomnus-badge lock">锁定</span>' : ""}
                <div class="arcsomnus-desc">${esc(w.description || "")}</div>
                <div class="arcsomnus-row">
                    ${w.is_current ? `<button class="menu_button" data-act="unlock" ${w.locked ? "" : "disabled"}>解锁</button>
                    <button class="menu_button" data-act="lock" ${w.locked ? "disabled" : ""}>锁死这套</button>`
                    : `<button class="menu_button" data-act="wear">换回这套</button>`}
                </div>
            </div>`).join("");
        box.querySelectorAll("button").forEach(btn => btn.addEventListener("click", async () => {
            const id = btn.closest(".arcsomnus-cloth").dataset.id;
            const act = btn.dataset.act;
            if (act === "wear") await api(`/api/wardrobe/${id}`, "PATCH", { is_current: true });
            if (act === "lock") await api(`/api/wardrobe/${id}`, "PATCH", { locked: true });
            if (act === "unlock") await api(`/api/wardrobe/${id}`, "PATCH", { locked: false });
            renderWardrobe(soul, box);
        }));
    } catch (e) { box.innerHTML = "后端连不上：" + esc(e.message); }
}

function buildPanel() {
    const s = settings();
    const html = `
    <div id="arcsomnus-panel">
        <div class="inline-drawer">
            <div class="inline-drawer-toggle inline-drawer-header">
                <b>ArcSomnus（离线中枢）</b>
                <div class="inline-drawer-icon fa-solid fa-circle-chevron-down down"></div>
            </div>
            <div class="inline-drawer-content">
                <label><input type="checkbox" id="arcsomnus-enabled" ${s.enabled ? "checked" : ""}> 启用 Dresser 模块</label>
                <div class="arcsomnus-row">
                    <span>后端</span>
                    <input id="arcsomnus-backend" class="text_pole" value="${esc(s.backendUrl)}" placeholder="空=自动 :9003">
                </div>
                <details class="arcsomnus-sec"><summary>模型渠道全局（存后端，opencode/go兼容）</summary>
                    <div class="arcsomnus-row"><span>apiUrl</span><input id="arcsomnus-apiurl" class="text_pole" placeholder="https://opencode.ai/zen/go/v1"></div>
                    <div class="arcsomnus-row"><span>apiKey</span><input id="arcsomnus-apikey" type="password" class="text_pole"></div>
                    <div class="arcsomnus-row"><span>model</span><input id="arcsomnus-model" class="text_pole"></div>
                    <div class="arcsomnus-row"><span>temperature</span><input id="arcsomnus-temp" class="text_pole" placeholder="0.7"></div>
                    <div class="arcsomnus-row"><span>max_tokens</span><input id="arcsomnus-maxtok" class="text_pole" placeholder="2048"></div>
                    <div class="arcsomnus-row"><span>reasoning</span><input id="arcsomnus-reason" class="text_pole" placeholder="none/low/medium/high"></div>
                    <div class="arcsomnus-row">
                        <button class="menu_button" id="arcsomnus-save-chan">保存渠道</button>
                        <select id="arcsomnus-test-scene">
                            <option value="all">三场景全测</option>
                            <option value="agent">agent</option>
                            <option value="dresser">dresser</option>
                            <option value="extract">extract</option>
                        </select>
                        <button class="menu_button" id="arcsomnus-test">测试连通</button>
                        <span id="arcsomnus-test-r"></span>
                    </div>
                    <div class="arcsomnus-desc" id="arcsomnus-test-detail"></div>
                </details>
                <details class="arcsomnus-sec"><summary>分场景覆盖（空=跟全局，各自独立调参）</summary>
                    <div class="arcsomnus-row"><span>dresser.model</span><input id="arcsomnus-m-dresser" class="text_pole" placeholder="空=全局"></div>
                    <div class="arcsomnus-row"><span>dresser.temp</span><input id="arcsomnus-t-dresser" class="text_pole" placeholder="空=全局"></div>
                    <div class="arcsomnus-row"><span>dresser.max</span><input id="arcsomnus-x-dresser" class="text_pole" placeholder="空=全局"></div>
                    <div class="arcsomnus-row"><span>dresser.reason</span><input id="arcsomnus-r-dresser" class="text_pole" placeholder="空=全局"></div>
                    <div class="arcsomnus-row"><span>extract.model</span><input id="arcsomnus-m-extract" class="text_pole" placeholder="空=全局"></div>
                    <div class="arcsomnus-row"><span>extract.temp</span><input id="arcsomnus-t-extract" class="text_pole" placeholder="空=全局"></div>
                    <div class="arcsomnus-row"><span>extract.max</span><input id="arcsomnus-x-extract" class="text_pole" placeholder="空=全局"></div>
                    <div class="arcsomnus-row"><span>extract.reason</span><input id="arcsomnus-r-extract" class="text_pole" placeholder="空=全局"></div>
                    <div class="arcsomnus-row"><span>agent.model</span><input id="arcsomnus-m-agent" class="text_pole" placeholder="空=全局"></div>
                    <div class="arcsomnus-row"><span>agent.temp</span><input id="arcsomnus-t-agent" class="text_pole" placeholder="空=全局"></div>
                    <div class="arcsomnus-row"><span>agent.max</span><input id="arcsomnus-x-agent" class="text_pole" placeholder="空=全局"></div>
                    <div class="arcsomnus-row"><span>agent.reason</span><input id="arcsomnus-r-agent" class="text_pole" placeholder="空=全局"></div>
                    <div class="arcsomnus-row"><button class="menu_button" id="arcsomnus-save-models">保存</button></div>
                </details>
                <details class="arcsomnus-sec"><summary>聊天快照（通用历史，所有LLM都从这取）</summary>
                    <div class="arcsomnus-row"><span>每轮存N条</span><input id="arcsomnus-snapn" type="number" min="10" max="100" class="text_pole" value="${s.snapshot_n || 40}"></div>
                    <div class="arcsomnus-desc">上限100，默认40。后端SQL存全量，dresser/extract/agent各自再截断，互不卡脖子。</div>
                    <div class="arcsomnus-row"><span>extract注入</span><input id="arcsomnus-extract-n" type="number" min="1" max="100" class="text_pole" placeholder="12"></div>
                    <div class="arcsomnus-row"><span>agent注入</span><input id="arcsomnus-agent-n" type="number" min="1" max="100" class="text_pole" placeholder="15"></div>
                    <div class="arcsomnus-row"><button class="menu_button" id="arcsomnus-save-snap">保存快照配置</button>
                        <button class="menu_button" id="arcsomnus-test-snap">检查快照</button>
                        <span id="arcsomnus-snap-r"></span></div>
                    <div class="arcsomnus-desc" id="arcsomnus-snap-detail"></div>
                </details>
                <details class="arcsomnus-sec"><summary>记忆回注（日记+信件进主会话）</summary>
                    <label><input type="checkbox" id="arcsomnus-mem-en" ${s.memory_enabled !== false ? "checked" : ""}> 启用回注</label>
                    <div class="arcsomnus-row"><span>信件N</span><input id="arcsomnus-mem-letters" type="number" min="0" max="10" class="text_pole" placeholder="3"></div>
                    <div class="arcsomnus-row"><span>日记N</span><input id="arcsomnus-mem-diary" type="number" min="0" max="10" class="text_pole" placeholder="3"></div>
                    <div class="arcsomnus-row"><span>总截断</span><input id="arcsomnus-mem-chars" type="number" min="300" max="6000" class="text_pole" placeholder="1500"></div>
                    <div class="arcsomnus-row"><button class="menu_button" id="arcsomnus-save-mem">保存回注配置</button>
                        <button class="menu_button" id="arcsomnus-test-mem">预览回注</button></div>
                    <div class="arcsomnus-desc" id="arcsomnus-mem-preview"></div>
                </details>
                <details class="arcsomnus-sec"><summary>发信（抄 ViGil，存后端）</summary>
                    <div class="arcsomnus-row"><span>通道</span><select id="arcsomnus-email-method">
                        <option value="smtp">smtp</option><option value="resend">resend</option></select></div>
                    <div class="arcsomnus-row"><span>收信邮箱</span><input id="arcsomnus-target" class="text_pole"></div>
                    <div class="arcsomnus-row"><span>smtp服</span><input id="arcsomnus-smtp-s" class="text_pole" placeholder="smtp.xxx.com"></div>
                    <div class="arcsomnus-row"><span>端口</span><input id="arcsomnus-smtp-p" class="text_pole" placeholder="465"></div>
                    <div class="arcsomnus-row"><span>账号</span><input id="arcsomnus-smtp-u" class="text_pole"></div>
                    <div class="arcsomnus-row"><span>密码</span><input id="arcsomnus-smtp-pass" type="password" class="text_pole" style="flex:1;min-width:120px"></div>
                    <div class="arcsomnus-row"><span>resendKey</span><input id="arcsomnus-resend-k" type="password" class="text_pole"></div>
                    <div class="arcsomnus-row"><button class="menu_button" id="arcsomnus-save-email">保存发信</button>
                    <span id="arcsomnus-email-r"></span></div>
                </details>
                <details class="arcsomnus-sec" open><summary>Dresser</summary>
                    <label><input type="checkbox" id="arcsomnus-always" ${s.dresser_always ? "checked" : ""}> 每次都跑（关=门控）</label>
                    <div class="arcsomnus-row"><span>门控轮数</span><input id="arcsomnus-rounds" type="number" class="text_pole" value="${s.dresser_rounds}"></div>
                    <div class="arcsomnus-row"><span>上下文条数</span><input id="arcsomnus-ctxn" type="number" class="text_pole" value="${s.dresser_context_n}"></div>
                </details>
                <details class="arcsomnus-sec" open><summary>衣柜</summary>
                    <div class="arcsomnus-row"><span>角色</span><select id="arcsomnus-soul"></select>
                    <button class="menu_button" id="arcsomnus-revert">换回上一套</button></div>
                    <div id="arcsomnus-wardrobe"></div>
                    <div class="arcsomnus-row"><span>添衣服</span>
                        <input id="arcsomnus-new-name" class="text_pole" placeholder="名字">
                        <input id="arcsomnus-new-desc" class="text_pole" placeholder="描述"></div>
                    <div class="arcsomnus-row"><button class="menu_button" id="arcsomnus-add">添进衣柜</button></div>
                </details>
                <details class="arcsomnus-sec" open><summary>日记</summary>
                    <div class="arcsomnus-row"><button class="menu_button" id="arcsomnus-diary-r">刷新</button></div>
                    <div id="arcsomnus-diary"></div>
                </details>
                <details class="arcsomnus-sec" open><summary>信件</summary>
                    <div class="arcsomnus-row"><button class="menu_button" id="arcsomnus-letters-r">刷新</button></div>
                    <div id="arcsomnus-letters"></div>
                </details>
                <details class="arcsomnus-sec" open><summary>离线唤醒（=后台30分钟定时做的事）</summary>
                    <div class="arcsomnus-row"><button class="menu_button" id="arcsomnus-sweep">立即唤醒一次</button>
                    <span id="arcsomnus-sweep-r"></span></div>
                    <div class="arcsomnus-desc">和后台定时调的是同一个函数，不用等30分钟。有待办的角色才跑LLM，没活直接跳过。</div>
                </details>
            </div>
        </div>
    </div>`;
    const host = document.querySelector("#extensions_settings");
    if (!host) return;
    host.insertAdjacentHTML("beforeend", html);

    const on = (id, ev, fn) => document.querySelector(id)?.addEventListener(ev, fn);
    on("#arcsomnus-enabled", "change", e => { settings().enabled = e.target.checked; saveSettingsDebounced(); });
    on("#arcsomnus-backend", "change", e => { settings().backendUrl = e.target.value.trim(); saveSettingsDebounced(); });
    on("#arcsomnus-always", "change", e => { settings().dresser_always = e.target.checked; saveSettingsDebounced(); });
    on("#arcsomnus-rounds", "change", e => { settings().dresser_rounds = Number(e.target.value) || 5; saveSettingsDebounced(); });
    on("#arcsomnus-ctxn", "change", e => { settings().dresser_context_n = Number(e.target.value) || 12; saveSettingsDebounced(); });
    on("#arcsomnus-save-chan", "click", async () => {
        const v = id => document.querySelector(id)?.value.trim() || "";
        for (const [k, val] of [["api_url", v("#arcsomnus-apiurl")], ["api_key", v("#arcsomnus-apikey")], ["model", v("#arcsomnus-model")],
                ["temperature", v("#arcsomnus-temp")], ["max_tokens", v("#arcsomnus-maxtok")], ["reasoning", v("#arcsomnus-reason")]]) {
            if (val) await api("/api/config", "POST", { key: k, value: val }).catch(() => {});
        }
        document.querySelector("#arcsomnus-test-r").textContent = "已保存";
    });
    on("#arcsomnus-test", "click", async () => {
        const el = document.querySelector("#arcsomnus-test-r");
        const detail = document.querySelector("#arcsomnus-test-detail");
        const which = document.querySelector("#arcsomnus-test-scene")?.value || "all";
        const scenes = which === "all" ? ["agent", "dresser", "extract"] : [which];
        el.textContent = "测…"; if (detail) detail.textContent = "";
        const out = [];
        try {
            for (const sc of scenes) {
                try {
                    const d = await api("/api/test_llm", "POST", { scene: sc });
                    out.push(d.status === "ok"
                        ? `✅${sc}通(${d.model || "?"}):${d.reply || ""}`
                        : `❌${sc}失败:${d.error || ""}`);
                } catch (e) { out.push(`❌${sc}后端连不上`); }
            }
            el.textContent = out.every(l => l.startsWith("✅")) ? "全通" : "有失败";
            if (detail) detail.textContent = out.join(" | ");
        } catch (e) { el.textContent = "后端连不上"; }
    });
    on("#arcsomnus-test-snap", "click", async () => {
        const el = document.querySelector("#arcsomnus-snap-r");
        const detail = document.querySelector("#arcsomnus-snap-detail");
        el.textContent = "查…";
        try {
            let chatFile = "";
            try { chatFile = getCurrentChatId() || ""; } catch {}
            const d = await api("/api/context/snapshot?chat_file=" + encodeURIComponent(chatFile));
            el.textContent = `存了${d.count || 0}条`;
            if (detail) {
                const pv = (d.preview || []);
                detail.textContent = pv.length
                    ? "抽查（只看一眼存没存，不代表只注入这些）：" + String(pv[pv.length - 1] || "").slice(0, 120)
                    : "快照是空的，先聊几句再查。";
            }
        } catch (e) { el.textContent = "后端连不上"; }
    });
    on("#arcsomnus-test-mem", "click", async () => {
        const box = document.querySelector("#arcsomnus-mem-preview");
        box.textContent = "拉…";
        try {
            const soul = document.querySelector("#arcsomnus-soul")?.value || "";
            if (!soul) { box.textContent = "先等角色列表加载出来"; return; }
            const d = await api("/api/memory?soul_id=" + encodeURIComponent(soul));
            box.textContent = (d.text || "(空的，她还没写过日记/信件)").slice(0, 600);
        } catch (e) { box.textContent = "后端连不上"; }
    });

    const soulSel = document.querySelector("#arcsomnus-soul");
    on("#arcsomnus-save-models", "click", async () => {
        const v = id => document.querySelector(id)?.value.trim() || "";
        const pairs = [
            ["llm_dresser_model", v("#arcsomnus-m-dresser")], ["llm_dresser_temperature", v("#arcsomnus-t-dresser")],
            ["llm_dresser_max_tokens", v("#arcsomnus-x-dresser")], ["llm_dresser_reasoning", v("#arcsomnus-r-dresser")],
            ["llm_extract_model", v("#arcsomnus-m-extract")], ["llm_extract_temperature", v("#arcsomnus-t-extract")],
            ["llm_extract_max_tokens", v("#arcsomnus-x-extract")], ["llm_extract_reasoning", v("#arcsomnus-r-extract")],
            ["llm_agent_model", v("#arcsomnus-m-agent")], ["llm_agent_temperature", v("#arcsomnus-t-agent")],
            ["llm_agent_max_tokens", v("#arcsomnus-x-agent")], ["llm_agent_reasoning", v("#arcsomnus-r-agent")],
        ];
        for (const [k, val] of pairs) {
            if (val) await api("/api/config", "POST", { key: k, value: val }).catch(() => {});
        }
    });
    on("#arcsomnus-save-snap", "click", async () => {
        const v = id => document.querySelector(id)?.value.trim() || "";
        const snapn = Math.max(10, Math.min(Number(v("#arcsomnus-snapn")) || 40, 100));
        settings().snapshot_n = snapn; saveSettingsDebounced();
        document.querySelector("#arcsomnus-snapn").value = snapn;
        for (const [k, val] of [["extract_context_n", v("#arcsomnus-extract-n")], ["agent_context_n", v("#arcsomnus-agent-n")]]) {
            if (val) await api("/api/config", "POST", { key: k, value: val }).catch(() => {});
        }
    });
    on("#arcsomnus-mem-en", "change", e => { settings().memory_enabled = e.target.checked; saveSettingsDebounced(); });
    on("#arcsomnus-save-mem", "click", async () => {
        const v = id => document.querySelector(id)?.value.trim() || "";
        const en = document.querySelector("#arcsomnus-mem-en")?.checked ? "1" : "0";
        settings().memory_enabled = en === "1"; saveSettingsDebounced();
        for (const [k, val] of [["memory_enabled", en], ["memory_letters_n", v("#arcsomnus-mem-letters")],
                ["memory_diary_n", v("#arcsomnus-mem-diary")], ["memory_max_chars", v("#arcsomnus-mem-chars")]]) {
            if (val) await api("/api/config", "POST", { key: k, value: val }).catch(() => {});
        }
    });
    on("#arcsomnus-save-email", "click", async () => {
        const v = id => document.querySelector(id)?.value.trim() || "";
        const pairs = [
            ["email_method", document.querySelector("#arcsomnus-email-method")?.value || "smtp"],
            ["target_email", v("#arcsomnus-target")],
            ["smtp_server", v("#arcsomnus-smtp-s")],
            ["smtp_port", v("#arcsomnus-smtp-p")],
            ["smtp_user", v("#arcsomnus-smtp-u")],
            ["smtp_pass", v("#arcsomnus-smtp-pass")],
            ["resend_key", v("#arcsomnus-resend-k")],
        ];
        for (const [k, val] of pairs) {
            if (val) await api("/api/config", "POST", { key: k, value: val }).catch(() => {});
        }
        document.querySelector("#arcsomnus-email-r").textContent = "已保存（密码不回填）";
    });
    const wbox = document.querySelector("#arcsomnus-wardrobe");
    const loadSouls = async () => {
        try {
            const d = await api("/api/souls");
            const names = (d.souls || []).map(f => f.replace(/\.[^.]+$/, ""));
            soulSel.innerHTML = names.map(n => `<option>${esc(n)}</option>`).join("");
            if (names.length) renderWardrobe(names[0], wbox);
        } catch { soulSel.innerHTML = ""; wbox.textContent = "后端连不上"; }
    };
    soulSel?.addEventListener("change", () => renderWardrobe(soulSel.value, wbox));
    on("#arcsomnus-revert", "click", async () => {
        await api("/api/wardrobe/revert", "POST", { soul_id: soulSel.value }).catch(() => {});
        renderWardrobe(soulSel.value, wbox);
    });
    on("#arcsomnus-add", "click", async () => {
        const name = document.querySelector("#arcsomnus-new-name")?.value.trim();
        const desc = document.querySelector("#arcsomnus-new-desc")?.value.trim();
        if (!name || !soulSel.value) return;
        await api("/api/wardrobe", "POST", { soul_id: soulSel.value, name, description: desc }).catch(() => {});
        renderWardrobe(soulSel.value, wbox);
    });
    loadSouls();

    const renderList = async (path, boxId, fmt) => {
        const box = document.querySelector(boxId);
        box.innerHTML = "加载中…";
        try {
            const soul = soulSel.value || "";
            const d = await api(path + "?soul_id=" + encodeURIComponent(soul));
            const items = d.items || [];
            box.innerHTML = items.length ? items.map(fmt).join("") : "空的，她还没写过。";
        } catch { box.textContent = "后端连不上"; }
    };
    const diaryFmt = d => `<div class="arcsomnus-cloth"><b>${esc(d.soul_id)}</b>
        <div class="arcsomnus-desc">${esc(d.content || "").slice(0, 300)}</div></div>`;
    const letterFmt = l => `<div class="arcsomnus-cloth"><b>${esc(l.subject || "(无题)")}</b>
        <span class="arcsomnus-badge">${esc(l.status || "")}</span>
        <div class="arcsomnus-desc">${esc(l.content || "").slice(0, 300)}</div></div>`;
    on("#arcsomnus-diary-r", "click", () => renderList("/api/diary", "#arcsomnus-diary", diaryFmt));
    on("#arcsomnus-letters-r", "click", () => renderList("/api/letters", "#arcsomnus-letters", letterFmt));
    on("#arcsomnus-sweep", "click", async () => {
        const el = document.querySelector("#arcsomnus-sweep-r");
        el.textContent = "唤醒中…";
        try {
            const d = await api("/api/sweep/now", "POST", {});
            el.textContent = d.status === "ok" ? ("跑完：" + JSON.stringify(d.results || [])) : ("失败：" + d.error);
            renderList("/api/diary", "#arcsomnus-diary", diaryFmt);
        } catch (e) { el.textContent = "后端连不上"; }
    });

    // 渠道回填（key类密码不回填）
    api("/api/config").then(d => {
        const set = (id, v) => { const el = document.querySelector(id); if (el && v) el.value = /key|pass/i.test(id) ? "" : v; };
        set("#arcsomnus-apiurl", d.api_url); set("#arcsomnus-model", d.model);
        set("#arcsomnus-temp", d.temperature); set("#arcsomnus-maxtok", d.max_tokens); set("#arcsomnus-reason", d.reasoning);
        set("#arcsomnus-m-dresser", d.llm_dresser_model); set("#arcsomnus-t-dresser", d.llm_dresser_temperature);
        set("#arcsomnus-x-dresser", d.llm_dresser_max_tokens); set("#arcsomnus-r-dresser", d.llm_dresser_reasoning);
        set("#arcsomnus-m-extract", d.llm_extract_model); set("#arcsomnus-t-extract", d.llm_extract_temperature);
        set("#arcsomnus-x-extract", d.llm_extract_max_tokens); set("#arcsomnus-r-extract", d.llm_extract_reasoning);
        set("#arcsomnus-m-agent", d.llm_agent_model); set("#arcsomnus-t-agent", d.llm_agent_temperature);
        set("#arcsomnus-x-agent", d.llm_agent_max_tokens); set("#arcsomnus-r-agent", d.llm_agent_reasoning);
        set("#arcsomnus-extract-n", d.extract_context_n); set("#arcsomnus-agent-n", d.agent_context_n);
        set("#arcsomnus-mem-letters", d.memory_letters_n); set("#arcsomnus-mem-diary", d.memory_diary_n);
        set("#arcsomnus-mem-chars", d.memory_max_chars);
    }).catch(() => {});
}

jQuery(async () => {
    settings();
    buildPanel();
    eventSource.on(event_types.MESSAGE_RECEIVED, () => { maybeDresser(); });
});
