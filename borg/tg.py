import asyncio
import ctypes
import json
import pathlib

import aiohttp

from . import x


class bot:
    def __init__(self, bot_token):
        self.bot_token = bot_token
        self.chat_id_migrations = {}

    async def req(self, req):
        if isinstance(req.get("chat_id"), str):
            req["chat_id"] = int(req["chat_id"])

        if chat_id := req.get("chat_id"):
            if migrated := self.chat_id_migrations.get(chat_id):
                req["chat_id"] = migrated

        url = f'https://api.telegram.org/bot{self.bot_token}/{req["method"]}'

        async with aiohttp.request("POST", url, json=req) as resp:
            ret = await resp.json()

        if ret.get("ok") is False:
            if migrated := (ret.get("parameters") or {}).get("migrate_to_chat_id"):
                self.chat_id_migrations[req["chat_id"]] = migrated
                return await self.req(req)

        return ret

    async def send_message(self, chat_id, msg):
        return await self.req({
            "method": "sendMessage",
            "chat_id": chat_id,
            "text": msg,
        })


class td(x.EventBus):
    def __init__(self):
        super().__init__()
        self.rpc_id = 0
        self.lib = None

    def loadlib(self, path):
        path = pathlib.Path(path)

        if path.is_dir():
            path = path / "tdjson.dll"

        path = path.resolve()
        self.lib = ctypes.CDLL(str(path))

        self.lib.td_create_client_id.argtypes = []
        self.lib.td_create_client_id.restype = ctypes.c_int

        self.lib.td_send.argtypes = [ctypes.c_int, ctypes.c_char_p]
        self.lib.td_send.restype = None

        self.lib.td_receive.argtypes = [ctypes.c_double]
        self.lib.td_receive.restype = ctypes.c_char_p

        self.lib.td_execute.argtypes = [ctypes.c_char_p]
        self.lib.td_execute.restype = ctypes.c_char_p

        self.lib.td_set_log_message_callback.restype = None
        return self

    def create(self):
        self.client_id = self.lib.td_create_client_id()
        self.task = asyncio.create_task(self.receive_loop())

    def execute(self, args):
        ret = self.lib.td_execute(json.dumps(args).encode())
        if not ret:
            return
        return json.loads(ret)

    async def receive(self, timeout=5):
        ret = await asyncio.to_thread(self.lib.td_receive, timeout)
        if not ret:
            return
        return json.loads(ret)

    async def send(self, args):
        if "@extra" not in args:
            self.rpc_id += 1
            args["@extra"] = self.rpc_id

        ft = self.once_future(f'rpc_{args["@extra"]}')
        data = json.dumps(args).encode()
        self.lib.td_send(self.client_id, data)
        _, ret = await ft
        return ret

    async def receive_loop(self, timeout=5):
        while True:
            upd = await self.receive(timeout)

            if not upd:
                continue

            if "@extra" in upd:
                self.emit(f'rpc_{upd["@extra"]}', upd)
            else:
                self.emit("update", upd)

    def log_enable(self, lvl=1023):
        callback_type = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p)
        self.log_callback = callback_type(self.log_cb)
        self.lib.td_set_log_message_callback(lvl, self.log_callback)

        return self.execute({
            "@type": "setLogVerbosityLevel",
            "new_verbosity_level": lvl,
        })

    def log_cb(self, lvl, msg):
        print("td_log", lvl, msg.decode())

    async def search_public_chat(self, username):
        ret = await self.send({
            "@type": "searchPublicChat",
            "username": username,
        })
        return ret.get("id")

    async def send_message(self, chat_id, msg):
        return await self.send({
            "@type": "sendMessage",
            "chat_id": chat_id,
            "input_message_content": {
                "@type": "inputMessageText",
                "text": {
                    "@type": "formattedText",
                    "text": msg,
                },
            },
        })

    async def login(self, conf):
        updates = asyncio.Queue()
        on_update = updates.put_nowait
        self.on("update", on_update)

        x.go(self.send({"@type": "getAuthorizationState"}))

        try:
            while True:
                upd = await updates.get()
                state = (upd.get("authorization_state") or {}).get("@type")

                if state == "authorizationStateWaitTdlibParameters":
                    x.go(self.send({
                        "@type": "setTdlibParameters",
                        "api_id": int(conf["api_id"]),
                        "api_hash": conf["api_hash"],
                        "database_directory": conf["tdlib_dir"],
                        "system_language_code": "en",
                        "use_message_database": True,
                        "use_secret_chats": True,
                        "device_model": "Desktop",
                        "application_version": "1.0",
                    }))

                elif state == "authorizationStateWaitPhoneNumber":
                    x.go(self.send({
                        "@type": "setAuthenticationPhoneNumber",
                        "phone_number": conf["tg_phone"],
                    }))

                elif state == "authorizationStateWaitCode":
                    x.go(self.send({
                        "@type": "checkAuthenticationCode",
                        "code": input("Code ? "),
                    }))

                elif state == "authorizationStateReady":
                    return upd

        finally:
            self.off("update", on_update)

    async def close(self):
        self.task.cancel()
