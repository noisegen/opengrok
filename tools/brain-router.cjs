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
const LOG = process.env.BRAIN_LOG || "/tmp/sand-brain.log";
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
    fs.appendFileSync(LOG, new Date().toISOString() + " " + msg + "\n");
  } catch {
    /* ignore */
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
  for (const [k, v] of Object.entries(obj)) {
    if (ID_KEY_RE.test(k) && typeof v === "string" && v.trim()) {
      out.add(v.trim().toLowerCase());
    }
    collect(v, out, depth + 1);
  }
}

function candidateIds(sessionOptions, requestedModel) {
  const out = new Set();
  collect(sessionOptions, out, 0);
  collect(requestedModel, out, 0);
  return [...out];
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

function resolveBrain(sessionOptions, requestedModel) {
  const opts = sessionOptions || {};
  if (opts.isSummarizationSession === true) {
    logLine("summarization -> grok");
    return providerOf({}, "grok");
  }
  const ids = candidateIds(opts, requestedModel);
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

function toChatMessages(list) {
  return (Array.isArray(list) ? list : []).map((m) => ({
    role: (m && m.role) || "user",
    content:
      m && typeof m.content === "string"
        ? m.content
        : m && m.content != null
          ? JSON.stringify(m.content)
          : "",
  }));
}

function createDeepseekHopSession(brain, opts) {
  const key = loadHopKey(brain);
  if (!key) {
    logLine("hop no-key -> native");
    return null;
  }
  const base = String((brain && brain.baseUrl) || "https://api.deepseek.com/v1").replace(/\/+$/, "");
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
          return Promise.resolve(
            fetchFn(base + "/chat/completions", {
              method: "POST",
              headers: {
                Authorization: "Bearer " + key,
                "Content-Type": "application/json",
              },
              body: JSON.stringify({
                model,
                messages: toChatMessages(messages),
                stream: false,
              }),
            })
          ).then((res) => {
            if (!res || !res.ok) {
              throw new Error("hop http " + (res && res.status));
            }
            return typeof res.json === "function" ? res.json() : res;
          }).then((json) => {
            const choice = json && json.choices && json.choices[0];
            const msg = choice && choice.message;
            return (msg && msg.content) || "";
          });
        },
      };
    },
  };
}

function brandedHop(opts, so, brain) {
  if (typeof opts.createHopSession === "function") {
    return opts.createHopSession(brain, opts, so);
  }
  return createDeepseekHopSession(brain, opts);
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
    if (hop) return hop;
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
      try {
        const seed = snapshotMessages(ex, initialMessages);
        const alt = typeof altExecutor === "function" ? altExecutor(seed) : null;
        if (alt && typeof alt.stream === "function") {
          return alt.stream.apply(alt, arguments);
        }
      } catch (err) {
        logLine("alt stream failed, native: " + (err && err.message));
      }
      if (!origStream) throw new Error("sand-brain: no stream");
      return origStream.apply(null, arguments);
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

function buildLazyBrainSession(opts) {
  const origOnRequestId = opts.onRequestId;
  const wrapId = readOptsId(opts);
  if (wrapId) {
    const so = Object.assign({}, opts.sessionOptions || {}, { conversationId: wrapId });
    const brain = resolveBrain(so, opts.requestedModel);
    brainLog(wrapId, (brain && brain.id) || "grok", "store");
    if (brain && brain.kind !== "native") {
      return materializeSession(opts, wrapId);
    }
  } else {
    brainLog("", "grok", "none");
  }
  return openNative(opts, origOnRequestId);
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
  identityText,
  injectIdentity,
  readConversationId,
  idFromCallArgs,
  createLazyBrainSession,
  createBrandedSession,
  createDeepseekHopSession,
  loadHopKey,
  STOCK_PROVIDERS,
  LIVE_BRAINS,
};
