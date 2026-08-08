import asyncio
import aiohttp
import json
from . import x


class jrpc(x.EventBus):
    def __init__(self):
        super().__init__()
        self.id = 0

    async def connect(self, url):
        self.session = aiohttp.ClientSession()
        self.ws = await self.session.ws_connect(url)
        self.task = asyncio.create_task(self.loop())

    async def loop(self):
        try:
            async for wsmsg in self.ws:
                if wsmsg.type != aiohttp.WSMsgType.TEXT:
                    continue

                msg = json.loads(wsmsg.data)

                if "id" in msg:
                    self.emit(f'rpc_{msg["id"]}', msg)
                elif "method" in msg:
                    self.emit("notify", msg)
                    self.emit(msg["method"], msg.get("params"))
        finally:
            self.emit("close", self.ws.close_code)

    async def call(self, req):
        if "id" not in req:
            self.id += 1
            req["id"] = self.id

        ft = self.once_future(f'rpc_{req["id"]} close')

        await self.ws.send_json(req)
        event, ret = await ft

        if event == "close":
            return

        if error := ret.get("error"):
            exc = Exception(error["message"])
            exc.cause = {"req": req, "ret": ret}
            raise exc

        return ret

    async def notify(self, req):
        await self.ws.send_json(req)

    async def close(self):
        self.task.cancel()
        await self.ws.close()
        await self.session.close()
