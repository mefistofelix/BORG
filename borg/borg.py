# py: smoothpy

import asyncio
import gc
import json
import os
import platform
import sys

import aiohttp
from . import x
from .cdp import cdp
from .tg import td
from .xdb import xdb

async def run(bd):
    chrome_udd = rf"{bd}/chrome_udd"
    c = cdp()

    try:
        await c.launch(chrome_udd)
        print("start")
        await c.task
    finally:
        await c.close()


    #await c.jrpc.req({"a": 1})
