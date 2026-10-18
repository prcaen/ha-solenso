"""Tests des micro-onduleurs : décodage protobuf, capteurs, DTU, onduleurs endormis."""

from datetime import date, datetime, timedelta
import struct

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solenso.const import BASE_URL, CONF_STATIONS, DOMAIN, HOYMILES_URL
from custom_components.solenso.coordinator import micro_state
from custom_components.solenso.module_pb import decode_module_day_data

LOGIN = f"{BASE_URL}/iam/auth_login"
REAL = f"{BASE_URL}/pvm-data/data_count_station_real_data"
MICROS = f"{HOYMILES_URL}/pvm/api/0/dev/micro/select_by_station"
DTUS = f"{HOYMILES_URL}/pvm/api/0/dev/dtu/select_by_station"
MODULE = f"{HOYMILES_URL}/pvm-data/api/0/module/data/down_module_day_data"
LAYOUT = f"{BASE_URL}/pvm/layout_select_all"
SID = 42

# --- Encodeur protobuf minimal pour fabriquer des réponses de test -------------


def _varint(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def _key(num: int, wire: int) -> bytes:
    return _varint(num << 3 | wire)


def _ld(num: int, payload: bytes) -> bytes:
    return _key(num, 2) + _varint(len(payload)) + payload


def _f32(num: int, value: float) -> bytes:
    return b"" if value == 0 else _key(num, 5) + struct.pack("<f", value)


def _packed(num: int, values: list[float]) -> bytes:
    return _ld(num, struct.pack(f"<{len(values)}f", *values))


def encode_micro(micro_id: int, times: list[str], ports: dict[int, list[tuple]], temp, grid_v, freq) -> bytes:
    day = _key(1, 0) + _varint(1)
    day += b"".join(_ld(2, t.encode()) for t in times)
    for port, points in ports.items():
        series = b"".join(
            _ld(1, _f32(1, v) + _f32(2, i) + _f32(3, p) + _f32(4, e) + _key(5, 0) + _varint(1))
            for v, i, p, e in points
        )
        day += _ld(4, _key(1, 0) + _varint(port) + _ld(2, series))
    day += _packed(5, freq) + _packed(6, temp) + _packed(7, grid_v)
    return _ld(3, _key(1, 0) + _varint(micro_id) + _ld(2, day))


def encode_day(*micros: bytes) -> bytes:
    return _key(1, 0) + _varint(SID) + _ld(2, b"2026-10-09") + b"".join(micros)


def slots(n: int, minutes_ago: int = 5) -> list[str]:
    """n créneaux de 15 min se terminant il y a `minutes_ago` minutes."""
    last = dt_util.now() - timedelta(minutes=minutes_ago)
    return [(last - timedelta(minutes=15 * k)).strftime("%H:%M") for k in reversed(range(n))]


# --- Décodage -----------------------------------------------------------------


def test_decode_roundtrip():
    raw = encode_day(
        encode_micro(
            7,
            ["08:30", "08:45"],
            {1: [(28.0, 0.08, 2.3, 0.0), (31.3, 0.17, 5.3, 1.0)]},
            [12.2, 12.6],
            [228.7, 229.2],
            [49.99, 49.97],
        )
    )
    day = decode_module_day_data(raw)[7]
    assert day.times == ["08:30", "08:45"]
    last = day.ports[1][-1]
    assert (last.voltage, last.current, last.power, last.energy) == (31.3, 0.17, 5.3, 1.0)
    assert day.temperature[-1] == 12.6 and day.grid_voltage[-1] == 229.2
    assert day.grid_frequency[-1] == 49.97
    # Valeur nulle omise par l'encodeur = 0.
    assert day.ports[1][0].energy == 0.0


def test_decode_two_ports():
    raw = encode_day(
        encode_micro(
            8, ["12:00"], {1: [(35.0, 8.0, 280.0, 900.0)], 2: [(34.0, 7.5, 255.0, 850.0)]},
            [30.0], [231.0], [50.0],
        )
    )
    state = micro_state(decode_module_day_data(raw)[8], date.today(), datetime.combine(
        date.today(), datetime.min.time().replace(hour=12, minute=5), tzinfo=dt_util.get_default_time_zone()
    ))
    assert state.power == 535.0 and state.energy == 1750.0
    assert state.ports[2].voltage == 34.0


def test_garbage_raises():
    from custom_components.solenso.module_pb import ModuleDataDecodeError

    with pytest.raises(ModuleDataDecodeError):
        decode_module_day_data(b"\x1a\xff\xff\xff\x0f")


def test_micro_asleep_keeps_energy_and_zeroes_power():
    raw = encode_day(encode_micro(7, ["18:00"], {1: [(30.0, 1.0, 30.0, 950.0)]}, [20.0], [230.0], [50.0]))
    day = decode_module_day_data(raw)[7]
    tz = dt_util.get_default_time_zone()
    night = datetime.combine(date.today(), datetime.min.time().replace(hour=22), tzinfo=tz)
    state = micro_state(day, date.today(), night)
    assert state.asleep
    assert state.power == 0 and state.ports[1].voltage == 0
    assert state.energy == 950.0
    assert state.temperature is None


def test_micro_without_data_yet():
    state = micro_state(None, date.today(), dt_util.now())
    assert state.power == 0 and state.energy == 0 and state.last_update is None


# --- Intégration complète -------------------------------------------------------

MICRO_LIST = {
    "status": "0",
    "message": "success",
    "data": {
        "list": [
            {"id": 101, "sn": "112100000001", "model_no": "Sol-H350", "init_soft_ver": "V01.00.14",
             "init_hard_ver": "H00.08.00", "dtu_id": 900, "dtu_sn": "10D300000009", "port_array": [1]},
            {"id": 102, "sn": "112100000002", "model_no": "Sol-H350", "init_soft_ver": "V01.00.14",
             "init_hard_ver": "H00.08.00", "dtu_id": 900, "dtu_sn": "10D300000009", "port_array": [1]},
        ]
    },
}
DTU_LIST = {
    "status": "0",
    "message": "success",
    "data": {"list": [{"id": 900, "sn": "10D300000009", "init_soft_ver": "V00.03.07",
                       "warn_data": {"connect": True, "warn": False}}]},
}


def _real() -> dict:
    t = (dt_util.now() - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
    return {"status": "0", "data": {"real_power": "11.4", "today_eq": "2.0", "month_eq": "1",
                                    "year_eq": "1", "total_eq": "1", "data_time": t}}


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="a@b.fr",
        data={CONF_USERNAME: "a@b.fr", CONF_PASSWORD: "x",
              CONF_STATIONS: [{"id": SID, "name": "Maison"}]},
    )


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


LAYOUT_LIST = {
    "status": "0",
    "message": "success",
    "data": [
        {"id": 1, "aid": 77, "dev_type": 3, "mi_id": 101, "mi_sn": "112100000001", "port": 1, "x": 1, "y": 7},
        {"id": 2, "aid": 77, "dev_type": 3, "mi_id": 102, "mi_sn": "112100000002", "port": 1, "x": 0, "y": 3},
    ],
}


async def _setup(hass, aioclient_mock, module_body: bytes | None, module_status: int = 200, layout=None):
    aioclient_mock.post(LOGIN, json={"status": "0", "data": {"token": "tok"}})
    if layout is not None:
        aioclient_mock.post(LAYOUT, **layout)
    aioclient_mock.post(REAL, json=_real())
    aioclient_mock.post(MICROS, json=MICRO_LIST)
    aioclient_mock.post(DTUS, json=DTU_LIST)
    aioclient_mock.post(
        MODULE, content=module_body or b"", status=module_status,
        headers={"Content-Type": "application/octet-stream;charset=utf-8"},
    )
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_micro_sensors_and_devices(hass: HomeAssistant, aioclient_mock):
    times = slots(2)
    body = encode_day(
        encode_micro(101, times, {1: [(28.0, 0.08, 2.3, 0.0), (31.3, 0.17, 5.3, 1.0)]},
                     [12.2, 12.6], [228.7, 229.2], [49.99, 49.97]),
        encode_micro(102, times, {1: [(28.0, 0.08, 2.0, 0.0), (31.5, 0.2, 6.1, 1.0)]},
                     [11.0, 11.1], [230.2, 230.6], [49.99, 49.97]),
    )
    entry = await _setup(hass, aioclient_mock, body, layout={"json": LAYOUT_LIST})
    assert entry.state is ConfigEntryState.LOADED
    # Position sur le plan et entrées PV exposées pour la carte.
    attrs = hass.states.get("sensor.micro_onduleur_112100000001_power").attributes
    assert attrs["panel_count"] == 1
    assert attrs["panels"] == [
        {"port": 1, "power": 5.3, "layout_row": 1, "layout_column": 7, "layout_array": 77}
    ]
    assert "panels" not in hass.states.get("sensor.micro_onduleur_112100000001_temperature").attributes

    states = {s.entity_id: s for s in hass.states.async_all()}
    print(sorted(k for k in states if "112100000001" in k or "dtu" in k))
    p1 = states["sensor.micro_onduleur_112100000001_power"]
    assert float(p1.state) == 5.3 and p1.attributes["unit_of_measurement"] == "W"
    assert float(states["sensor.micro_onduleur_112100000001_pv_voltage"].state) == 31.3
    assert float(states["sensor.micro_onduleur_112100000001_pv_current"].state) == 0.17
    assert float(states["sensor.micro_onduleur_112100000001_temperature"].state) == 12.6
    e1 = states["sensor.micro_onduleur_112100000001_energy_today"]
    assert e1.attributes["unit_of_measurement"] == "kWh" and float(e1.state) == 0.001
    assert float(states["sensor.micro_onduleur_112100000002_power"].state) == 6.1
    assert states["binary_sensor.dtu_10d300000009_cloud_connection"].state == "on"

    # Hiérarchie des appareils : centrale > DTU > onduleurs.
    reg = dr.async_get(hass)
    station = reg.async_get_device({(DOMAIN, str(SID))})
    dtu = reg.async_get_device({(DOMAIN, "dtu_10D300000009")})
    micro = reg.async_get_device({(DOMAIN, "micro_112100000001")})
    assert dtu.via_device_id == station.id
    assert micro.via_device_id == dtu.id
    assert micro.model == "Sol-H350" and micro.sw_version == "V01.00.14"
    # Le jeton part bien dans l'en-tête Authorization vers Hoymiles.
    module_call = next(c for c in aioclient_mock.mock_calls if str(c[1]) == MODULE)
    assert module_call[3]["Authorization"] == "tok"
    assert module_call[2] == {"sid": SID, "date": dt_util.now().date().isoformat()}


MICRO_LIST_MULTI = {
    "status": "0",
    "message": "success",
    "data": {
        "list": [
            {"id": 201, "sn": "112100000201", "model_no": "HMS-2000-4T", "init_soft_ver": "V01.00.14",
             "init_hard_ver": "H00.08.00", "dtu_id": 900, "dtu_sn": "10D300000009", "port_array": [1, 2]},
        ]
    },
}
LAYOUT_MULTI = {
    "status": "0",
    "message": "success",
    "data": [
        {"id": 1, "aid": 77, "dev_type": 3, "mi_id": 201, "mi_sn": "112100000201", "port": 1, "x": 0, "y": 0},
        {"id": 2, "aid": 77, "dev_type": 3, "mi_id": 201, "mi_sn": "112100000201", "port": 2, "x": 0, "y": 1},
    ],
}


async def test_multi_port_micro_one_panel_per_pv_input(hass: HomeAssistant, aioclient_mock):
    """Un onduleur à plusieurs entrées : la carte affiche un panneau par entrée PV."""
    body = encode_day(
        encode_micro(
            201, slots(1),
            {1: [(32.0, 4.0, 128.0, 300.0)], 2: [(33.0, 4.5, 148.5, 350.0)]},
            [30.0], [230.0], [50.0],
        )
    )
    aioclient_mock.post(LOGIN, json={"status": "0", "data": {"token": "tok"}})
    aioclient_mock.post(LAYOUT, json=LAYOUT_MULTI)
    aioclient_mock.post(REAL, json=_real())
    aioclient_mock.post(MICROS, json=MICRO_LIST_MULTI)
    aioclient_mock.post(DTUS, json=DTU_LIST)
    aioclient_mock.post(MODULE, content=body, headers={"Content-Type": "application/octet-stream;charset=utf-8"})
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    # Puissance de l'onduleur = somme de ses deux panneaux.
    assert float(hass.states.get("sensor.micro_onduleur_112100000201_power").state) == 276.5
    # Les panneaux de la carte : un par entrée, avec puissance et position propres.
    attrs = hass.states.get("sensor.micro_onduleur_112100000201_power").attributes
    assert attrs["panel_count"] == 2
    assert attrs["panels"] == [
        {"port": 1, "power": 128.0, "layout_row": 0, "layout_column": 0, "layout_array": 77},
        {"port": 2, "power": 148.5, "layout_row": 0, "layout_column": 1, "layout_array": 77},
    ]
    # Capteurs détaillés par entrée.
    assert float(hass.states.get("sensor.micro_onduleur_112100000201_pv_1_power").state) == 128.0
    assert float(hass.states.get("sensor.micro_onduleur_112100000201_pv_2_power").state) == 148.5
    assert float(hass.states.get("sensor.micro_onduleur_112100000201_pv_2_voltage").state) == 33.0


async def test_micro_data_failure_keeps_station_sensors(hass: HomeAssistant, aioclient_mock):
    entry = await _setup(hass, aioclient_mock, None, module_status=500, layout={"status": 500})
    assert entry.state is ConfigEntryState.LOADED
    assert float(hass.states.get("sensor.maison_power").state) == 11.4
    assert hass.states.get("sensor.micro_onduleur_112100000001_power").state == "unavailable"


async def test_micro_not_reporting_yet_reads_zero(hass: HomeAssistant, aioclient_mock):
    # Le matin, avant le premier point : onduleur présent mais sans série.
    body = encode_day(encode_micro(101, slots(1), {1: [(30.0, 0.1, 3.0, 0.0)]}, [10.0], [230.0], [50.0]))
    await _setup(hass, aioclient_mock, body, layout={"json": {"status": "0", "data": []}})
    assert float(hass.states.get("sensor.micro_onduleur_112100000002_power").state) == 0
    attrs = hass.states.get("sensor.micro_onduleur_112100000002_power").attributes
    assert attrs["panel_count"] == 1 and attrs["panels"][0]["layout_row"] is None
    assert float(hass.states.get("sensor.micro_onduleur_112100000002_energy_today").state) == 0
    assert hass.states.get("sensor.micro_onduleur_112100000002_temperature").state == "unknown"


async def test_expired_token_on_hoymiles_relogs(hass: HomeAssistant, aioclient_mock):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

    from custom_components.solenso.api import SolensoApi

    calls = {"n": 0}

    async def module(method, url, data):
        calls["n"] += 1
        if calls["n"] == 1:
            return AiohttpClientMockResponse(method, url, json={"status": "100", "message": "token"})
        return AiohttpClientMockResponse(
            method, url,
            response=encode_day(encode_micro(101, ["09:00"], {1: [(30.0, 0.1, 3.0, 1.0)]}, [1.0], [2.0], [3.0])),
            headers={"Content-Type": "application/octet-stream"},
        )

    aioclient_mock.post(LOGIN, json={"status": "0", "data": {"token": "tok"}})
    aioclient_mock.post(MODULE, side_effect=module)
    api = SolensoApi(async_get_clientsession(hass), "a@b.fr", "x")
    days = await api.get_module_day_data(SID, "2026-10-09")
    assert days[101].ports[1][-1].power == 3.0
    assert sum(1 for c in aioclient_mock.mock_calls if str(c[1]) == LOGIN) == 2
