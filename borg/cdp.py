import asyncio
import aiohttp
import json
import platform
import re
from .jrpc import jrpc
from . import x


class cdp(jrpc):
    def __init__(self):
        super().__init__()
        self.proc = None
        self.targets = {}
        self.sid_to_page = {}
        self.on("notify", self.on_notify)

    def on_notify(self, msg):
        params = msg.get("params") or {}
        sid = msg.get("sessionId")
        tid = params.get("targetInfo", {}).get("targetId")

        if tid and msg["method"] == "Target.attachedToTarget":
            self.targets[tid] = params
            self.emit(f"attached_{tid}", params)

        if page := self.sid_to_page.get(sid):
            page.emit("notify", msg)

    async def init(self):
        await self.call("Target.setAutoAttach", {
            "autoAttach": True,
            "flatten": True,
            "waitForDebuggerOnStart": False,
        })

    async def call(self, method, params=None):
        params = dict(params or {})
        tid = params.pop("_targetId", None)
        req = {"method": method}

        if params:
            req["params"] = params

        if tid:
            req["sessionId"] = self.targets[tid]["sessionId"]

        ret = await super().call(req)

        if ret:
            return ret.get("result")

    async def create_target(self):
        page = cdp_page()
        page.cdp = self

        ret = await self.call("Target.createTarget", {
            "url": "about:blank",
            "background": True,
        })

        page.tid = ret["targetId"]

        if page.tid not in self.targets:
            event, _ = await self.once_future(f"attached_{page.tid} close")

            if event == "close":
                return

        page.sid = self.targets[page.tid]["sessionId"]
        self.sid_to_page[page.sid] = page

        await page.call("Runtime.enable")
        await page.call("Page.enable")
        await page.call("Network.enable")
        await page.call("ServiceWorker.enable")

        await page.call("Emulation.setFocusEmulationEnabled", {
            "enabled": True,
        })

        await page.call("Runtime.addBinding", {
            "name": "_send_to_cdp",
        })

        await page.call("Runtime.runIfWaitingForDebugger")

        await page.call("BackgroundService.clearEvents", {
            "service": "pushMessaging",
        })

        await page.call("BackgroundService.startObserving", {
            "service": "pushMessaging",
        })

        await page.call("BackgroundService.setRecording", {
            "service": "pushMessaging",
            "shouldRecord": True,
        })

        return page

    async def launch(self, user_data_dir, chrome_path=None):
        if chrome_path is None:
            chrome_path = {
                "Windows": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                "Darwin": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            }[platform.system()]

        args = [
            "--enable-automation",
            f"--user-data-dir={user_data_dir}",
            "--remote-debugging-port=1234",
            "--mute-audio",
            "--disable-blink-features=AutomationControlled",
            "--hide-crash-restore-bubble",
            "--disable-field-trial-config",
            "--disable-background-networking",
            "--enable-features=NetworkService,NetworkServiceInProcess",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-back-forward-cache",
            "--disable-breakpad",
            "--disable-client-side-phishing-detection",
            "--disable-component-extensions-with-background-pages",
            "--disable-component-update",
            "--no-default-browser-check",
            "--disable-default-apps",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--disable-features=InfiniteSessionRestore,ImprovedCookieControls,LazyFrameLoading,GlobalMediaControls,DestroyProfileOnBrowserClose,MediaRouter,DialMediaRouteProvider,AcceptCHFrame,AutoExpandDetailsElement,CertificateTransparencyComponentUpdater,AvoidUnnecessaryBeforeUnloadCheckSync,Translate,HttpsUpgrades,PaintHolding",
            "--allow-pre-commit-input",
            "--disable-hang-monitor",
            "--disable-ipc-flooding-protection",
            "--disable-popup-blocking",
            "--disable-prompt-on-repost",
            "--force-color-profile=srgb",
            "--metrics-recording-only",
            "--no-first-run",
            "--password-store=basic",
            "--use-mock-keychain",
            "--no-service-autorun",
            "--export-tagged-pdf",
            "--disable-search-engine-choice-screen",
            "--no-sandbox",
            "--ignore-certificate-errors",
        ]

        self.proc = await asyncio.create_subprocess_exec(chrome_path, *args)

        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    async with session.get("http://127.0.0.1:1234/json/version") as resp:
                        version = await resp.json()
                    break
                except Exception:
                    await asyncio.sleep(.2)

        await self.connect(version["webSocketDebuggerUrl"])
        await self.init()
        return self


    async def close(self):
        await super().close()

        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            await self.proc.wait()


class cdp_page(x.EventBus):
    def __init__(self):
        super().__init__()
        self.cdp = None
        self.tid = None
        self.sid = None

    async def call(self, method, params=None):
        params = dict(params or {})
        params["_targetId"] = self.tid
        return await self.cdp.call(method, params)

    def xpx(self, xp):
        xp = re.sub(
            r"ends-with\(([^,]+),([^)]+)\)",
            r"(substring(\1, string-length(\1)- string-length(\2) + 1) = \2)",
            xp,
        )

        xp = re.sub(
            r"icontains\(([^,]+),([^)]+)\)",
            r"contains(translate(\1,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),\2)",
            xp,
        )

        return xp

    async def click(self, xp):
        xp = json.dumps(self.xpx(xp))

        expression = f"""
        (async () => {{
            for(let i = 0; i < 5; i++) {{
                let el = document.evaluate({xp}, document)?.iterateNext()

                if(el) {{
                    el.click()
                    return true
                }}

                await new Promise(resolve => setTimeout(resolve, 300))
            }}

            return false
        }})()
        """

        ret = await self.call("Runtime.evaluate", {
            "returnByValue": True,
            "awaitPromise": True,
            "silent": True,
            "userGesture": True,
            "expression": expression,
        })

        return ret.get("result")
