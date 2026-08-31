"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const assert = require("assert");

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "brain-router-"));
const bindings = path.join(tmp, "brain-bindings.json");
const log = path.join(tmp, "sand-brain.log");
fs.writeFileSync(
  bindings,
  JSON.stringify({
    default: "grok",
    agents: {
      "71b408bd-0c94-494b-8a45-754bc0ef2d73": { brain: "deepseek", name: "Long Run" },
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
} = require("./brain-router.cjs");

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
assert.strictEqual(lazy, nativeRef);
assert.strictEqual(lazy.getExecutor, nativeRef.getExecutor);
assert.strictEqual(created.ready, true);
assert.strictEqual(typeof lazy.getModelId, "function");
assert.strictEqual(lazy.getModelId(), "mid-native:");
assert.strictEqual(lazy.extraMethod(), "extra:native:");
const ex = lazy.getExecutor([{ role: "user", content: "hi" }]);
assert.strictEqual(require("util").types.isProxy(ex), false);
const out = ex.stream(new Map([[cidKey, LONG_RUN]]), "inv1", []);
assert.strictEqual(created.kind, "native:");
assert.strictEqual(out, "native::inv1");

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
assert.strictEqual(createdReq.ready, true);
assert.ok(createdReq.kind.startsWith("native:"));
assert.strictEqual(typeof lazyReq.getModelId, "function");
reqOpts.onRequestId(new Map([[cidKey, LONG_RUN]]));
assert.ok(createdReq.kind.startsWith("native:"));
const reqOut = lazyReq.getExecutor([]).stream(new Map([[cidKey, LONG_RUN]]), "inv4", []);
assert.ok(createdReq.kind.startsWith("native:"));
assert.strictEqual(reqOut, "native::inv4");

const logTxt = fs.readFileSync(log, "utf8");
assert.ok(logTxt.includes("[sand-brain] lazy conv=" + LONG_RUN));
assert.ok(logTxt.includes("where=store"));
assert.ok(logTxt.includes("where=none"));
assert.ok(!logTxt.includes("where=stream"));
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
assert.strictEqual(protoSess.getExecutor, ProtoSession.prototype.getExecutor);

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
const hopPending = hopEx.stream();
assert.ok(typeof hopPending.then === "function");
assert.ok(hopUrl.indexOf("https://api.deepseek.com/v1/chat/completions") === 0);
assert.ok(hopAuth.indexOf("Bearer sk-test") === 0);
hopPending.catch(() => {});

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

console.log("ok");
