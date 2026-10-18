"""Coordinateur de mise à jour Solenso."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import logging
from typing import Any

from astral import Observer
from astral.sun import elevation as solar_elevation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SolensoApi, SolensoAuthError, SolensoError
from .const import (
    CONF_STATIONS,
    DAYLIGHT_ELEVATION,
    DEFAULT_SCAN_INTERVAL,
    DEVICES_REFRESH_INTERVAL,
    DOMAIN,
    MICRO_STALE_DELAY,
    PREVIOUS_DAY_REFRESH_INTERVAL,
)
from .module_pb import MicroDay
from .statistics import ProductionStatistics

_LOGGER = logging.getLogger(__name__)

type SolensoConfigEntry = ConfigEntry[SolensoCoordinator]


@dataclass
class PortState:
    """Dernières valeurs d'une entrée PV (un panneau)."""

    power: float = 0.0
    voltage: float = 0.0
    current: float = 0.0
    energy: float = 0.0


@dataclass
class MicroState:
    """Dernières valeurs d'un micro-onduleur."""

    last_update: datetime | None = None
    asleep: bool = True
    ports: dict[int, PortState] = field(default_factory=dict)
    temperature: float | None = None
    grid_voltage: float | None = None
    grid_frequency: float | None = None

    @property
    def power(self) -> float:
        return round(sum(p.power for p in self.ports.values()), 2)

    @property
    def energy(self) -> float:
        return round(sum(p.energy for p in self.ports.values()), 2)


@dataclass
class StationData:
    """Données d'une centrale."""

    real: dict[str, Any]
    micros: dict[int, MicroState] = field(default_factory=dict)
    dtus: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Soleil assez haut pour que les panneaux produisent : une donnée figée est alors suspecte.
    daylight: bool = False
    # Mesures par onduleur du jour et de la veille ({jour: {id onduleur: série}}).
    days: dict[date, dict[int, MicroDay]] = field(default_factory=dict)


def _last(values: list[float], index: int) -> float | None:
    if not values:
        return None
    return values[min(index, len(values) - 1)]


def micro_state(day: MicroDay | None, day_date: date, now: datetime) -> MicroState:
    """Calculer l'état courant d'un onduleur à partir de sa série du jour."""
    if day is None or not day.times:
        return MicroState()
    index = len(day.times) - 1
    try:
        hour, minute = (int(x) for x in day.times[index].split(":")[:2])
        last_update = datetime.combine(day_date, time(hour, minute), tzinfo=now.tzinfo)
    except ValueError:
        last_update = None
    asleep = last_update is not None and now - last_update > MICRO_STALE_DELAY

    ports: dict[int, PortState] = {}
    for port, points in day.ports.items():
        if not points:
            continue
        point = points[min(index, len(points) - 1)]
        if asleep:
            # Un panneau endormi ne produit plus ; l'énergie du jour reste acquise.
            ports[port] = PortState(energy=point.energy)
        else:
            ports[port] = PortState(point.power, point.voltage, point.current, point.energy)

    return MicroState(
        last_update=last_update,
        asleep=asleep,
        ports=ports,
        temperature=None if asleep else _last(day.temperature, index),
        grid_voltage=None if asleep else _last(day.grid_voltage, index),
        grid_frequency=None if asleep else _last(day.grid_frequency, index),
    )


def is_daylight(hass: HomeAssistant, when: datetime) -> bool:
    """Le soleil est-il assez haut pour que les panneaux produisent ?"""
    try:
        observer = Observer(hass.config.latitude, hass.config.longitude, hass.config.elevation or 0)
        return solar_elevation(observer, when) > DAYLIGHT_ELEVATION
    except (ValueError, TypeError) as err:  # emplacement de Home Assistant non configuré
        _LOGGER.debug("Hauteur du soleil incalculable : %s", err)
        return False


def _positions(items: list[dict[str, Any]]) -> dict[int, dict[int, dict[str, int]]]:
    """Position de chaque entrée PV : {id onduleur: {port: {\"row\", \"column\", \"array\"}}}."""
    positions: dict[int, dict[int, dict[str, int]]] = {}
    for item in sorted(items, key=lambda i: (i.get("mi_id") or 0, i.get("port") or 0)):
        try:
            micro_id, row, column = int(item["mi_id"]), int(item["x"]), int(item["y"])
        except (KeyError, TypeError, ValueError):
            continue
        port = int(item.get("port") or 1)
        positions.setdefault(micro_id, {}).setdefault(
            port, {"row": row, "column": column, "array": int(item.get("aid") or 0)}
        )
    return positions


class SolensoCoordinator(DataUpdateCoordinator[dict[int, StationData]]):
    """Récupère les données de toutes les centrales du compte."""

    config_entry: SolensoConfigEntry

    def __init__(self, hass: HomeAssistant, entry: SolensoConfigEntry, api: SolensoApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.api = api
        self.stations: list[dict[str, Any]] = entry.data[CONF_STATIONS]
        # Inventaire des micro-onduleurs par centrale : {sid: [micro, ...]}
        self.micros: dict[int, list[dict[str, Any]]] = {}
        # Position de chaque entrée PV sur le plan : {sid: {id onduleur: {port: {"row", "column", "array"}}}}
        self.layout: dict[int, dict[int, dict[int, dict[str, int]]]] = {}
        self._micros_fetched_at: datetime | None = None
        self._module_warned = False
        # Dernière relecture de la veille : {sid: (jour, mesures, lue à)}
        self._previous_cache: dict[int, tuple[date, dict[int, MicroDay], datetime]] = {}
        # Production horaire dans les statistiques (tableau Énergie), une par centrale.
        self.statistics = {s["id"]: ProductionStatistics(hass, api, s) for s in self.stations}

    async def _refresh_inventory(self) -> None:
        now = dt_util.utcnow()
        if self._micros_fetched_at and now - self._micros_fetched_at < DEVICES_REFRESH_INTERVAL:
            return
        complete = True
        for station in self.stations:
            sid = station["id"]
            try:
                micros = await self.api.get_micros(sid)
            except SolensoAuthError:
                raise
            except SolensoError as err:
                # Retentée au prochain passage, pas dans 6 h : sans elle, aucune mesure par onduleur.
                _LOGGER.warning("Liste des micro-onduleurs indisponible : %s", err)
                self.micros.setdefault(sid, [])
                complete = False
            else:
                if micros and not self.micros.get(sid) and self.data is not None:
                    # Onduleurs découverts après le démarrage : recharger pour créer leurs capteurs.
                    _LOGGER.info("Micro-onduleurs disponibles, rechargement de l'intégration")
                    self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
                self.micros[sid] = micros
            try:
                self.layout[station["id"]] = _positions(await self.api.get_layout(station["id"]))
            except SolensoAuthError:
                raise
            except SolensoError as err:
                _LOGGER.debug("Disposition des panneaux indisponible : %s", err)
                self.layout.setdefault(station["id"], {})
        if complete:
            self._micros_fetched_at = now

    async def _previous_day(self, sid: int, today: date) -> tuple[date, dict[int, MicroDay]] | None:
        """Mesures de la veille, relues de temps en temps (la DTU peut les envoyer en différé)."""
        yesterday = today - timedelta(days=1)
        now = dt_util.utcnow()
        cached = self._previous_cache.get(sid)
        if cached and cached[0] == yesterday and now - cached[2] < PREVIOUS_DAY_REFRESH_INTERVAL:
            return cached[0], cached[1]
        try:
            days = await self.api.get_module_day_data(sid, yesterday.isoformat())
        except SolensoAuthError:
            raise
        except SolensoError as err:
            _LOGGER.debug("Mesures de la veille indisponibles : %s", err)
            return (cached[0], cached[1]) if cached and cached[0] == yesterday else None
        self._previous_cache[sid] = (yesterday, days, now)
        return yesterday, days

    async def _station(self, sid: int, previous: StationData | None) -> StationData:
        now = dt_util.now()
        station = StationData(
            real=await self.api.get_station_real_data(sid), daylight=is_daylight(self.hass, now)
        )

        try:
            station.dtus = {d["id"]: d for d in await self.api.get_dtus(sid) if "id" in d}
        except SolensoAuthError:
            raise
        except SolensoError as err:
            _LOGGER.debug("État des DTU indisponible : %s", err)
            station.dtus = previous.dtus if previous else {}

        if not self.micros.get(sid):
            return station
        try:
            days = await self.api.get_module_day_data(sid, now.date().isoformat())
        except SolensoAuthError:
            raise
        except SolensoError as err:
            if not self._module_warned:
                _LOGGER.warning("Données par micro-onduleur indisponibles : %s", err)
                self._module_warned = True
            station.micros = previous.micros if previous else {}
            return station
        self._module_warned = False
        station.days[now.date()] = days
        if (prev := await self._previous_day(sid, now.date())) is not None:
            station.days[prev[0]] = prev[1]
        station.micros = {
            micro["id"]: micro_state(days.get(micro["id"]), now.date(), now)
            for micro in self.micros[sid]
        }
        return station

    async def _async_update_data(self) -> dict[int, StationData]:
        previous = self.data or {}
        data: dict[int, StationData] = {}
        try:
            await self._refresh_inventory()
            for station in self.stations:
                data[station["id"]] = await self._station(station["id"], previous.get(station["id"]))
        except SolensoAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SolensoError as err:
            raise UpdateFailed(str(err)) from err
        for sid, station in data.items():
            if not station.days:
                continue
            try:
                await self.statistics[sid].async_update(station.days)
            except SolensoAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except SolensoError as err:
                # Les capteurs restent à jour ; les statistiques seront réécrites au prochain passage.
                _LOGGER.debug("Statistiques de production non mises à jour : %s", err)
            except Exception:  # noqa: BLE001 - une erreur de la base ne doit pas couper les capteurs
                _LOGGER.exception("Échec de l'écriture des statistiques de production")
        return data
