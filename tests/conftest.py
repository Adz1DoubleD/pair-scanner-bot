from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from aiohttp import ClientSession, TCPConnector
from web3 import AsyncHTTPProvider, AsyncWeb3, Web3

import pair_scanner.w3 as w3_service
from pair_scanner import jobs
from pair_scanner.networks import NETWORKS
from pair_scanner.settings import OPTION_DEFAULTS, OPTIONS, SETTINGS


def utc(year, month, day, hour=12):
    return datetime(year, month, day, hour, tzinfo=ZoneInfo("UTC"))


def freeze_datetime(monkeypatch, fixed_utc, module):
    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz else fixed_utc

    monkeypatch.setattr(module, "datetime", FakeDatetime)


def make_ctx(job=True, data=None, own_jq=True):
    return SimpleNamespace(
        job=SimpleNamespace(data=data, chat_id=555) if job else None,
        job_queue=SimpleNamespace(run_once=MagicMock()) if own_jq else None,
        application=SimpleNamespace(
            create_task=MagicMock(),
            job_queue=SimpleNamespace(run_once=MagicMock()),
        ),
        bot=SimpleNamespace(send_message=AsyncMock()),
    )


class FakeCursor:
    def __init__(
        self, *, fetchone=None, fetchall=None, rowcount=1, lastrowid=7, error=None
    ):
        self.fetchone_values = list(fetchone or [])
        self.fetchall_values = list(fetchall or [])
        self.rowcount = rowcount
        self.lastrowid = lastrowid
        self.error = error
        self.calls = []
        self._fetchone_index = 0
        self._fetchall_index = 0
        self.execute = AsyncMock(side_effect=self._execute)
        self.fetchone = AsyncMock(side_effect=self._fetchone)
        self.fetchall = AsyncMock(side_effect=self._fetchall)

    async def _execute(self, *args):
        self.calls.append(args)
        if self.error:
            raise self.error

    async def _fetchone(self):
        if self._fetchone_index >= len(self.fetchone_values):
            return None
        value = self.fetchone_values[self._fetchone_index]
        self._fetchone_index += 1
        if isinstance(value, Exception):
            raise value
        return value

    async def _fetchall(self):
        if self._fetchall_index >= len(self.fetchall_values):
            return []
        value = self.fetchall_values[self._fetchall_index]
        self._fetchall_index += 1
        if isinstance(value, Exception):
            raise value
        return value


class FakeSession:
    def __init__(self, cursor):
        self.cursor = cursor

    async def __aenter__(self):
        return self.cursor

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __call__(self):
        return self


@pytest.fixture
def make_cursor():
    return FakeCursor


@pytest.fixture
def patch_session(monkeypatch):
    def _patch(module, cursor):
        session = FakeSession(cursor)
        monkeypatch.setattr(module, "db_session", session)
        return cursor

    return _patch


class FakeJob:
    def __init__(self, name, next_t=None):
        self.name = name
        self.next_t = next_t
        self.removed = False

    def schedule_removal(self):
        self.removed = True


class FakeJobQueue:
    def __init__(self):
        self._jobs = []
        self.calls = []

    def add_existing(self, name, next_t=None):
        job = FakeJob(name, next_t)
        self._jobs.append(job)
        return job

    def _record(self, kind, name, **kw):
        self._jobs.append(FakeJob(name))
        self.calls.append(SimpleNamespace(kind=kind, name=name, **kw))

    def run_repeating(self, callback, interval, first=None, name=None, data=None):
        self._record(
            "repeating",
            name,
            callback=callback,
            interval=interval,
            first=first,
            data=data,
        )

    def run_daily(self, callback, time, name=None, data=None):
        self._record("daily", name, callback=callback, time=time, data=data)

    def run_once(self, callback, when, chat_id=None, name=None, data=None):
        self._record(
            "once", name, callback=callback, when=when, chat_id=chat_id, data=data
        )

    def get_jobs_by_name(self, name):
        return [j for j in self._jobs if j.name == name and not j.removed]

    def jobs(self):
        return [j for j in self._jobs if not j.removed]

    def get(self, name):
        return next(c for c in reversed(self.calls) if c.name == name)


@pytest.fixture
def jq():
    return FakeJobQueue()


TEST_KEY = "0x" + "11" * 32
SAMPLE_ADDR = "0x4200000000000000000000000000000000000006"
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WETH = "0x4200000000000000000000000000000000000006"
AERO_FACTORY = NETWORKS["Base"]["aero_factory"]


async def try_urls(urls, coro_fn):
    last_err = None
    for url in urls:
        w3 = AsyncWeb3(AsyncHTTPProvider(url, request_kwargs={"timeout": 20}))
        await w3.provider.cache_async_session(ClientSession(connector=TCPConnector()))
        try:
            return await coro_fn(w3)
        except Exception as e:
            last_err = e
        finally:
            await w3.provider.disconnect()
    raise last_err or RuntimeError("no urls")


def base_urls():
    conf = NETWORKS["Base"]
    return conf, [conf["rpc"]["url"]] + [
        f["url"] for f in conf.get("rpc_fallbacks", [])
    ]


def contract_at(abi):
    w3 = AsyncWeb3(AsyncHTTPProvider("https://example.invalid"))
    return w3.eth.contract(address=Web3.to_checksum_address(SAMPLE_ADDR), abi=abi)


def addr_word(addr):
    return "00" * 12 + addr[2:]


@pytest.fixture(autouse=True)
def scanner_settings(monkeypatch):
    for name, value in {
        "token": "test-token",
        "owner_id": "1",
        "alerts_chat_id": "-100",
        "db_url": None,
        "wallet_address": "0x" + "22" * 20,
        "private_key": TEST_KEY,
        "timezone": "Europe/London",
        "test_mode": False,
        "mode": None,
        "env_prefix": "",
    }.items():
        monkeypatch.setattr(SETTINGS, name, value)
    OPTIONS.clear()
    OPTIONS.update(OPTION_DEFAULTS, mode=2)
    jobs.REPORTED.clear()
    yield SETTINGS
    OPTIONS.clear()
    OPTIONS.update(OPTION_DEFAULTS)


@pytest.fixture(autouse=True)
async def _close_w3():
    yield
    await w3_service.close_w3()
