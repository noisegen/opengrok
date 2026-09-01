"use strict";
/**
 * Per-Bot brain gate for grok-bot-setup's createSession hook.
 * Default is native Grok. Assigned DeepSeek agents use the OpenAI-compat hop
 * plus a short identity nudge.
 *
 * Live hop: DeepSeek only (BRAIN_LIVE=deepseek). Other catalog providers
 * can be stored on an agent; they fall back to Grok until enabled.
 */
const fs = require("fs");
const path = require("path");

const BINDINGS =
  process.env.BRAIN_BINDINGS ||
  path.join(process.env.HOME || "/home/box", "sand-data", "brain-bindings.json");

/** Durable hop-fail log — /tmp dies on Update Computer; sand-data survives. */
function defaultBrainLogPath() {
  const home = process.env.HOME || "/home/box";
  const sand = path.join(home, "sand-data", "hop-fail-logs", "sand-brain.log");
  const agent = path.join(home, "agent-data", "hop-fail-logs", "sand-brain.log");
  try {
    if (fs.existsSync(path.join(home, "sand-data"))) return sand;
  } catch {
    /* ignore */
  }
  try {
    if (fs.existsSync(path.join(home, "agent-data"))) return agent;
  } catch {
    /* ignore */
  }
  return "/tmp/sand-brain.log";
}

const LOG = process.env.BRAIN_LOG || defaultBrainLogPath();
const UUID_RE =
  /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;
const ID_KEY_RE =
  /^(conversationid|conversation_id|agentid|agent_id|bcid|bc_id|provenanceagentid|agentbcid|sessionid|botid|bot_id)$/i;
const IDENTITY_MARK = "[sand-brain]";
const LIVE_BRAINS = new Set(
  String(process.env.BRAIN_LIVE || "deepseek")
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean)
);

const STOCK_PROVIDERS = {
  grok: { kind: "native", label: "Grok 4.6", model: "grok-4.6" },
  deepseek: {
    kind: "openai",
    label: "DeepSeek",
    model: "deepseek-v4-flash",
    baseUrl: "https://api.deepseek.com/v1",
    keyEnv: "DEEPSEEK_API_KEY",
  },
  openrouter: {
    kind: "openai",
    label: "OpenRouter",
    model: "",
    baseUrl: "https://openrouter.ai/api/v1",
    keyEnv: "OPENROUTER_API_KEY",
  },
  glm: {
    kind: "openai",
    label: "GLM",
    model: "glm-5.3-flash",
    baseUrl: "",
    keyEnv: "ZAI_API_KEY",
  },
  kimi: {
    kind: "openai",
    label: "Kimi",
    model: "kimi-k2.5",
    baseUrl: "https://api.moonshot.ai/v1",
    keyEnv: "MOONSHOT_API_KEY",
  },
};

function logLine(msg) {
  try {
    try {
      fs.mkdirSync(path.dirname(LOG), { recursive: true });
    } catch {
      /* ignore */
    }
    fs.appendFileSync(LOG, new Date().toISOString() + " " + msg + "\n");
  } catch {
    /* logging must never throw into hop */
  }
}

function loadBindings() {
  try {
    return JSON.parse(fs.readFileSync(BINDINGS, "utf8"));
  } catch {
    return { default: "grok", providers: {}, agents: {} };
  }
}

function collect(obj, out, depth) {
  if (obj == null || depth > 8) return;
  if (typeof obj === "string") {
    const matches = obj.match(UUID_RE);
    if (matches) for (const m of matches) out.add(m.toLowerCase());
    return;
  }
  if (typeof obj === "number" || typeof obj === "boolean") return;
  if (Array.isArray(obj)) {
    for (const x of obj) collect(x, out, depth + 1);
    return;
  }
  if (typeof obj !== "object") return;
  let entries;
  try {
    entries = Object.entries(obj);
  } catch {
    return;
  }
  for (const [k, v] of entries) {
    try {
      if (ID_KEY_RE.test(k) && typeof v === "string" && v.trim()) {
        out.add(v.trim().toLowerCase());
      } else if (ID_KEY_RE.test(k) && typeof v === "function" && v.length === 0) {
        try {
          const got = v.call(obj);
          if (typeof got === "string" && got.trim()) out.add(got.trim().toLowerCase());
        } catch {
          /* ignore */
        }
      }
      if (typeof v !== "function") collect(v, out, depth + 1);
    } catch {
      /* ignore hostile getters */
    }
  }
}

function candidateIds(sessionOptions, requestedModel) {
  const out = new Set();
  collect(sessionOptions, out, 0);
  collect(requestedModel, out, 0);
  return [...out];
}

/** Merge wrap-time identity from sessionOptions + options2 + hints for resolveBrain. */
function buildIdentityBag(opts) {
  const bag = Object.assign({}, (opts && opts.sessionOptions) || {});
  const extras = [opts && opts.options2, opts && opts.identityBag, opts && opts.context];
  for (const src of extras) {
    if (!src || typeof src !== "object") continue;
    for (const key of [
      "agentId",
      "agent_id",
      "conversationId",
      "conversation_id",
      "bcId",
      "bc_id",
      "botId",
      "bot_id",
      "provenanceAgentId",
    ]) {
      if (bag[key]) continue;
      try {
        const v = src[key];
        if (typeof v === "string" && v.trim()) bag[key] = v.trim();
      } catch {
        /* ignore */
      }
    }
    for (const g of ["getAgentId", "getBotId", "getConversationId", "getBcId", "getAgentBCId"]) {
      try {
        if (typeof src[g] !== "function" || src[g].length !== 0) continue;
        const got = src[g]();
        if (typeof got !== "string" || !got.trim()) continue;
        const s = got.trim();
        if (!bag.agentId && /agent/i.test(g)) bag.agentId = s;
        else if (!bag.conversationId && /conversation/i.test(g)) bag.conversationId = s;
        else if (!bag.bcId && /bc/i.test(g)) bag.bcId = s;
        else if (!bag.botId && /bot/i.test(g)) bag.botId = s;
        else if (!bag.agentId) bag.agentId = s;
      } catch {
        /* ignore */
      }
    }
  }
  return bag;
}

function agentEntry(agents, id) {
  if (!agents || !id) return null;
  if (agents[id]) return agents[id];
  const lower = String(id).toLowerCase();
  if (agents[lower]) return agents[lower];
  for (const [k, ent] of Object.entries(agents)) {
    if (k.toLowerCase() === lower) return ent;
  }
  return null;
}

function providerOf(data, brainId) {
  const id = String(brainId || "grok").toLowerCase();
  const fromFile = (data.providers || {})[id] || {};
  const stock = STOCK_PROVIDERS[id] || { kind: "openai", label: brainId, model: "" };
  return Object.assign({ id }, stock, fromFile);
}

function resolveBrain(sessionOptions, requestedModel, moreSources) {
  const opts = sessionOptions || {};
  if (opts.isSummarizationSession === true) {
    logLine("summarization -> grok");
    return providerOf({}, "grok");
  }
  const idSet = new Set(candidateIds(opts, requestedModel));
  if (Array.isArray(moreSources)) {
    for (const src of moreSources) collect(src, idSet, 0);
  } else if (moreSources) {
    collect(moreSources, idSet, 0);
  }
  const ids = [...idSet];
  const keys =
    opts && typeof opts === "object" && !Array.isArray(opts)
      ? Object.keys(opts)
      : [];
  logLine("keys=" + keys.join(",") + " ids=" + (ids.join(",") || "(none)"));

  const data = loadBindings();
  const agents = data.agents || {};
  for (const id of ids) {
    const ent = agentEntry(agents, id);
    const brainId = ent && ent.brain ? String(ent.brain).toLowerCase() : "";
    if (!brainId || brainId === "grok" || brainId === "cursor" || brainId === "stock") {
      continue;
    }
    const brain = providerOf(data, brainId);
    brain.agentId = id;
    brain.agentName = (ent && ent.name) || "";
    if (ent.model) brain.model = ent.model;
    brain.kind = brain.kind || "openai";
    if (data.identity === false || ent.identity === false) brain.identity = false;
    if (!LIVE_BRAINS.has(brain.id)) {
      logLine("match " + id + " -> " + brain.id + " not live, using grok");
      return providerOf(data, "grok");
    }
    logLine("match " + id + " -> " + brain.id + " kind=" + brain.kind);
    return brain;
  }
  logLine("default grok");
  return providerOf(data, "grok");
}

function shouldUseDeepseek(sessionOptions, requestedModel) {
  const brain = resolveBrain(sessionOptions, requestedModel);
  return Boolean(brain && brain.kind !== "native" && LIVE_BRAINS.has(brain.id));
}

function identityText(brain) {
  if (!brain || brain.kind === "native" || brain.identity === false) return "";
  const label = brain.label || brain.id || "a custom model";
  const model = brain.model ? ` (${brain.model})` : "";
  const name = brain.agentName ? ` This Bot is named "${brain.agentName}".` : "";
  return (
    `${IDENTITY_MARK} You are ${label}${model} driving the Grok Bot harness ` +
    `(tools, computer, UI). You are not Grok 4.6 and not an xAI model. ` +
    `Grok Bot's own system prompt is the product wrapper — follow its tools and ` +
    `truth-seeking, but if asked which model you are, say ${label}${model}.` +
    name
  );
}

function messageRole(m) {
  if (!m || typeof m !== "object") return "";
  return String(m.role || "").toLowerCase();
}

function contentHasMark(m) {
  if (!m) return false;
  const c = m.content;
  if (typeof c === "string") return c.includes(IDENTITY_MARK);
  if (Array.isArray(c)) {
    return c.some((p) => p && typeof p.text === "string" && p.text.includes(IDENTITY_MARK));
  }
  return false;
}

function injectIdentity(messages, brain) {
  const text = identityText(brain);
  const list = Array.isArray(messages) ? messages.slice() : [];
  if (!text) return list;
  if (list.some(contentHasMark)) return list;
  let i = 0;
  while (i < list.length && messageRole(list[i]) === "system") i++;
  list.splice(i, 0, { role: "system", content: text });
  return list;
}

function snapshotMessages(ex, fallback) {
  try {
    if (ex && typeof ex.getMessages === "function") {
      const m = ex.getMessages();
      if (Array.isArray(m)) return m.slice();
    }
  } catch {
    /* ignore */
  }
  return Array.isArray(fallback) ? fallback.slice() : [];
}

function asId(v) {
  if (v == null) return "";
  const s = String(v);
  if (!s || s === "[object Object]") return "";
  return s;
}

function fromStore(store, conversationIdKey, getConversationId) {
  if (store == null) return "";
  if (typeof store === "string") return asId(store);
  try {
    if (typeof getConversationId === "function") {
      const v = asId(getConversationId(store));
      if (v) return v;
    }
  } catch {
    /* ignore */
  }
  try {
    if (store && typeof store.get === "function" && conversationIdKey != null) {
      const v = asId(store.get(conversationIdKey));
      if (v) return v;
    }
  } catch {
    /* ignore */
  }
  try {
    if (store && typeof store === "object") {
      const v = asId(store.conversationId || store.agentId);
      if (v) return v;
    }
  } catch {
    /* ignore */
  }
  return "";
}

function readConversationId(opts, ctx) {
  const key = opts && opts.conversationIdKey;
  const getter = opts && opts.getConversationId;
  let id = fromStore(ctx, key, getter);
  if (id) return id;
  if (key && typeof key.getStore === "function") {
    try {
      id = fromStore(key.getStore(), key, getter);
      if (id) return id;
    } catch {
      /* ignore */
    }
  }
  if (opts && opts.capturedId) {
    id = asId(opts.capturedId.id);
    if (id) return id;
  }
  if (opts && typeof opts.getBotId === "function") {
    try {
      id = asId(opts.getBotId(ctx));
      if (id) return id;
    } catch {
      /* ignore */
    }
  }
  return asId(opts && opts.sessionOptions && opts.sessionOptions.conversationId);
}

/** Walk callback args for a ctx-like object. Skip strings (request ids). */
function idFromCallArgs(opts, argsLike) {
  opts = opts || {};
  if (argsLike && argsLike.length) {
    for (let i = 0; i < argsLike.length; i++) {
      const a = argsLike[i];
      if (a == null || typeof a !== "object") continue;
      const id = fromStore(a, opts.conversationIdKey, opts.getConversationId);
      if (id) return id;
    }
  }
  return readConversationId(
    {
      conversationIdKey: opts.conversationIdKey,
      getConversationId: opts.getConversationId,
      getBotId: opts.getBotId,
      sessionOptions: opts.sessionOptions,
    },
    null
  );
}

function xaiFactory(opts) {
  if (typeof opts.createXaiPromptSession === "function") {
    return opts.createXaiPromptSession;
  }
  return require("./xai-prompt-session.cjs").createXaiPromptSession;
}

function envFileKey(filePath, names) {
  try {
    const text = fs.readFileSync(filePath, "utf8");
    for (const name of names) {
      const re = new RegExp("^\\s*" + name + "\\s*=\\s*(.*)$", "m");
      const m = text.match(re);
      if (!m) continue;
      let v = m[1].trim();
      if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
        v = v.slice(1, -1);
      }
      if (v) return v;
    }
  } catch {
    /* ignore */
  }
  return "";
}

function loadHopKey(brain) {
  const envName = (brain && brain.keyEnv) || "DEEPSEEK_API_KEY";
  const names = [envName, "DEEPSEEK_API_KEY", "XAI_API_KEY"];
  for (const n of names) {
    const fromEnv = process.env[n];
    if (fromEnv) return String(fromEnv);
  }
  const home = process.env.HOME || "/home/box";
  const sand = process.env.SAND_DATA || path.join(home, "sand-data");
  const files = [path.join(sand, "deepseek.env"), path.join(sand, "xai-inference.env")];
  for (const f of files) {
    const v = envFileKey(f, names);
    if (v) return v;
  }
  return "";
}

function partText(part) {
  if (part == null) return "";
  if (typeof part === "string") return part;
  if (typeof part !== "object") return String(part);
  if (typeof part.text === "string") return part.text;
  if (typeof part.content === "string") return part.content;
  return "";
}

function stringifyArgs(args) {
  if (typeof args === "string") return args;
  try {
    return JSON.stringify(args == null ? {} : args);
  } catch {
    return "{}";
  }
}

// UltraCode-Shim #3 / 7e5ca40: strict backends require one tool message per
// tool_call_id immediately after assistant tool_calls. Stub missing ids.
const SKIPPED_TOOL_STUB =
  "Tool call was not executed (rejected or skipped by the user).";

/**
 * Repair assistant tool_calls adjacency for DeepSeek/OpenAI (UltraCode-Shim #3).
 * Emit tool replies in call order immediately after each tool_calls message;
 * synthesize SKIPPED_TOOL_STUB for unanswered ids; defer user text until after.
 */
function sanitizeToolCallPairs(messages) {
  const list = Array.isArray(messages) ? messages : [];
  const out = [];
  let pendingIds = null;
  let pendingAt = -1;

  function collectTools(from, to, into) {
    for (let k = from; k < to; k++) {
      const t = list[k];
      if (!t || t.role !== "tool") continue;
      const id = String(t.tool_call_id || t.toolCallId || "");
      if (id && !into.has(id)) into.set(id, t);
    }
  }

  function flushPending(toolById) {
    if (!pendingIds || !pendingIds.length) return;
    const map = toolById || new Map();
    for (const id of pendingIds) {
      const hit = map.get(id);
      if (hit) {
        out.push({
          role: "tool",
          tool_call_id: String(hit.tool_call_id || hit.toolCallId || id),
          content: hit.content == null ? "" : String(hit.content),
        });
      } else {
        out.push({ role: "tool", tool_call_id: id, content: SKIPPED_TOOL_STUB });
      }
    }
    pendingIds = null;
    pendingAt = -1;
  }

  for (let i = 0; i < list.length; i++) {
    const m = list[i];
    if (!m || typeof m !== "object") continue;
    const role = m.role || "user";

    if (
      pendingIds &&
      ((role === "assistant" && Array.isArray(m.tool_calls) && m.tool_calls.length) ||
        role === "assistant" ||
        role === "system")
    ) {
      const toolById = new Map();
      collectTools(pendingAt + 1, i, toolById);
      flushPending(toolById);
    }

    if (role === "assistant" && Array.isArray(m.tool_calls) && m.tool_calls.length) {
      out.push({
        role: "assistant",
        content: m.content != null ? m.content : null,
        tool_calls: m.tool_calls,
      });
      pendingIds = m.tool_calls.map((tc) => String(tc.id));
      pendingAt = i;
      continue;
    }

    if (role === "tool") {
      if (!pendingIds) continue;
      continue;
    }

    if (role === "user" && pendingIds) {
      const toolById = new Map();
      collectTools(pendingAt + 1, i, toolById);
      let j = i + 1;
      while (j < list.length && list[j] && list[j].role === "tool") {
        const t = list[j];
        const id = String(t.tool_call_id || t.toolCallId || "");
        if (id && !toolById.has(id)) toolById.set(id, t);
        j++;
      }
      flushPending(toolById);
      const userContent =
        typeof m.content === "string"
          ? m.content
          : m.content != null
            ? stringifyArgs(m.content)
            : "";
      out.push({ role: "user", content: userContent });
      i = j - 1;
      continue;
    }

    out.push(m);
  }

  if (pendingIds) {
    const toolById = new Map();
    collectTools(pendingAt + 1, list.length, toolById);
    flushPending(toolById);
  }
  return out;
}

/**
 * Convert AI-SDK / Grok core messages to OpenAI chat.completions messages.
 * Preserves assistant tool_calls before role:tool (DeepSeek 400 otherwise).
 * Never JSON.stringifies whole part arrays into content.
 */
function toChatMessages(list) {
  const out = [];
  for (const m of Array.isArray(list) ? list : []) {
    if (!m || typeof m !== "object") continue;
    const role = m.role || "user";
    const content = m.content;
    const parts = Array.isArray(content)
      ? content
      : Array.isArray(m.parts)
        ? m.parts
        : null;

    if (role === "tool" || role === "function") {
      let id = m.tool_call_id || m.toolCallId || "";
      let c = content;
      if (parts) {
        for (const part of parts) {
          if (!part || typeof part !== "object") continue;
          const t = part.type;
          if (t === "tool-result" || t === "tool_result") {
            if (!id) id = part.toolCallId || part.tool_call_id || part.id || "";
            let result =
              part.result != null
                ? part.result
                : part.output != null
                  ? part.output
                  : part.content;
            if (typeof result !== "string") result = stringifyArgs(result);
            c = result;
          } else {
            const s = partText(part);
            if (s) c = typeof c === "string" && c ? c + s : s;
          }
        }
      } else if (Array.isArray(c)) {
        c = c.map(partText).filter(Boolean).join("") || stringifyArgs(c);
      } else if (typeof c !== "string") {
        c = c != null ? stringifyArgs(c) : "";
      }
      if (!id) continue;
      out.push({ role: "tool", tool_call_id: String(id), content: c == null ? "" : String(c) });
      continue;
    }

    if (role === "assistant" && Array.isArray(m.tool_calls) && m.tool_calls.length) {
      const msg = { role: "assistant", tool_calls: m.tool_calls, content: null };
      if (typeof content === "string" && content) msg.content = content;
      else if (parts) {
        const texts = parts.map(partText).filter(Boolean);
        if (texts.length) msg.content = texts.join("");
      }
      out.push(msg);
      continue;
    }

    if (parts) {
      const texts = [];
      const toolCalls = [];
      const toolResults = [];
      for (const part of parts) {
        if (part == null) continue;
        if (typeof part !== "object") {
          const t = partText(part);
          if (t) texts.push(t);
          continue;
        }
        const t = part.type;
        if (t === "text" || t === "input_text" || t === "output_text") {
          const s = partText(part);
          if (s) texts.push(s);
        } else if (t === "tool-call" || t === "tool_call") {
          const id = part.toolCallId || part.tool_call_id || part.id;
          const name =
            part.toolName ||
            part.tool_name ||
            (part.function && part.function.name) ||
            "";
          const args =
            part.args != null
              ? part.args
              : part.arguments != null
                ? part.arguments
                : part.function && part.function.arguments;
          if (id && name) {
            toolCalls.push({
              id: String(id),
              type: "function",
              function: { name: String(name), arguments: stringifyArgs(args) },
            });
          }
        } else if (t === "tool-result" || t === "tool_result") {
          const id = part.toolCallId || part.tool_call_id || part.id;
          let result =
            part.result != null
              ? part.result
              : part.output != null
                ? part.output
                : part.content;
          if (typeof result !== "string") result = stringifyArgs(result);
          if (id) {
            toolResults.push({
              role: "tool",
              tool_call_id: String(id),
              content: result,
            });
          }
        } else {
          const s = partText(part);
          if (s) texts.push(s);
        }
      }
      if (toolCalls.length) {
        out.push({
          role: "assistant",
          content: texts.length ? texts.join("") : null,
          tool_calls: toolCalls,
        });
        for (const tr of toolResults) out.push(tr);
      } else {
        // User tool-result parts must precede user text (UltraCode-Shim #3).
        if (toolResults.length && role === "user") {
          for (const tr of toolResults) out.push(tr);
        }
        if (texts.length) {
          out.push({
            role: role === "assistant" ? "assistant" : role,
            content: texts.join(""),
          });
        }
        if (toolResults.length && role !== "user") {
          for (const tr of toolResults) out.push(tr);
        }
      }
      continue;
    }

    if (typeof content === "string") {
      if (!content && role !== "assistant") continue;
      out.push({ role, content });
      continue;
    }

    // Skip unknown non-string content (do not dump objects into content).
  }
  return sanitizeToolCallPairs(out);
}

function hasChatPayload(messages) {
  for (const m of Array.isArray(messages) ? messages : []) {
    if (!m) continue;
    const role = m.role || "";
    // System-only identity is not enough to POST; need user/assistant/tool payload.
    if (role === "system") continue;
    if (role === "tool" && m.tool_call_id) return true;
    if (Array.isArray(m.tool_calls) && m.tool_calls.length) return true;
    if (typeof m.content === "string" && m.content.trim()) return true;
  }
  return false;
}

function redactHopSnippet(s) {
  return String(s || "")
    .replace(/sk-[A-Za-z0-9._-]{8,}/g, "[redacted]")
    .replace(/Bearer\s+\S+/gi, "Bearer [redacted]")
    .slice(0, 300);
}

async function readHopHttpError(res) {
  const status = (res && res.status) || 0;
  let raw = "";
  try {
    if (res && typeof res.text === "function") raw = await res.text();
  } catch {
    raw = "";
  }
  let message = "";
  try {
    const j = JSON.parse(raw);
    const err = j && j.error;
    message =
      (err && (err.message || err.code || err.type)) ||
      (typeof j.message === "string" ? j.message : "") ||
      "";
  } catch {
    message = raw;
  }
  return { status, message: redactHopSnippet(message) };
}

/**
 * Native executor.stream() returns a stream-result OBJECT synchronously
 * (fullStream / response / usage / extendedUsage). Returning a Promise
 * makes UsageSanitizingMiddleware read undefined.modelId and kill inference.
 */
function makeHopStreamResult(innerPromise, model) {
  const modelId = String(model || "deepseek-v4-flash");
  const inner = Promise.resolve(innerPromise);
  const response = inner.then(() => ({ modelId }));
  const usage = inner.then(() => ({ promptTokens: 0, completionTokens: 0 }));
  const extendedUsage = inner.then(() => ({ promptTokens: 0, completionTokens: 0 }));
  // Derived promises reject when hop fails; swallow until failClosed / caller attaches.
  inner.catch(() => {});
  response.catch(() => {});
  usage.catch(() => {});
  extendedUsage.catch(() => {});

  return {
    fullStream: (async function* () {
      const data = await inner;
      const text = (data && data.text) || "";
      if (text) {
        yield { type: "text-delta", textDelta: text };
      }
    })(),
    response,
    usage,
    extendedUsage,
  };
}

function rejectHopStreamResult(err, model) {
  const modelId = String(model || "deepseek-v4-flash");
  const rejected = Promise.reject(err);
  const response = rejected;
  const usage = rejected;
  const extendedUsage = rejected;
  rejected.catch(() => {});
  return {
    fullStream: (async function* () {
      await rejected;
    })(),
    response,
    usage,
    extendedUsage,
    modelId,
  };
}

/**
 * If hop stream-result fails (HTTP/empty), fall back to native origStream.
 * Always returns a sync value (never a raw Promise from hop).
 * Opaque sync returns from custom createHopSession (tests/legacy) pass through.
 */
function failClosedStreamResult(hopResult, origStream, args) {
  if (!origStream) return hopResult;

  // Bug 2: hop.stream() must not return a Promise to the harness.
  if (hopResult != null && typeof hopResult.then === "function") {
    try {
      hopResult.catch(() => {});
    } catch {
      /* ignore */
    }
    logLine("hop stream not a stream-result -> native");
    return origStream.apply(null, args);
  }

  // Proper stream-result: wrap so async HTTP/empty rejection uses native.
  if (
    hopResult &&
    typeof hopResult === "object" &&
    hopResult.response &&
    hopResult.fullStream
  ) {
    let nativeResult = null;
    const getNative = () => {
      if (!nativeResult) nativeResult = origStream.apply(null, args);
      return nativeResult;
    };

    // Swallow hop-side rejections until decided chooses.
    Promise.resolve(hopResult.response).catch(() => {});
    Promise.resolve(hopResult.usage).catch(() => {});
    Promise.resolve(hopResult.extendedUsage).catch(() => {});

    const decided = Promise.resolve(hopResult.response).then(
      () => ({ kind: "hop", result: hopResult }),
      (err) => {
        logLine("hop stream failed -> native: " + ((err && err.message) || err));
        return { kind: "native", result: getNative() };
      }
    );

    return {
      fullStream: (async function* () {
        const d = await decided;
        const r = d.result;
        if (!r || typeof r !== "object" || !r.fullStream) return;
        for await (const ev of r.fullStream) yield ev;
      })(),
      response: decided.then(async (d) => {
        const r = d.result;
        if (r && typeof r === "object" && r.response != null) {
          return typeof r.response.then === "function" ? await r.response : r.response;
        }
        return { modelId: "native" };
      }),
      usage: decided.then(async (d) => {
        const r = d.result;
        if (r && typeof r === "object" && r.usage != null) {
          return typeof r.usage.then === "function" ? await r.usage : r.usage;
        }
        return { promptTokens: 0, completionTokens: 0 };
      }),
      extendedUsage: decided.then(async (d) => {
        const r = d.result;
        if (r && typeof r === "object" && r.extendedUsage != null) {
          return typeof r.extendedUsage.then === "function"
            ? await r.extendedUsage
            : r.extendedUsage;
        }
        return { promptTokens: 0, completionTokens: 0 };
      }),
    };
  }

  // Opaque sync hop return (string / custom) — pass through unchanged.
  return hopResult;
}

function createDeepseekHopSession(brain, opts) {
  const key = loadHopKey(brain);
  if (!key) {
    logLine("hop no-key -> native");
    return null;
  }
  const base = String((brain && brain.baseUrl) || "https://api.deepseek.com/v1").replace(
    /\/+$/,
    ""
  );
  const model = (brain && brain.model) || "deepseek-v4-flash";
  const fetchFn = (opts && opts.hopFetch) || globalThis.fetch;
  if (typeof fetchFn !== "function") {
    logLine("hop no-fetch -> native");
    return null;
  }
  let messages = [];
  function modelId() {
    return model;
  }
  return {
    getModelId: modelId,
    getModelID: modelId,
    getExecutor(initialMessages) {
      const seeded =
        brain && brain.kind !== "native" && brain.identity !== false
          ? injectIdentity(initialMessages, brain)
          : Array.isArray(initialMessages)
            ? initialMessages.slice()
            : [];
      messages = seeded;
      return {
        getMessages() {
          return messages;
        },
        clearMessages() {
          messages = [];
        },
        appendMessages(next) {
          if (Array.isArray(next)) messages = messages.concat(next);
        },
        stream: function () {
          const chatMessages = toChatMessages(messages);
          if (!hasChatPayload(chatMessages)) {
            logLine("hop empty messages -> fail-closed");
            return rejectHopStreamResult(new Error("hop empty messages"), model);
          }
          const inner = (async () => {
            const res = await Promise.resolve(
              fetchFn(base + "/chat/completions", {
                method: "POST",
                headers: {
                  Authorization: "Bearer " + key,
                  "Content-Type": "application/json",
                },
                body: JSON.stringify({
                  model,
                  messages: chatMessages,
                  stream: false,
                }),
              })
            );
            if (!res || !res.ok) {
              const info = await readHopHttpError(res);
              logLine(
                "hop http " + info.status + (info.message ? ": " + info.message : "")
              );
              throw new Error(
                "hop http " + info.status + (info.message ? ": " + info.message : "")
              );
            }
            const json = typeof res.json === "function" ? await res.json() : res;
            const choice = json && json.choices && json.choices[0];
            const msg = choice && choice.message;
            return { text: (msg && msg.content) || "", modelId: model };
          })();
          return makeHopStreamResult(inner, model);
        },
      };
    },
  };
}

function brandedHop(opts, so, brain) {
  try {
    if (typeof opts.createHopSession === "function") {
      return opts.createHopSession(brain, opts, so);
    }
    return createDeepseekHopSession(brain, opts);
  } catch (err) {
    logLine("hop create failed -> native: " + ((err && err.message) || err));
    try {
      console.error("[sand-brain] hop create failed, native:", err);
    } catch {
      /* ignore */
    }
    return null;
  }
}

function brainLog(convId, brainId, where) {
  const line =
    "[sand-brain] lazy conv=" +
    (convId || "") +
    " brain=" +
    (brainId || "grok") +
    " where=" +
    (where || "none");
  logLine(line);
  try {
    console.error(line);
  } catch {
    /* ignore */
  }
}

function materializeSession(opts, convId) {
  const so = Object.assign(
    {},
    opts.sessionOptions || {},
    convId ? { conversationId: convId } : {}
  );
  const brain = resolveBrain(so, opts.requestedModel);
  if (brain && brain.kind !== "native") {
    const hop = brandedHop(opts, so, brain);
    if (hop) {
      // Lazy native: only materialize if hop stream fails closed.
      let nativeSess = null;
      const getNative = () => {
        if (!nativeSess) {
          nativeSess = openNative(
            Object.assign({}, opts, { sessionOptions: so }),
            opts.onRequestId
          );
        }
        return nativeSess;
      };
      const hopGetExecutor =
        typeof hop.getExecutor === "function" ? hop.getExecutor.bind(hop) : null;
      return proxyObject(hop, {
        getExecutor: function (initialMessages) {
          const hex = hopGetExecutor
            ? hopGetExecutor.apply(hop, arguments)
            : null;
          if (!hex || typeof hex.stream !== "function") {
            const n = getNative();
            return n.getExecutor.apply(n, arguments);
          }
          const execArgs = arguments;
          const origStream = function () {
            const n = getNative();
            const nex = n.getExecutor.apply(n, execArgs);
            return nex.stream.apply(nex, arguments);
          };
          return proxyObject(hex, {
            stream: function () {
              try {
                const hopResult = hex.stream.apply(hex, arguments);
                return failClosedStreamResult(hopResult, origStream, arguments);
              } catch (err) {
                logLine("alt stream failed, native: " + (err && err.message));
                return origStream.apply(null, arguments);
              }
            },
          });
        },
      });
    }
    logLine("hop unavailable -> native");
  }
  if (typeof opts.nativeFactory === "function") {
    return opts.nativeFactory(so, opts.onRequestId);
  }
  const create = xaiFactory(opts);
  return create({
    requestedModel: opts.requestedModel,
    onRequestId: opts.onRequestId,
    sessionOptions: so,
  });
}

function proxyObject(inner, overrides) {
  if (!inner || typeof inner !== "object") {
    throw new Error("sand-brain: missing inner session");
  }
  overrides = overrides || {};
  return new Proxy(inner, {
    get(target, prop) {
      if (Object.prototype.hasOwnProperty.call(overrides, prop)) {
        return overrides[prop];
      }
      if (prop === "getModelId" || prop === "getModelID") {
        const fn = target.getModelId || target.getModelID;
        if (typeof fn === "function") return fn.bind(target);
      }
      const v = target[prop];
      if (typeof v === "function") return v.bind(target);
      return v;
    },
  });
}

function wrapExecutor(ex, onStream, initialMessages, altExecutor) {
  if (!ex || typeof ex !== "object") return ex;
  const origStream = typeof ex.stream === "function" ? ex.stream.bind(ex) : null;
  return proxyObject(ex, {
    stream: function (ctx) {
      if (typeof onStream === "function") onStream(ctx);
      const args = arguments;
      try {
        const seed = snapshotMessages(ex, initialMessages);
        const alt = typeof altExecutor === "function" ? altExecutor(seed) : null;
        if (alt && typeof alt.stream === "function") {
          const hopResult = alt.stream.apply(alt, args);
          if (origStream) {
            return failClosedStreamResult(hopResult, origStream, args);
          }
          return hopResult;
        }
      } catch (err) {
        logLine("alt stream failed, native: " + (err && err.message));
      }
      if (!origStream) throw new Error("sand-brain: no stream");
      return origStream.apply(null, args);
    },
  });
}

function openNative(opts, onRequestId) {
  opts = opts || {};
  if (typeof opts.nativeFactory === "function") {
    return opts.nativeFactory(opts.sessionOptions || {}, onRequestId);
  }
  const create = xaiFactory(opts);
  return create({
    requestedModel: opts.requestedModel,
    onRequestId: onRequestId,
    sessionOptions: opts.sessionOptions,
  });
}

function readOptsId(opts) {
  return readConversationId(
    {
      conversationIdKey: opts.conversationIdKey,
      getConversationId: opts.getConversationId,
      getBotId: opts.getBotId,
      sessionOptions: opts.sessionOptions,
    },
    null
  );
}

function resolveFromOpts(opts, so, more) {
  const bag = so || buildIdentityBag(opts);
  const extras = [opts && opts.options2, opts && opts.identityBag, more].filter(Boolean);
  return resolveBrain(bag, opts.requestedModel, extras);
}

/**
 * When wrap-time identity is empty, do NOT eagerly native forever.
 * Re-resolve at getExecutor / stream (where=stream) once ctx/sessionOptions carry an id.
 */
function createDeferredHopSession(opts) {
  const native = openNative(opts, opts.onRequestId);
  let hopped = null;

  function tryHop(where, argsLike, ctx) {
    if (hopped) return hopped;
    const bag = buildIdentityBag(opts);
    const fromArgs = idFromCallArgs(opts, argsLike);
    const fromCtx = ctx != null ? fromStore(ctx, opts.conversationIdKey, opts.getConversationId) : "";
    const wrapId = readOptsId(opts);
    const id = fromArgs || fromCtx || wrapId || bag.conversationId || bag.agentId || bag.bcId || bag.botId || "";
    if (id) {
      if (!bag.conversationId) bag.conversationId = id;
      if (
        !bag.agentId &&
        /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)
      ) {
        bag.agentId = id;
      }
    }
    const brain = resolveFromOpts(
      Object.assign({}, opts, { sessionOptions: bag }),
      bag,
      ctx
    );
    const label = id || (brain && brain.agentId) || "";
    brainLog(label, (brain && brain.id) || "grok", where);
    if (brain && brain.kind !== "native") {
      hopped = materializeSession(
        Object.assign({}, opts, { sessionOptions: bag }),
        label || brain.agentId
      );
      return hopped;
    }
    return null;
  }

  return proxyObject(native, {
    getExecutor: function (initialMessages) {
      try {
        const hop = tryHop("executor", [], null);
        if (hop && typeof hop.getExecutor === "function") {
          return hop.getExecutor(initialMessages);
        }
      } catch (err) {
        logLine("executor hop failed, native: " + ((err && err.message) || err));
      }
      const ex = native.getExecutor.apply(native, arguments);
      if (!ex || typeof ex !== "object") return ex;
      const origStream = typeof ex.stream === "function" ? ex.stream.bind(ex) : null;
      return proxyObject(ex, {
        stream: function (ctx) {
          const args = arguments;
          try {
            const hop = tryHop("stream", args, ctx);
            if (hop && typeof hop.getExecutor === "function") {
              const seed = snapshotMessages(ex, initialMessages);
              const hex = hop.getExecutor(seed);
              if (hex && typeof hex.stream === "function") {
                const hopResult = hex.stream.apply(hex, args);
                if (origStream) {
                  return failClosedStreamResult(hopResult, origStream, args);
                }
                return hopResult;
              }
            }
          } catch (err) {
            logLine("stream hop failed, native: " + ((err && err.message) || err));
          }
          if (!origStream) throw new Error("sand-brain: no stream");
          return origStream.apply(null, args);
        },
      });
    },
  });
}

function buildLazyBrainSession(opts) {
  opts = opts || {};
  const origOnRequestId = opts.onRequestId;
  const bag = buildIdentityBag(opts);
  opts = Object.assign({}, opts, { sessionOptions: bag });

  const wrapId = readOptsId(opts);
  const brain = resolveFromOpts(opts, bag);
  const earlyId =
    wrapId ||
    bag.conversationId ||
    bag.agentId ||
    bag.bcId ||
    bag.botId ||
    (brain && brain.agentId) ||
    "";

  if (earlyId) {
    const so = Object.assign({}, bag, wrapId ? { conversationId: wrapId } : {});
    // Re-resolve with conversationId filled when wrapId came from ALS/store.
    const brain2 = resolveFromOpts(Object.assign({}, opts, { sessionOptions: so }), so);
    brainLog(earlyId, (brain2 && brain2.id) || "grok", "store");
    if (brain2 && brain2.kind !== "native") {
      return materializeSession(Object.assign({}, opts, { sessionOptions: so }), earlyId);
    }
    return openNative(Object.assign({}, opts, { sessionOptions: so }), origOnRequestId);
  }

  // No bot identity at wrap — log and defer. Empty cid must not nail native forever.
  brainLog("", "grok", "none");
  return createDeferredHopSession(opts);
}

function createLazyBrainSession(opts) {
  opts = opts || {};
  const origOnRequestId = opts.onRequestId;
  try {
    return buildLazyBrainSession(opts);
  } catch (err) {
    logLine("lazy fail-open: " + (err && err.message));
    if (typeof opts.nativeFactory === "function") {
      try {
        return opts.nativeFactory(opts.sessionOptions || {}, origOnRequestId);
      } catch (e2) {
        logLine("lazy fail-open native: " + (e2 && e2.message));
      }
    }
    throw err;
  }
}

function createBrandedSession(opts) {
  const brain = opts.brain || resolveBrain(opts.sessionOptions, opts.requestedModel);
  const create = xaiFactory(opts);
  const session = create(opts);
  const origGetExecutor = session.getExecutor.bind(session);
  session.getExecutor = function (initialMessages) {
    const seeded =
      brain && brain.kind !== "native" && brain.identity !== false
        ? injectIdentity(initialMessages, brain)
        : initialMessages;
    return origGetExecutor(seeded);
  };
  return session;
}

module.exports = {
  shouldUseDeepseek,
  resolveBrain,
  candidateIds,
  buildIdentityBag,
  identityText,
  injectIdentity,
  readConversationId,
  idFromCallArgs,
  createLazyBrainSession,
  createBrandedSession,
  createDeepseekHopSession,
  loadHopKey,
  toChatMessages,
  sanitizeToolCallPairs,
  hasChatPayload,
  makeHopStreamResult,
  failClosedStreamResult,
  SKIPPED_TOOL_STUB,
  STOCK_PROVIDERS,
  LIVE_BRAINS,
  defaultBrainLogPath,
  logLine,
  LOG,
};
