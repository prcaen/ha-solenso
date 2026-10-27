"""Tests de l'intégration Solenso contre une API simulée."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solenso.api import hash_password
from custom_components.solenso.const import BASE_URL, CONF_STATIONS, DOMAIN, HOYMILES_URL

LOGIN = f"{BASE_URL}/iam/auth_login"
STATIONS = f"{BASE_URL}/pvm/station_select_by_page"
REAL = f"{BASE_URL}/pvm-data/data_count_station_real_data"

EMPTY_LIST = {"status": "0", "message": "success", "data": {"list": []}}


def mock_no_devices(aioclient_mock):
    aioclient_mock.post(f"{HOYMILES_URL}/pvm/api/0/dev/micro/select_by_station", json=EMPTY_LIST)
    aioclient_mock.post(f"{HOYMILES_URL}/pvm/api/0/dev/dtu/select_by_station", json=EMPTY_LIST)
    aioclient_mock.post(f"{BASE_URL}/pvm/layout_select_all", json={"status": "0", "data": []})


OK_LOGIN = {"status": "0", "message": "success", "data": {"token": "tok123"}}
OK_STATIONS = {"status": "0", "data": {"list": [{"id": 1234567, "name": "Maison"}]}}


def real_data(minutes_ago: int = 2) -> dict:
    t = (dt_util.now() - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "status": "0",
        "message": "success",
        "data": {
            "real_power": "451.7",
            "today_eq": "14608.0",
            "month_eq": "112706",
            "year_eq": "5512271",
            "total_eq": "22411023",
            "data_time": t,
            "last_data_time": t,
        },
    }


def real_data_with_meter(minutes_ago: int = 2) -> dict:
    """Réponse du cloud avec le compteur d'achat/vente (contrôle du reflux)."""
    out = real_data(minutes_ago)
    out["data"]["is_reflux"] = 1
    out["data"]["reflux_station_data"] = {
        "pv_power": "451.7",
        "load_power": "380.5",
        "grid_power": "-71.2",
        "meter_location": 2,
        "self_eq": "8000.0",
        "meter_b_in_eq": "6200000",
        "meter_b_out_eq": "8400000",
        "mb_in_eq": {"today_eq": "2100.0", "month_eq": "45000", "year_eq": "220000", "total_eq": "6200000"},
        "mb_out_eq": {"today_eq": "12500.0", "month_eq": "200000", "year_eq": "900000", "total_eq": "8400000"},
    }
    return out


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def test_hash_matches_browser_format():
    h = hash_password("test")
    md5, sha = h.split(".")
    assert md5 == "098f6bcd4621d373cade4e832627b4f6"
    assert len(sha) == 44 and sha.endswith("=")


async def test_user_flow_discovers_station(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(LOGIN, json=OK_LOGIN)
    aioclient_mock.post(STATIONS, json=OK_STATIONS)
    aioclient_mock.post(REAL, json=real_data())

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.solenso.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "User@Example.com", CONF_PASSWORD: "secret"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Maison"
    assert result["data"][CONF_STATIONS] == [{"id": 1234567, "name": "Maison"}]
    assert result["result"].unique_id == "user@example.com"
    # Le mot de passe part hashé, jamais en clair.
    login_body = aioclient_mock.mock_calls[0][2]
    assert login_body["body"]["password"] == hash_password("secret")


async def test_user_flow_falls_back_to_manual_sid(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(LOGIN, json=OK_LOGIN)
    aioclient_mock.post(STATIONS, status=404)
    aioclient_mock.post(REAL, json=real_data())

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch("custom_components.solenso.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "a@b.fr", CONF_PASSWORD: "x", "sid": 1234567},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_STATIONS] == [{"id": 1234567, "name": "Centrale 1234567"}]


async def test_user_flow_bad_password(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(LOGIN, json={"status": "1", "message": "password error"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "a@b.fr", CONF_PASSWORD: "bad"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_no_station(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(LOGIN, json=OK_LOGIN)
    aioclient_mock.post(STATIONS, json={"status": "0", "data": {"list": []}})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "a@b.fr", CONF_PASSWORD: "x"}
    )
    assert result["errors"] == {"base": "no_station"}


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="a@b.fr",
        title="Maison",
        data={
            CONF_USERNAME: "a@b.fr",
            CONF_PASSWORD: "x",
            CONF_STATIONS: [{"id": 1234567, "name": "Maison"}],
        },
    )


async def test_sensors(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(LOGIN, json=OK_LOGIN)
    aioclient_mock.post(REAL, json=real_data())
    mock_no_devices(aioclient_mock)
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    states = {s.entity_id: s for s in hass.states.async_all("sensor")}
    print(sorted(states))
    power = states["sensor.maison_power"]
    assert float(power.state) == 451.7 and power.attributes["unit_of_measurement"] == "W"
    today = states["sensor.maison_energy_today"]
    assert today.attributes["unit_of_measurement"] == "kWh"
    assert round(float(today.state), 3) == 14.608
    total = states["sensor.maison_total_energy"]
    assert round(float(total.state), 3) == 22411.023
    assert total.attributes["state_class"] == "total_increasing"
    # Le token est bien transmis à l'appel de données.
    headers = next(c[3] for c in aioclient_mock.mock_calls if str(c[1]) == REAL)
    assert headers["token"] == "tok123" and "solenso_token=tok123" in headers["Cookie"]
    # Sans compteur d'achat/vente : pas de capteurs de consommation.
    assert "sensor.maison_consumed_power" not in states
    assert "sensor.maison_grid_import_today" not in states


async def test_consumption_sensors_with_meter(hass: HomeAssistant, aioclient_mock):
    """Le compteur d'achat/vente expose la consommation de la maison et le réseau."""
    aioclient_mock.post(LOGIN, json=OK_LOGIN)
    aioclient_mock.post(REAL, json=real_data_with_meter())
    mock_no_devices(aioclient_mock)
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    load = hass.states.get("sensor.maison_consumed_power")
    assert load is not None
    assert float(load.state) == 380.5 and load.attributes["unit_of_measurement"] == "W"
    assert float(hass.states.get("sensor.maison_grid_power").state) == -71.2

    imported = hass.states.get("sensor.maison_grid_import_today")
    assert imported.attributes["unit_of_measurement"] == "kWh"
    assert imported.attributes["state_class"] == "total_increasing"
    assert round(float(imported.state), 3) == 2.1
    assert round(float(hass.states.get("sensor.maison_total_grid_import").state), 1) == 6200.0
    exported = hass.states.get("sensor.maison_grid_export_today")
    assert round(float(exported.state), 2) == 12.5
    assert round(float(hass.states.get("sensor.maison_total_grid_export").state), 1) == 8400.0


async def test_power_zero_when_data_stale(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(LOGIN, json=OK_LOGIN)
    aioclient_mock.post(REAL, json=real_data(minutes_ago=600))
    mock_no_devices(aioclient_mock)
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.maison_power").state) == 0


async def test_relogin_on_expired_token(hass: HomeAssistant, aioclient_mock):
    from custom_components.solenso.api import SolensoApi
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    calls = {"n": 0}

    async def real_handler(method, url, data):
        from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

        calls["n"] += 1
        body = {"status": "100", "message": "token expired"} if calls["n"] == 1 else real_data()
        return AiohttpClientMockResponse(method, url, json=body)

    aioclient_mock.post(LOGIN, json=OK_LOGIN)
    aioclient_mock.post(REAL, side_effect=real_handler)
    api = SolensoApi(async_get_clientsession(hass), "a@b.fr", "x")
    data = await api.get_station_real_data(1234567)
    assert data["real_power"] == "451.7"
    logins = [c for c in aioclient_mock.mock_calls if str(c[1]) == LOGIN]
    assert len(logins) == 2


async def test_auth_failure_triggers_reauth(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(LOGIN, json={"status": "1", "message": "password error"})
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_cloud_down_retries(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(LOGIN, status=503)
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_reauth_flow_updates_password(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(LOGIN, json=OK_LOGIN)
    entry = _entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    with patch("custom_components.solenso.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "nouveau"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "nouveau"
