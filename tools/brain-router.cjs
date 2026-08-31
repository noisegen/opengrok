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
          try {
            const hop = tryHop("stream", arguments, ctx);
            if (hop && typeof hop.getExecutor === "function") {
              const seed = snapshotMessages(ex, initialMessages);
              const hex = hop.getExecutor(seed);
              if (hex && typeof hex.stream === "function") {
                return hex.stream.apply(hex, arguments);
              }
            }
          } catch (err) {
            logLine("stream hop failed, native: " + ((err && err.message) || err));
          }
          if (!origStream) throw new Error("sand-brain: no stream");
          return origStream.apply(null, arguments);
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
  STOCK_PROVIDERS,
  LIVE_BRAINS,
  defaultBrainLogPath,
  logLine,
  LOG,
};
