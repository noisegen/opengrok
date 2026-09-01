"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const assert = require("assert");

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "brain-router-"));
const bindings = path.join(tmp, "brain-bindings.json");
const log = path.join(tmp, "sand-brain.log");
const XIAN = "3caa4bd8-2a3d-4a61-aed1-d2e78969faea";
fs.writeFileSync(
  bindings,
  JSON.stringify({
    default: "grok",
    agents: {
      "71b408bd-0c94-494b-8a45-754bc0ef2d73": { brain: "deepseek", name: "Long Run" },
      [XIAN]: { brain: "deepseek", name: "Xian" },
      "bbbbbbbb-0000-4000-8000-000000000002": { brain: "kimi", name: "Future" },
    },
  })
);
process.env.BRAIN_BINDINGS = bindings;
process.env.BRAIN_LOG = log;
const {
  shouldUseDeepseek,
  candidateIds,
  resolveBrain,
  identityText,
  injectIdentity,
  readConversationId,
  idFromCallArgs,
  createLazyBrainSession,
  createBrandedSession,
  createDeepseekHopSession,
  loadHopKey,
  defaultBrainLogPath,
  logLine,
  LOG,
  toChatMessages,
  sanitizeToolCallPairs,
  hasChatPayload,
  makeHopStreamResult,
  failClosedStreamResult,
  completeStreamResult,
  SKIPPED_TOOL_STUB,
} = require("./brain-router.cjs");

// Mirrors live host-main summarization / self-summary / tool stream wrapper.
function hostMainAttachStreamCatchHandlers(result) {
  result.extendedUsage.catch(() => ({
    inputTokens: 0,
    outputTokens: 0,
    promptTokens: 0,
    completionTokens: 0,
    totalTokens: 0,
  }));
  result.usage.catch(() => ({
    totalTokens: 0,
    promptTokens: 0,
    completionTokens: 0,
  }));
  result.providerMetadata.catch(() => undefined);
  result.invocationId.catch(() => undefined);
  result.response.catch(() => ({ modelId: "fallback" }));
}

function assertToolCallAdjacency(messages, label) {
  for (let i = 0; i < messages.length; i++) {
    const mm = messages[i];
    if (!mm || !Array.isArray(mm.tool_calls) || !mm.tool_calls.length) continue;
    const need = mm.tool_calls.map((t) => t.id);
    const got = [];
    let j = i + 1;
    while (j < messages.length && messages[j] && messages[j].role === "tool") {
      got.push(messages[j].tool_call_id);
      j++;
    }
    assert.deepStrictEqual(
      got,
      need,
      (label || "adjacency") + " tool_calls " + JSON.stringify(need) + " got " + JSON.stringify(got)
    );
  }
}

assert.strictEqual(
  shouldUseDeepseek({ conversationId: "71b408bd-0c94-494b-8a45-754bc0ef2d73" }),
  true
);
assert.strictEqual(
  shouldUseDeepseek({ conversationId: "aaaaaaaa-0000-4000-8000-000000000001" }),
  false
);
assert.strictEqual(
  shouldUseDeepseek({
    isSummarizationSession: true,
    conversationId: "71b408bd-0c94-494b-8a45-754bc0ef2d73",
  }),
  false
);
assert.strictEqual(
  shouldUseDeepseek({ conversationId: "bbbbbbbb-0000-4000-8000-000000000002" }),
  false
);
assert.strictEqual(
  resolveBrain({ conversationId: "bbbbbbbb-0000-4000-8000-000000000002" }).kind,
  "native"
);

const ids = candidateIds({
  nested: { agentId: "71b408bd-0c94-494b-8a45-754bc0ef2d73" },
});
assert.ok(ids.includes("71b408bd-0c94-494b-8a45-754bc0ef2d73"));

const brain = resolveBrain({
  conversationId: "71b408bd-0c94-494b-8a45-754bc0ef2d73",
});
assert.strictEqual(brain.id, "deepseek");
assert.strictEqual(brain.kind, "openai");
const text = identityText(brain);
assert.ok(text.includes("[sand-brain]"));
assert.ok(text.includes("DeepSeek"));
assert.ok(text.includes("not Grok 4.6"));

const msgs = [
  { role: "system", content: "You are Grok." },
  { role: "user", content: "tell me about sacred geometry" },
];
const branded = injectIdentity(msgs, brain);
assert.strictEqual(branded.length, 3);
assert.strictEqual(branded[1].role, "system");
assert.ok(branded[1].content.includes("[sand-brain]"));
assert.strictEqual(branded[2].role, "user");
assert.strictEqual(branded[2].content, "tell me about sacred geometry");
assert.strictEqual(msgs[1].content, "tell me about sacred geometry");
assert.ok(!branded.some((m) => String(m.content).includes("(continue)")));
assert.strictEqual(injectIdentity(branded, brain).length, 3);

const grok = resolveBrain({ conversationId: "aaaaaaaa-0000-4000-8000-000000000001" });
assert.strictEqual(grok.kind, "native");
assert.strictEqual(identityText(grok), "");

const LONG_RUN = "71b408bd-0c94-494b-8a45-754bc0ef2d73";
const cidKey = { name: "conversationId" };
assert.strictEqual(
  readConversationId(
    {
      conversationIdKey: cidKey,
      getConversationId: (ctx) => ctx && ctx.get(cidKey),
    },
    new Map([[cidKey, LONG_RUN]])
  ),
  LONG_RUN
);
const alsKey = {
  getStore() {
    return new Map([[cidKey, LONG_RUN]]);
  },
};
assert.strictEqual(
  readConversationId(
    {
      conversationIdKey: alsKey,
      getConversationId: (ctx) => ctx && ctx.get(cidKey),
    },
    null
  ),
  LONG_RUN
);

function fakeSession(kind, created) {
  created.kind = kind;
  created.ready = true;
  created.lastSeed = null;
  created.lastStreamArgs = null;
  return {
    getModelId() {
      return "mid-" + kind;
    },
    extraMethod() {
      return "extra:" + kind;
    },
    getExecutor(msgs) {
      created.lastSeed = Array.isArray(msgs) ? msgs.slice() : msgs;
      let messages = Array.isArray(msgs) ? msgs.slice() : [];
      return {
        stream: function () {
          created.lastStreamArgs = Array.prototype.slice.call(arguments);
          return kind + ":" + arguments[1];
        },
        getMessages() {
          return messages;
        },
        clearMessages() {
          messages = [];
        },
        appendMessages(next) {
          if (Array.isArray(next)) messages = messages.concat(next);
        },
      };
    },
  };
}

function hopSession(created) {
  return function (brain, _opts, so) {
    const sess = fakeSession("hop:" + ((so && so.conversationId) || ""), created);
    const orig = sess.getExecutor.bind(sess);
    sess.getExecutor = function (initialMessages) {
      const seeded =
        brain && brain.kind !== "native" && brain.identity !== false
          ? injectIdentity(initialMessages, brain)
          : initialMessages;
      return orig(seeded);
    };
    return sess;
  };
}

const created = { ready: false, kind: "" };
let nativeRef;
const lazy = createLazyBrainSession({
  sessionOptions: { modelId: "grok-4.6" },
  conversationIdKey: cidKey,
  getConversationId: (ctx) => ctx && ctx.get(cidKey),
  nativeFactory: (so) => {
    nativeRef = fakeSession("native:" + (so.conversationId || ""), created);
    return nativeRef;
  },
  createHopSession: hopSession(created),
});
// Empty cid at wrap → deferred proxy (not eager native forever).
assert.strictEqual(typeof lazy.getExecutor, "function");
assert.strictEqual(typeof lazy.getModelId, "function");
assert.strictEqual(lazy.getModelId(), "mid-native:");
assert.strictEqual(lazy.extraMethod(), "extra:native:");
const ex = lazy.getExecutor([{ role: "user", content: "hi" }]);
// Stream ctx carries the bound id → hop (where=stream).
const out = ex.stream(new Map([[cidKey, LONG_RUN]]), "inv1", []);
assert.strictEqual(created.kind, "hop:" + LONG_RUN);
assert.strictEqual(out, "hop:" + LONG_RUN + ":inv1");

const createdHopNow = { ready: false, kind: "" };
const hopNow = createLazyBrainSession({
  sessionOptions: { modelId: "grok-4.6", conversationId: LONG_RUN },
  nativeFactory: () => fakeSession("native", createdHopNow),
  createHopSession: hopSession(createdHopNow),
});
assert.strictEqual(createdHopNow.kind, "hop:" + LONG_RUN);
assert.strictEqual(typeof hopNow.getModelId, "function");
assert.strictEqual(hopNow.getModelId(), "mid-hop:" + LONG_RUN);

const createdNative = { ready: false, kind: "" };
const lazyNative = createLazyBrainSession({
  sessionOptions: { modelId: "grok-4.6" },
  conversationIdKey: cidKey,
  getConversationId: (ctx) => ctx && ctx.get(cidKey),
  nativeFactory: (so) => fakeSession("native:" + (so.conversationId || ""), createdNative),
  createXaiPromptSession: () => fakeSession("xai", createdNative),
});
lazyNative.getExecutor([]).stream(new Map(), "inv2", []);
assert.ok(createdNative.kind.startsWith("native:"));

const wrapAls = {
  getStore() {
    return new Map([[cidKey, LONG_RUN]]);
  },
};
const createdWrap = { ready: false, kind: "" };
const eager = createLazyBrainSession({
  sessionOptions: { modelId: "grok-4.6" },
  conversationIdKey: wrapAls,
  getConversationId: (ctx) => ctx && ctx.get(cidKey),
  nativeFactory: () => fakeSession("native", createdWrap),
  createHopSession: hopSession(createdWrap),
});
assert.strictEqual(createdWrap.kind, "hop:" + LONG_RUN);
assert.ok(typeof eager.getExecutor === "function");
assert.strictEqual(typeof eager.getModelId, "function");
assert.strictEqual(eager.getModelId(), "mid-hop:" + LONG_RUN);
assert.strictEqual(eager.extraMethod(), "extra:hop:" + LONG_RUN);

const createdSum = { ready: false, kind: "" };
const lazySum = createLazyBrainSession({
  sessionOptions: { isSummarizationSession: true },
  conversationIdKey: cidKey,
  getConversationId: (ctx) => ctx && ctx.get(cidKey),
  nativeFactory: () => fakeSession("native", createdSum),
  createXaiPromptSession: () => fakeSession("xai", createdSum),
});
lazySum.getExecutor([]).stream(new Map([[cidKey, LONG_RUN]]), "inv3", []);
assert.strictEqual(createdSum.kind, "native");

assert.strictEqual(
  idFromCallArgs(
    { conversationIdKey: cidKey, getConversationId: (ctx) => ctx && ctx.get(cidKey) },
    ["aaaaaaaa-0000-4000-8000-000000000099"]
  ),
  ""
);
assert.strictEqual(
  idFromCallArgs(
    { conversationIdKey: cidKey, getConversationId: (ctx) => ctx && ctx.get(cidKey) },
    [new Map([[cidKey, LONG_RUN]])]
  ),
  LONG_RUN
);

const createdReq = { ready: false, kind: "" };
const reqOpts = {
  sessionOptions: { modelId: "grok-4.6" },
  conversationIdKey: cidKey,
  getConversationId: (ctx) => ctx && ctx.get(cidKey),
  onRequestId: function () {},
  nativeFactory: (so) => fakeSession("native:" + (so.conversationId || ""), createdReq),
  createHopSession: hopSession(createdReq),
};
const lazyReq = createLazyBrainSession(reqOpts);
assert.strictEqual(typeof lazyReq.getModelId, "function");
reqOpts.onRequestId(new Map([[cidKey, LONG_RUN]]));
const reqOut = lazyReq.getExecutor([]).stream(new Map([[cidKey, LONG_RUN]]), "inv4", []);
assert.strictEqual(createdReq.kind, "hop:" + LONG_RUN);
assert.strictEqual(reqOut, "hop:" + LONG_RUN + ":inv4");

const logTxt = fs.readFileSync(log, "utf8");
assert.ok(logTxt.includes("[sand-brain] lazy conv=" + LONG_RUN));
assert.ok(logTxt.includes("where=store"));
assert.ok(logTxt.includes("where=none"));
assert.ok(logTxt.includes("where=stream"));
assert.ok(!logTxt.includes("where=onRequestId"));

const createdEmpty = { ready: false, kind: "" };
const lazyEmpty = createLazyBrainSession({
  sessionOptions: { modelId: "grok-4.6" },
  conversationIdKey: cidKey,
  getConversationId: (ctx) => ctx && ctx.get(cidKey),
  nativeFactory: () => fakeSession("native", createdEmpty),
  createXaiPromptSession: () => fakeSession("xai", createdEmpty),
});
lazyEmpty.getExecutor([]).stream(new Map(), "inv5", []);
const logEmpty = fs.readFileSync(log, "utf8");
assert.ok(logEmpty.includes("where=none"));
assert.ok(createdEmpty.kind.startsWith("native"));

// Recovered Cursor-native: empty conversationId, agentId = Xian → hop at store.
const createdXian = { ready: false, kind: "" };
const xianSess = createLazyBrainSession({
  sessionOptions: { modelId: "grok-4.6", agentId: XIAN },
  nativeFactory: () => fakeSession("native", createdXian),
  createHopSession: hopSession(createdXian),
});
assert.strictEqual(createdXian.kind, "hop:" + XIAN);
assert.strictEqual(xianSess.getModelId(), "mid-hop:" + XIAN);
const logXian = fs.readFileSync(log, "utf8");
assert.ok(logXian.includes("brain=deepseek"));
assert.ok(logXian.includes("where=store"));

// options2.getAgentId() harvest (zero-arg) → hop at store.
const createdOpts2 = { ready: false, kind: "" };
const opts2Sess = createLazyBrainSession({
  sessionOptions: { modelId: "grok-4.6" },
  options2: {
    getAccessToken() {
      return "tok";
    },
    getAgentId() {
      return XIAN;
    },
  },
  nativeFactory: () => fakeSession("native", createdOpts2),
  createHopSession: hopSession(createdOpts2),
});
assert.strictEqual(createdOpts2.kind, "hop:" + XIAN);

// Unassigned agent id → native.
const createdUn = { ready: false, kind: "" };
createLazyBrainSession({
  sessionOptions: {
    modelId: "grok-4.6",
    agentId: "aaaaaaaa-0000-4000-8000-000000000001",
  },
  nativeFactory: () => fakeSession("native-un", createdUn),
  createHopSession: hopSession(createdUn),
});
assert.strictEqual(createdUn.kind, "native-un");

function ProtoSession(kind, created) {
  created.kind = kind;
  created.ready = true;
}
ProtoSession.prototype.getModelId = function () {
  return "proto-" + this.kind;
};
ProtoSession.prototype.mystery = function () {
  return "mystery:" + this.kind;
};
ProtoSession.prototype.getExecutor = function () {
  const kind = this.kind;
  return {
    stream: function (_ctx, inv) {
      return kind + ":" + inv;
    },
  };
};
Object.defineProperty(ProtoSession.prototype, "kind", {
  get() {
    return this._kind;
  },
});

const createdProto = { ready: false, kind: "" };
function makeProto(kind, created) {
  const s = Object.create(ProtoSession.prototype);
  s._kind = kind;
  created.kind = kind;
  created.ready = true;
  return s;
}
const protoSess = createLazyBrainSession({
  sessionOptions: { modelId: "grok-4.6" },
  nativeFactory: () => makeProto("native-proto", createdProto),
  createXaiPromptSession: () => makeProto("xai-proto", createdProto),
});
assert.strictEqual(typeof protoSess.getModelId, "function");
assert.strictEqual(protoSess.getModelId(), "proto-native-proto");
assert.strictEqual(protoSess.mystery(), "mystery:native-proto");
assert.strictEqual(typeof protoSess.getExecutor, "function");
// Deferred hop wraps getExecutor; prototype method remains reachable via native until hop.

const holdBoom = {};
Object.defineProperty(holdBoom, "id", {
  get() {
    return "";
  },
  set() {
    throw new Error("hold boom");
  },
});
let cbArgs;
let cbThis;
const reqBoom = {
  capturedId: holdBoom,
  conversationIdKey: cidKey,
  getConversationId: (ctx) => ctx && ctx.get(cidKey),
  onRequestId: function (a, b, c) {
    cbThis = this;
    cbArgs = [a, b, c];
    return "cb-ok";
  },
  nativeFactory: () => fakeSession("native", { ready: false, kind: "" }),
  createXaiPromptSession: () => fakeSession("xai", { ready: false, kind: "" }),
};
createLazyBrainSession(reqBoom);
const ctxMap = new Map([[cidKey, LONG_RUN]]);
const hostThis = { host: true };
const cbRet = reqBoom.onRequestId.call(hostThis, ctxMap, "arg2", "arg3");
assert.strictEqual(cbRet, "cb-ok");
assert.strictEqual(cbThis, hostThis);
assert.strictEqual(cbArgs[0], ctxMap);
assert.strictEqual(cbArgs[1], "arg2");
assert.strictEqual(cbArgs[2], "arg3");

let xaiFallback = false;
assert.throws(() => {
  createLazyBrainSession({
    sessionOptions: { modelId: "grok-4.6" },
    nativeFactory: () => {
      throw new Error("native boom");
    },
    createXaiPromptSession: () => {
      xaiFallback = true;
      return fakeSession("xai-fallback", { ready: false, kind: "" });
    },
  });
});
assert.strictEqual(xaiFallback, false);

function hookFailOpen(requireMap) {
  const requestedModel = "grok";
  const sessionOptions = {};
  const onRequestId = function orig() {
    return "orig";
  };
  function createCursorInferencePromptSession() {
    return {
      via: "cursor",
      getModelId() {
        return "cursor";
      },
    };
  }
  try {
    const { createLazyBrainSession: lazy } = requireMap["./brain-router.cjs"]();
    const { createXaiPromptSession } = requireMap["./xai-prompt-session.cjs"]();
    return lazy({
      requestedModel,
      onRequestId,
      sessionOptions,
      createXaiPromptSession,
    });
  } catch (xaiErr) {
    return createCursorInferencePromptSession({ requestedModel, onRequestId, sessionOptions });
  }
}

const nativeHook = {
  via: "xai",
  getModelId() {
    return "xai";
  },
  getExecutor() {
    return {};
  },
};
const fromRequireFail = hookFailOpen({
  "./brain-router.cjs": () => {
    throw new Error("MODULE_NOT_FOUND");
  },
  "./xai-prompt-session.cjs": () => ({
    createXaiPromptSession: (o) => {
      assert.strictEqual(typeof o.onRequestId, "function");
      assert.strictEqual(o.onRequestId(), "orig");
      return nativeHook;
    },
  }),
});
assert.strictEqual(fromRequireFail.via, "cursor");
assert.strictEqual(fromRequireFail.getModelId(), "cursor");

const fromThrow = hookFailOpen({
  "./brain-router.cjs": () => ({
    createLazyBrainSession: () => {
      throw new Error("lazy boom");
    },
  }),
  "./xai-prompt-session.cjs": () => ({
    createXaiPromptSession: () => nativeHook,
  }),
});
assert.strictEqual(fromThrow.via, "cursor");

const createdFwd = { ready: false, kind: "" };
const lazyFwd = createLazyBrainSession({
  sessionOptions: { modelId: "grok-4.6", conversationId: LONG_RUN },
  conversationIdKey: cidKey,
  getConversationId: (ctx) => ctx && ctx.get(cidKey),
  nativeFactory: (so) => fakeSession("native:" + (so.conversationId || ""), createdFwd),
  createHopSession: hopSession(createdFwd),
});
const userTurn = { role: "user", content: "tell me about sacred geometry" };
const fwdEx = lazyFwd.getExecutor([
  { role: "system", content: "You are Grok." },
  userTurn,
]);
const fwdCtx = new Map([[cidKey, LONG_RUN]]);
const extraPayload = { kept: true };
const fwdOut = fwdEx.stream(fwdCtx, "inv-fwd", ["tool-a"], extraPayload, "tail");
assert.strictEqual(fwdOut, "hop:" + LONG_RUN + ":inv-fwd");
assert.strictEqual(createdFwd.lastStreamArgs[0], fwdCtx);
assert.strictEqual(createdFwd.lastStreamArgs[1], "inv-fwd");
assert.deepStrictEqual(createdFwd.lastStreamArgs[2], ["tool-a"]);
assert.strictEqual(createdFwd.lastStreamArgs[3], extraPayload);
assert.strictEqual(createdFwd.lastStreamArgs[4], "tail");
assert.strictEqual(createdFwd.lastStreamArgs.length, 5);
const fwdUsers = (createdFwd.lastSeed || []).filter((m) => m && m.role === "user");
assert.strictEqual(fwdUsers.length, 1);
assert.strictEqual(fwdUsers[0].content, "tell me about sacred geometry");
assert.ok(
  (createdFwd.lastSeed || []).some(
    (m) => m && m.role === "system" && String(m.content).includes("[sand-brain]")
  )
);
assert.ok(!(createdFwd.lastSeed || []).some((m) => String(m.content).includes("(continue)")));

const createdBrand = { ready: false, kind: "" };
const brandSess = createBrandedSession({
  brain,
  sessionOptions: { conversationId: LONG_RUN },
  createXaiPromptSession: () => fakeSession("xai-brand", createdBrand),
});
const brandUser = { role: "user", content: "tell me about sacred geometry" };
const brandEx = brandSess.getExecutor([
  { role: "system", content: "You are Grok." },
  brandUser,
]);
const afterBrand = brandEx.getMessages();
const brandUsers = afterBrand.filter((m) => m && m.role === "user");
assert.strictEqual(brandUsers.length, 1);
assert.strictEqual(brandUsers[0].content, "tell me about sacred geometry");
assert.strictEqual(brandUser.content, "tell me about sacred geometry");
assert.ok(afterBrand.some((m) => m.role === "system" && String(m.content).includes("[sand-brain]")));
const brandCtx = { ctx: true };
brandEx.stream(brandCtx, "inv-brand", [], "rest-a", "rest-b");
assert.strictEqual(createdBrand.lastStreamArgs[0], brandCtx);
assert.strictEqual(createdBrand.lastStreamArgs[3], "rest-a");
assert.strictEqual(createdBrand.lastStreamArgs[4], "rest-b");
assert.strictEqual(brandEx.getMessages().find((m) => m.role === "user").content, brandUser.content);
assert.ok(!brandEx.getMessages().some((m) => String(m.content).includes("(continue)")));

const createdIgnoreXai = { ready: false, kind: "" };
const lazyIgnoreXai = createLazyBrainSession({
  sessionOptions: { conversationId: LONG_RUN, modelId: "grok-4.6" },
  nativeFactory: () => fakeSession("native-keep", createdIgnoreXai),
  createXaiPromptSession: () => fakeSession("xai-should-not", createdIgnoreXai),
});
assert.strictEqual(createdIgnoreXai.kind, "native-keep");
assert.strictEqual(typeof lazyIgnoreXai.getExecutor, "function");

const prevKey = process.env.DEEPSEEK_API_KEY;
delete process.env.DEEPSEEK_API_KEY;
assert.strictEqual(createDeepseekHopSession({ keyEnv: "DEEPSEEK_API_KEY" }, {}), null);

const sandHop = fs.mkdtempSync(path.join(os.tmpdir(), "brain-hop-"));
fs.writeFileSync(path.join(sandHop, "deepseek.env"), "DEEPSEEK_API_KEY=file-secret\n");
const prevSand = process.env.SAND_DATA;
process.env.SAND_DATA = sandHop;
assert.strictEqual(loadHopKey({ keyEnv: "DEEPSEEK_API_KEY" }), "file-secret");
process.env.SAND_DATA = prevSand;

process.env.DEEPSEEK_API_KEY = "sk-test";
let hopUrl = "";
let hopAuth = "";
let hopBody = null;
const hopSess = createDeepseekHopSession(
  {
    kind: "openai",
    id: "deepseek",
    model: "deepseek-v4-flash",
    baseUrl: "https://api.deepseek.com/v1",
    keyEnv: "DEEPSEEK_API_KEY",
  },
  {
    hopFetch: (url, init) => {
      hopUrl = url;
      hopAuth = String((init && init.headers && init.headers.Authorization) || "");
      hopBody = JSON.parse(init.body);
      return {
        ok: true,
        json: () => Promise.resolve({ choices: [{ message: { content: "sacred" } }] }),
      };
    },
  }
);
assert.ok(hopSess);
const hopEx = hopSess.getExecutor([
  { role: "system", content: "You are Grok." },
  { role: "user", content: "tell me about sacred geometry" },
]);
assert.ok(hopEx.getMessages().some((m) => String(m.content).includes("[sand-brain]")));
assert.ok(hopEx.getMessages().some((m) => m.role === "user" && m.content.includes("sacred geometry")));
assert.strictEqual(typeof hopEx.getState, "function");
const hopState = hopEx.getState();
assert.ok(Array.isArray(hopState));
assert.ok(hopState.some((m) => m.role === "user" && m.content.includes("sacred geometry")));
hopState.push({ role: "user", content: "mutated-copy" });
assert.ok(!hopEx.getMessages().some((m) => m.content === "mutated-copy"));
// host-main SimplePromptToolExecutor / RedactedPromptToolExecutor pattern.
const fakeToolMiddleware = { innerExecutor: hopEx };
assert.doesNotThrow(() => {
  const s = fakeToolMiddleware.innerExecutor.getState();
  assert.ok(Array.isArray(s));
});
const hopResult = hopEx.stream();
// Bug 2: must be sync stream-result object, NOT a Promise.
assert.strictEqual(typeof hopResult.then, "undefined");
assert.ok(hopResult.fullStream);
assert.ok(hopResult.response);
assert.ok(hopResult.usage);
assert.ok(hopResult.extendedUsage);
assert.ok(typeof hopResult.providerMetadata.catch === "function");
assert.ok(typeof hopResult.invocationId.catch === "function");
hostMainAttachStreamCatchHandlers(hopResult);
assert.ok(hopUrl.indexOf("https://api.deepseek.com/v1/chat/completions") === 0);
assert.ok(hopAuth.indexOf("Bearer sk-test") === 0);
assert.ok(Array.isArray(hopBody.messages));
assert.ok(hopBody.messages.some((m) => m.role === "user"));

// --- toChatMessages / OpenAI conversion ---
{
  const textParts = toChatMessages([
    { role: "user", content: [{ type: "text", text: "hello " }, { type: "text", text: "world" }] },
  ]);
  assert.deepStrictEqual(textParts, [{ role: "user", content: "hello world" }]);

  const stringOnly = toChatMessages([{ role: "user", content: "ping" }]);
  assert.deepStrictEqual(stringOnly, [{ role: "user", content: "ping" }]);

  const empty = toChatMessages([]);
  assert.deepStrictEqual(empty, []);
  assert.strictEqual(hasChatPayload(empty), false);
  assert.strictEqual(hasChatPayload([{ role: "system", content: "identity only" }]), false);

  const toolPair = toChatMessages([
    {
      role: "assistant",
      content: [
        { type: "tool-call", toolCallId: "call_1", toolName: "lookup", args: { q: "x" } },
      ],
    },
    {
      role: "tool",
      content: [{ type: "tool-result", toolCallId: "call_1", result: { ok: true } }],
    },
    { role: "user", content: "testing" },
  ]);
  assert.strictEqual(toolPair.length, 3);
  assert.strictEqual(toolPair[0].role, "assistant");
  assert.ok(Array.isArray(toolPair[0].tool_calls));
  assert.strictEqual(toolPair[0].tool_calls[0].id, "call_1");
  assert.strictEqual(toolPair[0].tool_calls[0].type, "function");
  assert.strictEqual(toolPair[0].tool_calls[0].function.name, "lookup");
  assert.strictEqual(toolPair[0].tool_calls[0].function.arguments, JSON.stringify({ q: "x" }));
  assert.strictEqual(toolPair[1].role, "tool");
  assert.strictEqual(toolPair[1].tool_call_id, "call_1");
  assert.ok(!String(toolPair[0].content || "").includes("tool-call"));
  assert.strictEqual(hasChatPayload(toolPair), true);
  assertToolCallAdjacency(toolPair, "happy path");

  // Reject/skip: no tool-result → stub, adjacency holds (UltraCode-Shim #3).
  const rejectSkip = toChatMessages([
    {
      role: "assistant",
      content: [
        { type: "tool-call", toolCallId: "call_1", toolName: "grep", args: { q: "x" } },
      ],
    },
    { role: "user", content: "nah, do it differently" },
  ]);
  assertToolCallAdjacency(rejectSkip, "reject skip");
  assert.strictEqual(rejectSkip.length, 3);
  assert.strictEqual(rejectSkip[0].role, "assistant");
  assert.strictEqual(rejectSkip[1].role, "tool");
  assert.strictEqual(rejectSkip[1].tool_call_id, "call_1");
  assert.strictEqual(rejectSkip[1].content, SKIPPED_TOOL_STUB);
  assert.strictEqual(rejectSkip[2].role, "user");
  assert.strictEqual(rejectSkip[2].content, "nah, do it differently");

  // Partial parallel: call_1 result, call_2 missing → stub on call_2.
  const partialPair = toChatMessages([
    {
      role: "assistant",
      content: [
        { type: "tool-call", toolCallId: "call_1", toolName: "read", args: {} },
        { type: "tool-call", toolCallId: "call_2", toolName: "grep", args: {} },
      ],
    },
    {
      role: "user",
      content: [{ type: "tool-result", toolCallId: "call_1", result: "done" }],
    },
  ]);
  assertToolCallAdjacency(partialPair, "partial parallel");
  const toolIds = partialPair.filter((m) => m.role === "tool").map((m) => m.tool_call_id);
  assert.deepStrictEqual(toolIds, ["call_1", "call_2"]);
  assert.strictEqual(partialPair[1].content, "done");
  assert.strictEqual(partialPair[2].content, SKIPPED_TOOL_STUB);

  // User comment mixed with tool-result in same turn → tools first, then user.
  const mixedComment = toChatMessages([
    {
      role: "assistant",
      content: [
        { type: "tool-call", toolCallId: "call_1", toolName: "t", args: {} },
      ],
    },
    {
      role: "user",
      content: [
        { type: "tool-result", toolCallId: "call_1", result: "rejected" },
        { type: "text", text: "no, do it differently" },
      ],
    },
  ]);
  assertToolCallAdjacency(mixedComment, "mixed comment");
  assert.strictEqual(mixedComment[1].role, "tool");
  assert.strictEqual(mixedComment[1].content, "rejected");
  assert.strictEqual(mixedComment[2].role, "user");
  assert.strictEqual(mixedComment[2].content, "no, do it differently");

  // Long Grok-style thread: partial/missing results get stubs, adjacency throughout.
  const longThread = toChatMessages([
    { role: "user", content: "find the router" },
    {
      role: "assistant",
      content: [
        { type: "text", text: "I'll search." },
        { type: "tool-call", toolCallId: "call_1", toolName: "glob", args: { p: "**/*" } },
      ],
    },
    {
      role: "tool",
      content: [{ type: "tool-result", toolCallId: "call_1", result: ["a.cjs"] }],
    },
    {
      role: "assistant",
      content: [{ type: "text", text: "Found a.cjs" }],
    },
    { role: "user", content: "read it" },
    {
      role: "assistant",
      content: [
        { type: "tool-call", toolCallId: "call_2", toolName: "read", args: { path: "a.cjs" } },
        { type: "tool-call", toolCallId: "call_3", toolName: "grep", args: { q: "hop" } },
      ],
    },
    {
      role: "tool",
      content: [{ type: "tool-result", toolCallId: "call_2", result: "contents" }],
    },
    {
      role: "assistant",
      content: [{ type: "text", text: "Partial results." }],
    },
    { role: "user", content: "testing" },
  ]);
  assertToolCallAdjacency(longThread, "long thread");
  assert.ok(longThread.some((m) => m.role === "user" && m.content === "testing"));
  const call3Tool = longThread.find(
    (m) => m.role === "tool" && m.tool_call_id === "call_3"
  );
  assert.ok(call3Tool, "call_3 must have stub tool message");
  assert.strictEqual(call3Tool.content, SKIPPED_TOOL_STUB);
}

// makeHopStreamResult shape (not a Promise; all host-main .catch fields present)
{
  const shaped = makeHopStreamResult(Promise.resolve({ text: "hi" }), "deepseek-v4-flash");
  assert.strictEqual(typeof shaped.then, "undefined");
  assert.ok(shaped.fullStream);
  assert.ok(typeof shaped.response.then === "function");
  assert.ok(typeof shaped.usage.catch === "function");
  assert.ok(typeof shaped.extendedUsage.catch === "function");
  assert.ok(typeof shaped.providerMetadata.catch === "function");
  assert.ok(typeof shaped.invocationId.catch === "function");
  hostMainAttachStreamCatchHandlers(shaped);

  // failClosed completes native fallback missing providerMetadata / invocationId.
  const wrapped = failClosedStreamResult(
    shaped,
    function () {
      return {
        fullStream: (async function* () {})(),
        response: Promise.resolve({ modelId: "n" }),
        usage: Promise.resolve({ totalTokens: 0, promptTokens: 0, completionTokens: 0 }),
        extendedUsage: Promise.resolve({
          inputTokens: 0,
          outputTokens: 0,
          promptTokens: 0,
          completionTokens: 0,
          totalTokens: 0,
        }),
      };
    },
    []
  );
  assert.ok(typeof wrapped.providerMetadata.catch === "function");
  assert.ok(typeof wrapped.invocationId.catch === "function");
  hostMainAttachStreamCatchHandlers(wrapped);

  const completed = completeStreamResult({
    fullStream: (async function* () {})(),
    response: Promise.resolve({ modelId: "x" }),
  });
  assert.ok(typeof completed.providerMetadata.catch === "function");
  assert.ok(typeof completed.invocationId.catch === "function");
  hostMainAttachStreamCatchHandlers(completed);
}

function nativeStreamSession(marker) {
  return {
    getModelId: () => "mid-native",
    getExecutor() {
      return {
        stream() {
          marker.nativeStreamed = true;
          return {
            fullStream: (async function* () {
              yield { type: "text-delta", textDelta: marker.text || "native-fallback" };
            })(),
            response: Promise.resolve({ modelId: "grok-native" }),
            usage: Promise.resolve({ promptTokens: 0, completionTokens: 0 }),
            extendedUsage: Promise.resolve({ promptTokens: 0, completionTokens: 0 }),
          };
        },
        getMessages() {
          return [];
        },
      };
    },
  };
}

(async () => {
  const resp = await hopResult.response;
  assert.ok(resp && typeof resp.modelId === "string" && resp.modelId.trim());
  assert.strictEqual(resp.modelId.trim(), "deepseek-v4-flash");
  const texts = [];
  for await (const ev of hopResult.fullStream) {
    if (ev && ev.type === "text-delta") texts.push(ev.textDelta);
  }
  assert.strictEqual(texts.join(""), "sacred");

  process.env.DEEPSEEK_API_KEY = "sk-test";

  // 200: hop yields assistant text; sanitize can trim modelId
  const mark200 = { nativeStreamed: false, text: "native-fallback" };
  const lazy200 = createLazyBrainSession({
    sessionOptions: { conversationId: LONG_RUN, modelId: "grok-4.6" },
    nativeFactory: () => nativeStreamSession(mark200),
    hopFetch: () => ({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ choices: [{ message: { content: "from-hop" } }] }),
    }),
  });
  const ex200 = lazy200.getExecutor([{ role: "user", content: "hi" }]);
  const r200 = ex200.stream({}, "inv-200", []);
  assert.strictEqual(typeof r200.then, "undefined");
  assert.ok(r200.response && r200.fullStream);
  hostMainAttachStreamCatchHandlers(r200);
  const resp200 = await r200.response;
  assert.ok(typeof resp200.modelId.trim() === "string" && resp200.modelId.trim());
  const parts200 = [];
  for await (const ev of r200.fullStream) {
    if (ev && ev.type === "text-delta") parts200.push(ev.textDelta);
  }
  assert.strictEqual(parts200.join(""), "from-hop");

  // 400: fail closed to native; response.modelId.trim() must not throw
  const mark400 = { nativeStreamed: false, text: "native-after-400" };
  const lazy400 = createLazyBrainSession({
    sessionOptions: { conversationId: LONG_RUN, modelId: "grok-4.6" },
    nativeFactory: () => nativeStreamSession(mark400),
    hopFetch: () => ({
      ok: false,
      status: 400,
      text: () =>
        Promise.resolve(
          JSON.stringify({
            error: {
              message:
                "Messages with role 'tool' must be a response to a preceding message with 'tool_calls'",
            },
          })
        ),
    }),
  });
  const ex400 = lazy400.getExecutor([
    {
      role: "assistant",
      content: [{ type: "tool-call", toolCallId: "c1", toolName: "t", args: {} }],
    },
    {
      role: "tool",
      content: [{ type: "tool-result", toolCallId: "c1", result: "ok" }],
    },
    { role: "user", content: "testing" },
  ]);
  const r400 = ex400.stream({}, "inv-400", []);
  assert.strictEqual(typeof r400.then, "undefined");
  assert.ok(r400.response);
  hostMainAttachStreamCatchHandlers(r400);
  const [response2, usage2] = await Promise.all([r400.response, r400.extendedUsage]);
  assert.ok(response2 && typeof response2.modelId === "string");
  assert.strictEqual(response2.modelId.trim(), "grok-native");
  assert.ok(usage2);
  const parts400 = [];
  for await (const ev of r400.fullStream) {
    if (ev && ev.type === "text-delta") parts400.push(ev.textDelta);
  }
  assert.strictEqual(parts400.join(""), "native-after-400");
  assert.strictEqual(mark400.nativeStreamed, true);

  // empty / system-only → no POST; fail closed to native
  let emptyPosted = false;
  const markEmpty = { nativeStreamed: false, text: "native-empty" };
  const lazyEmpty = createLazyBrainSession({
    sessionOptions: { conversationId: LONG_RUN, modelId: "grok-4.6" },
    nativeFactory: () => nativeStreamSession(markEmpty),
    hopFetch: () => {
      emptyPosted = true;
      return {
        ok: true,
        json: () => Promise.resolve({ choices: [{ message: { content: "x" } }] }),
      };
    },
  });
  const emptyEx = lazyEmpty.getExecutor([]);
  const rEmpty = emptyEx.stream({}, "inv-empty", []);
  assert.strictEqual(typeof rEmpty.then, "undefined");
  const respEmpty = await rEmpty.response;
  assert.strictEqual(respEmpty.modelId.trim(), "grok-native");
  assert.strictEqual(emptyPosted, false);

  // Promise-shaped hop → immediate native (Bug 2 harness shape)
  const nativeSync = failClosedStreamResult(
    Promise.resolve("bad"),
    function () {
      return {
        fullStream: (async function* () {})(),
        response: Promise.resolve({ modelId: "n" }),
        usage: Promise.resolve({}),
        extendedUsage: Promise.resolve({}),
      };
    },
    []
  );
  assert.strictEqual(typeof nativeSync.then, "undefined");
  assert.ok(nativeSync.response);
  assert.ok(typeof nativeSync.providerMetadata.catch === "function");
  hostMainAttachStreamCatchHandlers(nativeSync);
  const nr = await nativeSync.response;
  assert.strictEqual(nr.modelId, "n");

  if (prevKey == null) delete process.env.DEEPSEEK_API_KEY;
  else process.env.DEEPSEEK_API_KEY = prevKey;
  delete process.env.DEEPSEEK_API_KEY;

  const createdNo = { ready: false, kind: "" };
  const lazyNoKey = createLazyBrainSession({
    sessionOptions: { modelId: "grok-4.6" },
    conversationIdKey: cidKey,
    getConversationId: (ctx) => ctx && ctx.get(cidKey),
    nativeFactory: () => fakeSession("native", createdNo),
    hopFetch: () => {
      throw new Error("fetch without key");
    },
  });
  lazyNoKey.getExecutor([]).stream(new Map([[cidKey, LONG_RUN]]), "inv-nokey", []);
  assert.ok(createdNo.kind.startsWith("native"));

  const createdBoom = { ready: false, kind: "" };
  const lazyBoom = createLazyBrainSession({
    sessionOptions: { conversationId: LONG_RUN, modelId: "grok-4.6" },
    nativeFactory: () => fakeSession("native-after-hop-boom", createdBoom),
    createHopSession: () => {
      throw new Error("simulated hop explode");
    },
  });
  assert.strictEqual(createdBoom.kind, "native-after-hop-boom");
  assert.strictEqual(typeof lazyBoom.getExecutor, "function");

  {
    const { spawnSync } = require("child_process");
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "brain-log-home-"));
    fs.mkdirSync(path.join(home, "sand-data"));
    const script = `
    delete process.env.BRAIN_LOG;
    process.env.HOME = ${JSON.stringify(home)};
    process.env.BRAIN_BINDINGS = ${JSON.stringify(bindings)};
    const m = require(${JSON.stringify(path.join(__dirname, "brain-router.cjs"))});
    const p = m.defaultBrainLogPath();
    if (!String(p).includes("sand-data")) process.exit(2);
    if (!String(p).includes("hop-fail-logs")) process.exit(3);
    if (!String(p).endsWith("sand-brain.log")) process.exit(4);
    if (m.LOG !== p) process.exit(5);
    m.logLine("durable-default-ok");
    const fs = require("fs");
    if (!fs.existsSync(p)) process.exit(6);
    if (!fs.readFileSync(p, "utf8").includes("durable-default-ok")) process.exit(7);
  `;
    const r = spawnSync(process.execPath, ["-e", script], {
      encoding: "utf8",
      env: { ...process.env, HOME: home },
    });
    assert.strictEqual(r.status, 0, r.stderr || r.stdout);
  }

  {
    const { spawnSync } = require("child_process");
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "brain-log-ovr-"));
    fs.mkdirSync(path.join(home, "sand-data"));
    const custom = path.join(home, "custom-brain.log");
    const script = `
    process.env.HOME = ${JSON.stringify(home)};
    process.env.BRAIN_LOG = ${JSON.stringify(custom)};
    process.env.BRAIN_BINDINGS = ${JSON.stringify(bindings)};
    const m = require(${JSON.stringify(path.join(__dirname, "brain-router.cjs"))});
    if (m.LOG !== ${JSON.stringify(custom)}) process.exit(2);
    m.logLine("override-ok");
  `;
    const r = spawnSync(process.execPath, ["-e", script], { encoding: "utf8" });
    assert.strictEqual(r.status, 0, r.stderr || r.stdout);
    assert.ok(fs.readFileSync(custom, "utf8").includes("override-ok"));
  }

  logLine("no-throw-check");
  console.log("ok");
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
