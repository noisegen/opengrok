#!/usr/bin/env python3
"""Tests for host-main hook patching."""
import importlib.util
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

HERE = os.path.dirname(os.path.abspath(__file__))


def load_install():
    path = os.path.join(HERE, "install-brain-router.py")
    spec = importlib.util.spec_from_file_location("ibr", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.ibr = load_install()

    def test_stock_grok_bot_setup_hook(self):
        host = "PRE\n" + self.ibr.OLD_HOOK + "\nPOST"
        out = self.ibr.patch_host(host)
        self.assertIn("createBrandedSession", out)
        self.assertIn("resolveBrain", out)
        self.assertNotIn("shouldUseDeepseek", out)

    def test_boolean_gate_upgrade(self):
        host = "PRE\n" + self.ibr.BOOL_HOOK + "\nPOST"
        out = self.ibr.patch_host(host)
        self.assertIn("createBrandedSession", out)
        self.assertNotIn("shouldUseDeepseek", out)

    def test_idempotent(self):
        host = self.ibr.NEW_HOOK
        self.assertEqual(self.ibr.patch_host(host), host)

    def test_seed_bindings(self):
        import json

        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "brain-bindings.json")
            self.ibr.seed_bindings(p, "71b408bd-0c94-494b-8a45-754bc0ef2d73", "Long Run")
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["default"], "grok")
            self.assertEqual(
                data["agents"]["71b408bd-0c94-494b-8a45-754bc0ef2d73"]["brain"],
                "deepseek",
            )
            self.assertIn("deepseek", data["providers"])
            self.assertIn("kimi", data["providers"])

    def test_minified_hook(self):
        host = (
            'PRE;const inferenceProvider=(process.env.SAND_INFERENCE_PROVIDER||"xai").toLowerCase();'
            'if(inferenceProvider!=="cursor"){try{const{createXaiPromptSession}'
            '=require("./xai-prompt-session.cjs");return createXaiPromptSession('
            "{requestedModel,onRequestId,sessionOptions});}catch(xaiErr){"
            'console.error("[sand-xai] failed to create xAI session, falling back to Cursor:",xaiErr);}}'
            "const session=createCursorInferencePromptSession({"
        )
        out = self.ibr.patch_host(host)
        self.assertIn("createBrandedSession", out)
        self.assertIn("resolveBrain", out)
        self.assertIn("createCursorInferencePromptSession", out)

    def test_small_hook_script_minified(self):
        path = os.path.join(HERE, "patch-brain-hook.py")
        spec = importlib.util.spec_from_file_location("pbh", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        host = (
            'if(inferenceProvider!=="cursor"){try{const{createXaiPromptSession}'
            '=require("./xai-prompt-session.cjs");return createXaiPromptSession('
            "{requestedModel,onRequestId,sessionOptions});}catch(xaiErr){"
            'console.error("[sand-xai] failed to create xAI session, falling back to Cursor:",xaiErr);}}'
            "const session=createCursorInferencePromptSession({"
        )
        out = mod.patch(host)
        self.assertIn("createLazyBrainSession", out)
        self.assertIn("createXaiPromptSession", out)
        self.assertIn("conversationIdKey", out)
        self.assertIn("getConversationId", out)
        self.assertIn("nativeFactory", out)
        self.assertIn("createCursorInferencePromptSession", out)
        self.assertIn("sand-brain pass-through", out)
        self.assertNotIn("wrappedOnRequestId", out)
        self.assertIn("overlay failed, native", out)
        self.assertIn("createCursorInferencePromptSession", out)
        self.assertIn('if (inferenceProvider !== "cursor")', out)
        self.assertLess(out.find("createLazyBrainSession"), out.rfind("createXaiPromptSession"))
        self.assertNotIn(
            "return createXaiPromptSession({ requestedModel, onRequestId: cb",
            out,
        )
        self.assertNotIn("factoryFn", out)
        self.assertNotIn("innerFactory", out)

    def test_upgrades_branded_hook_without_convid(self):
        path = os.path.join(HERE, "patch-brain-hook.py")
        spec = importlib.util.spec_from_file_location("pbh2", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        old = (
            'if(inferenceProvider!=="cursor"){try{const{resolveBrain,createBrandedSession}'
            '=require("./brain-router.cjs");const{createXaiPromptSession}'
            '=require("./xai-prompt-session.cjs");const brain=resolveBrain(sessionOptions,requestedModel);'
            'if(brain&&brain.kind!=="native"){return createBrandedSession({requestedModel,onRequestId,sessionOptions,brain});}'
            '}catch(xaiErr){console.error("[sand-xai] failed:",xaiErr);}}'
        )
        out = mod.patch(old)
        self.assertIn("createLazyBrainSession", out)
        self.assertIn("createXaiPromptSession", out)
        self.assertIn("nativeFactory", out)
        self.assertNotIn("shouldUseDeepseek", out)

    def test_upgrades_where_hook_to_als_ctx(self):
        path = os.path.join(HERE, "patch-brain-hook.py")
        spec = importlib.util.spec_from_file_location("pbh3", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        old = (
            'if(inferenceProvider!=="cursor"){try{const{resolveBrain,createBrandedSession}'
            '=require("./brain-router.cjs");const{createXaiPromptSession}'
            '=require("./xai-prompt-session.cjs");let convId="";let where="none";'
            'try{if(typeof options2!=="undefined")take("options2",options2);}catch(e){}'
            'const so=Object.assign({},sessionOptions||{},{conversationId:convId});'
            'console.error("[sand-brain] where="+where+" conv="+convId+" typeofHost="+(typeof host));'
            'const brain=resolveBrain(so,requestedModel);'
            'if(brain&&brain.kind!=="native"){return createBrandedSession({requestedModel,onRequestId,sessionOptions:so,brain});}'
            '}catch(xaiErr){console.error("[sand-xai] failed:",xaiErr);}}'
        )
        out = mod.patch(old)
        self.assertIn("createLazyBrainSession", out)
        self.assertIn("createXaiPromptSession", out)
        self.assertEqual(out, mod.patch(out))

    def test_upgrades_options2keys_hook(self):
        path = os.path.join(HERE, "patch-brain-hook.py")
        spec = importlib.util.spec_from_file_location("pbh4", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        old = (
            'if(inferenceProvider!=="cursor"){try{const{resolveBrain,createBrandedSession}'
            '=require("./brain-router.cjs");const{createXaiPromptSession}'
            '=require("./xai-prompt-session.cjs");let convId="";let where="none";'
            'const gs=["getConversationId","getBcId"];'
            'console.error("[sand-brain] where="+where+" conv="+convId+" options2keys="+o2keys);'
            'const so=Object.assign({},sessionOptions||{},{conversationId:convId});'
            'const brain=resolveBrain(so,requestedModel);'
            'if(brain&&brain.kind!=="native"){return createBrandedSession({requestedModel,onRequestId,sessionOptions:so,brain});}'
            '}catch(xaiErr){console.error("[sand-xai] failed:",xaiErr);}}'
        )
        out = mod.patch(old)
        self.assertIn("createLazyBrainSession", out)
        self.assertNotEqual(out, old)
        self.assertEqual(out, mod.patch(out))

    def test_upgrades_ctx_hook_to_arg_walk(self):
        path = os.path.join(HERE, "patch-brain-hook.py")
        spec = importlib.util.spec_from_file_location("pbh5", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        old = (
            'if(inferenceProvider!=="cursor"){try{const{resolveBrain,createBrandedSession}'
            '=require("./brain-router.cjs");const{createXaiPromptSession}'
            '=require("./xai-prompt-session.cjs");let convId="";let where="none";'
            'const typeofCtx=typeof ctx;const typeofGetConversationId=typeof getConversationId;'
            'try{if(typeofGetConversationId==="function"&&typeofCtx!=="undefined"&&ctx){'
            'const v=String(getConversationId(ctx)||"");if(v){convId=v;where="getConversationId(ctx)";}}}'
            'catch(e){}const so=Object.assign({},sessionOptions||{},{conversationId:convId});'
            'console.error("[sand-brain] typeofCtx="+typeofCtx+" typeofGetConversationId="+typeofGetConversationId+" conv="+convId+" where="+where);'
            'const brain=resolveBrain(so,requestedModel);'
            'if(brain&&brain.kind!=="native"){return createBrandedSession({requestedModel,onRequestId,sessionOptions:so,brain});}'
            '}catch(xaiErr){console.error("[sand-xai] failed:",xaiErr);}}'
        )
        out = mod.patch(old)
        self.assertIn("createLazyBrainSession", out)
        self.assertIn("createXaiPromptSession", out)
        self.assertNotEqual(out, old)
        self.assertEqual(out, mod.patch(out))

    def test_upgrades_arg_walk_hook_to_lazy(self):
        path = os.path.join(HERE, "patch-brain-hook.py")
        spec = importlib.util.spec_from_file_location("pbh6", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        old = (
            'if(inferenceProvider!=="cursor"){try{const{resolveBrain,createBrandedSession}'
            '=require("./brain-router.cjs");const{createXaiPromptSession}'
            '=require("./xai-prompt-session.cjs");let convId="";let where="none";'
            'const typeofConversationIdKey=typeof conversationIdKey;const argc=arguments.length;'
            'const argKeys=[];console.error("[sand-brain] argKeys="+argKeys.join("|")+" conv="+convId);'
            'const so=Object.assign({},sessionOptions||{},{conversationId:convId});'
            'const brain=resolveBrain(so,requestedModel);'
            'if(brain&&brain.kind!=="native"){return createBrandedSession({requestedModel,onRequestId,sessionOptions:so,brain});}'
            '}catch(xaiErr){console.error("[sand-xai] failed:",xaiErr);}}'
        )
        out = mod.patch(old)
        self.assertIn("createLazyBrainSession", out)
        self.assertNotIn("argKeys=", out)
        self.assertEqual(out, mod.patch(out))

    def test_upgrades_innerfactory_lazy_hook(self):
        path = os.path.join(HERE, "patch-brain-hook.py")
        spec = importlib.util.spec_from_file_location("pbh7", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        old = (
            'if(inferenceProvider!=="cursor"){try{const{createLazyBrainSession}'
            '=require("./brain-router.cjs");const{createXaiPromptSession}'
            '=require("./xai-prompt-session.cjs");let cidKey;let getCid;'
            'const innerFactory=typeof factoryFn==="function"?factoryFn:'
            '(typeof arguments!=="undefined"&&typeof arguments[0]==="function"?arguments[0]:null);'
            'return createLazyBrainSession({requestedModel,onRequestId,sessionOptions,'
            'conversationIdKey:cidKey,getConversationId:getCid,createXaiPromptSession,'
            'nativeFactory:function(so){if(innerFactory)return innerFactory(so);'
            'throw new Error("sand-brain: no native factory");}});'
            '}catch(xaiErr){console.error("[sand-xai] failed:",xaiErr);}}'
        )
        out = mod.patch(old)
        self.assertIn("createLazyBrainSession", out)
        self.assertIn("sand-brain pass-through", out)
        self.assertNotIn("wrappedOnRequestId", out)
        self.assertNotIn("innerFactory", out)
        self.assertNotIn("factoryFn", out)
        self.assertEqual(out, mod.patch(out))

    def test_upgrades_lazy_cursor_fallback_hook(self):
        path = os.path.join(HERE, "patch-brain-hook.py")
        spec = importlib.util.spec_from_file_location("pbh8", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        old = (
            'if(inferenceProvider!=="cursor"){try{const{createLazyBrainSession,idFromCallArgs}'
            '=require("./brain-router.cjs");const{createXaiPromptSession}'
            '=require("./xai-prompt-session.cjs");let cidKey;let getCid;'
            'function wrappedOnRequestId(){try{const v=idFromCallArgs({conversationIdKey:cidKey,'
            'getConversationId:getCid},arguments);if(v){}}catch(e){}'
            'if(typeof onRequestId==="function")return onRequestId.apply(this,arguments);}'
            'return createLazyBrainSession({requestedModel,onRequestId:wrappedOnRequestId,'
            'sessionOptions,createXaiPromptSession});'
            '}catch(xaiErr){console.error("[sand-xai] failed to create xAI session, '
            'falling back to Cursor:",xaiErr);}}'
        )
        out = mod.patch(old)
        self.assertIn("createLazyBrainSession", out)
        self.assertIn("createXaiPromptSession", out)
        self.assertIn("overlay failed, native", out)
        self.assertIn("createCursorInferencePromptSession", out)
        self.assertNotIn("falling back to Cursor", out)
        self.assertNotIn(
            "return createXaiPromptSession({ requestedModel, onRequestId: cb",
            out,
        )
        self.assertNotEqual(out, old)
        self.assertEqual(out, mod.patch(out))

    def test_hook_keeps_createXaiPromptSession_string(self):
        path = os.path.join(HERE, "patch-brain-hook.py")
        spec = importlib.util.spec_from_file_location("pbh9", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertIn("createXaiPromptSession", mod.NEW)
        self.assertGreaterEqual(mod.NEW.count("createXaiPromptSession"), 2)
        self.assertIn("sand-brain pass-through", mod.NEW)
        self.assertNotIn("wrappedOnRequestId", mod.NEW)
        self.assertIn("overlay failed, native", mod.NEW)
        self.assertIn('if (inferenceProvider !== "cursor")', mod.NEW)
        nf = mod.NEW.split("nativeFactory:", 1)[1]
        self.assertIn("createCursorInferencePromptSession", nf)
        self.assertNotIn(
            "return createXaiPromptSession({ requestedModel, onRequestId: cb",
            nf,
        )

    def test_upgrades_xai_first_nativefactory(self):
        path = os.path.join(HERE, "patch-brain-hook.py")
        spec = importlib.util.spec_from_file_location("pbh10", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        old = (
            'if(inferenceProvider!=="cursor"){try{const{createLazyBrainSession,idFromCallArgs}'
            '=require("./brain-router.cjs");const{createXaiPromptSession}'
            '=require("./xai-prompt-session.cjs");function wrappedOnRequestId(){'
            'if(typeof onRequestId==="function")return onRequestId.apply(this,arguments);}'
            'return createLazyBrainSession({requestedModel,onRequestId:wrappedOnRequestId,'
            'sessionOptions,createXaiPromptSession,nativeFactory:function(so,rid){'
            'const cb=typeof rid==="function"?rid:wrappedOnRequestId;'
            'try{return createXaiPromptSession({requestedModel,onRequestId:cb,sessionOptions:so});}'
            'catch(e){}if(typeof createCursorInferencePromptSession==="function"){'
            'return createCursorInferencePromptSession({requestedModel,onRequestId:cb,sessionOptions:so});}'
            'throw new Error("sand-brain: no native factory");}});'
            '}catch(xaiErr){console.error("[sand-xai] failed to create xAI session, '
            'falling back to native:",xaiErr);}}'
        )
        out = mod.patch(old)
        self.assertNotIn(
            "return createXaiPromptSession({ requestedModel, onRequestId: cb",
            out,
        )
        self.assertIn("overlay failed, native", out)
        self.assertIn("sand-brain pass-through", out)
        self.assertEqual(out, mod.patch(out))

    def test_upgrades_stream_wrap_overlay_to_passthrough(self):
        path = os.path.join(HERE, "patch-brain-hook.py")
        spec = importlib.util.spec_from_file_location("pbh11", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        old = (
            "try {\n"
            ' const { createLazyBrainSession, idFromCallArgs } = require("./brain-router.cjs");\n'
            ' const { createXaiPromptSession } = require("./xai-prompt-session.cjs");\n'
            " function wrappedOnRequestId() {\n"
            "  if (typeof onRequestId === \"function\") return onRequestId.apply(this, arguments);\n"
            " }\n"
            " return createLazyBrainSession({\n"
            "  requestedModel, onRequestId: wrappedOnRequestId, sessionOptions,\n"
            "  nativeFactory: function (so, rid) {\n"
            "   const cb = typeof rid === \"function\" ? rid : wrappedOnRequestId;\n"
            "   return createCursorInferencePromptSession({ requestedModel, onRequestId: cb, sessionOptions: so });\n"
            "  }\n"
            " });\n"
            "} catch (sandErr) {\n"
            ' console.error("[sand-brain] overlay failed, native:", sandErr);\n'
            " return createCursorInferencePromptSession({ requestedModel, onRequestId, sessionOptions });\n"
            "}\n"
            'if (inferenceProvider !== "cursor") {\n'
            " try {\n"
            '  const { createXaiPromptSession } = require("./xai-prompt-session.cjs");\n'
            "  return createXaiPromptSession({ requestedModel, onRequestId, sessionOptions });\n"
            " } catch (xaiErr) {}\n"
            "}"
        )
        out = mod.patch(old)
        self.assertIn("sand-brain pass-through", out)
        self.assertNotIn("wrappedOnRequestId", out)
        self.assertIn("overlay failed, native", out)
        self.assertEqual(out, mod.patch(out))

    def test_patch_host_leaves_lazy_overlay(self):
        host = (
            'try{const{createLazyBrainSession}=require("./brain-router.cjs");'
            'return createLazyBrainSession({nativeFactory:function(){}});}'
            'catch(e){console.error("[sand-brain] overlay failed, native:",e);}'
            'if(inferenceProvider!=="cursor"){try{const{createXaiPromptSession}'
            '=require("./xai-prompt-session.cjs");return createXaiPromptSession('
            "{requestedModel,onRequestId,sessionOptions});}catch(xaiErr){}}"
        )
        self.assertEqual(self.ibr.patch_host(host), host)

    def test_preflight_cursor_is_ok(self):
        with tempfile.TemporaryDirectory() as td:
            env = os.path.join(td, "xai-inference.env")
            with open(env, "w", encoding="utf-8") as f:
                f.write("SAND_INFERENCE_PROVIDER=cursor\n")
            host_dir = os.path.join(td, "sand-host")
            os.makedirs(host_dir)
            open(os.path.join(host_dir, "xai-prompt-session.cjs"), "w", encoding="utf-8").write("ok\n")
            buf = StringIO()
            with redirect_stdout(buf):
                self.ibr.preflight(td, host_dir)
            note = buf.getvalue()
            self.assertIn("SAND_INFERENCE_PROVIDER=cursor", note)
            self.assertIn("that is correct", note)
            self.assertIn("Never ./adapters use deepseek", note)
            self.assertNotIn("hook is skipped", note)

    def test_preflight_missing_env_is_ok(self):
        with tempfile.TemporaryDirectory() as td:
            host_dir = os.path.join(td, "sand-host")
            os.makedirs(host_dir)
            open(os.path.join(host_dir, "xai-prompt-session.cjs"), "w", encoding="utf-8").write("ok\n")
            buf = StringIO()
            with redirect_stdout(buf):
                self.ibr.preflight(td, host_dir)
            note = buf.getvalue()
            self.assertIn("stay on cursor", note)
            self.assertIn("Never ./adapters use deepseek", note)


if __name__ == "__main__":
    unittest.main()
