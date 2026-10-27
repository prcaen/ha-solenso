"""Capteurs Solenso : centrale et micro-onduleurs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import STALE_DATA_DELAY
from .coordinator import (
    MicroState,
    PortState,
    SolensoConfigEntry,
    SolensoCoordinator,
    StationData,
)
from .entity import link_only, micro_device, station_device

# --- Centrale ----------------------------------------------------------------


def _float(data: dict[str, Any], key: str) -> float | None:
    try:
        return float(data[key])
    except (KeyError, TypeError, ValueError):
        return None


def _last_update(data: dict[str, Any]) -> datetime | None:
    """Horodatage de la dernière remontée (heure locale de la centrale)."""
    raw = data.get("last_data_time") or data.get("data_time")
    if not raw:
        return None
    parsed = dt_util.parse_datetime(str(raw))
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return parsed


def _is_stale(data: dict[str, Any]) -> bool:
    """La dernière remontée de la centrale est-elle trop ancienne ?"""
    last = _last_update(data)
    return last is not None and dt_util.now() - last > STALE_DATA_DELAY


def _power(data: dict[str, Any]) -> float | None:
    """Puissance instantanée, nulle quand la dernière remontée est trop ancienne (la nuit)."""
    if _is_stale(data):
        return 0.0
    return _float(data, "real_power")


def _reflux(data: dict[str, Any]) -> dict[str, Any]:
    """Données du compteur d'achat/vente (page « Contrôle du reflux » du cloud)."""
    reflux = data.get("reflux_station_data")
    return reflux if isinstance(reflux, dict) else {}


def _reflux_power(key: str) -> Callable[[dict[str, Any]], float | None]:
    """Puissance instantanée du compteur, nulle quand la remontée est trop ancienne."""

    def value_fn(data: dict[str, Any]) -> float | None:
        if _is_stale(data):
            return 0.0
        return _float(_reflux(data), key)

    return value_fn


@dataclass(frozen=True, kw_only=True)
class SolensoSensorDescription(SensorEntityDescription):
    """Description d'un capteur de centrale."""

    value_fn: Callable[[dict[str, Any]], Any]
    # Mesure instantanée : indisponible en plein jour quand les remontées sont figées.
    live: bool = False


def _energy(key: str, translation_key: str, precision: int) -> SolensoSensorDescription:
    return SolensoSensorDescription(
        key=translation_key,
        translation_key=translation_key,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=precision,
        value_fn=lambda d: _float(d, key),
    )


SENSORS: tuple[SolensoSensorDescription, ...] = (
    SolensoSensorDescription(
        key="power",
        translation_key="power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_power,
        live=True,
    ),
    _energy("today_eq", "energy_today", 2),
    _energy("month_eq", "energy_month", 1),
    _energy("year_eq", "energy_year", 0),
    _energy("total_eq", "energy_total", 0),
    SolensoSensorDescription(
        key="last_update",
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_update,
    ),
)


def _meter_energy(group: str, key: str, translation_key: str, precision: int) -> SolensoSensorDescription:
    """Énergie du compteur d'achat/vente : mb_in_eq (achat) ou mb_out_eq (vente)."""
    return SolensoSensorDescription(
        key=translation_key,
        translation_key=translation_key,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=precision,
        value_fn=lambda d: _float(_reflux(d).get(group) or {}, key),
    )


# Compteur d'achat/vente installé sur la centrale (contrôle du reflux) :
# consommation de la maison, puissance réseau et énergies achetées / vendues.
REFLUX_SENSORS: tuple[SolensoSensorDescription, ...] = (
    SolensoSensorDescription(
        key="load_power",
        translation_key="load_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_reflux_power("load_power"),
        live=True,
    ),
    SolensoSensorDescription(
        key="grid_power",
        translation_key="grid_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_reflux_power("grid_power"),
        live=True,
    ),
    _meter_energy("mb_in_eq", "today_eq", "grid_import_today", 2),
    _meter_energy("mb_in_eq", "month_eq", "grid_import_month", 1),
    _meter_energy("mb_in_eq", "year_eq", "grid_import_year", 0),
    _meter_energy("mb_in_eq", "total_eq", "grid_import_total", 0),
    _meter_energy("mb_out_eq", "today_eq", "grid_export_today", 2),
    _meter_energy("mb_out_eq", "month_eq", "grid_export_month", 1),
    _meter_energy("mb_out_eq", "year_eq", "grid_export_year", 0),
    _meter_energy("mb_out_eq", "total_eq", "grid_export_total", 0),
)

# --- Compteur pour le tableau Énergie ----------------------------------------
#
# Le cloud ne recalcule les totaux (mois, année, total) qu'une fois par nuit :
# branchés sur le tableau Énergie, ils font apparaître toute la production de la
# veille d'un coup, vers 2 h du matin. La production du jour, elle, suit les
# remontées des onduleurs tout au long de la journée. Ce compteur additionne ses
# hausses successives : il ne fait que monter, passe minuit sans à-coup et
# rattrape ce qui a été produit pendant un redémarrage ou une coupure du cloud.


@dataclass
class Counter:
    """État du compteur : total cumulé et dernière production du jour vue."""

    total: float | None = None
    last_today: float = 0.0
    last_day: date | None = None


def accumulate(counter: Counter, today: float | None, day: date | None) -> Counter:
    """Ajouter au total ce que la production du jour a gagné depuis la dernière lecture."""
    if today is None or day is None or today < 0:
        return counter
    if counter.total is None or counter.last_day is None:
        # Première lecture : point de départ, rien n'est compté en double.
        total = counter.total if counter.total is not None else today
        return Counter(total, today, day)
    if day == counter.last_day:
        if today > counter.last_today:
            return Counter(counter.total + today - counter.last_today, today, day)
        # Baisse passagère ou correction du cloud : on attend qu'il dépasse l'ancien maximum.
        return counter
    if day > counter.last_day:
        if today == counter.last_today and today > 0:
            # Nouvelle journée mais valeur de la veille pas encore remise à zéro.
            return counter
        return Counter(counter.total + today, today, day)
    return counter


@dataclass
class CounterStoredData(ExtraStoredData):
    """Ce qu'il faut garder d'un redémarrage à l'autre."""

    counter: Counter

    def as_dict(self) -> dict[str, Any]:
        c = self.counter
        return {
            "total": c.total,
            "last_today": c.last_today,
            "last_day": c.last_day.isoformat() if c.last_day else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CounterStoredData | None:
        try:
            total = data.get("total")
            last_day = data.get("last_day")
            return cls(
                Counter(
                    float(total) if total is not None else None,
                    float(data.get("last_today") or 0.0),
                    date.fromisoformat(last_day) if last_day else None,
                )
            )
        except (TypeError, ValueError):
            return None


class SolensoProductionCounter(CoordinatorEntity[SolensoCoordinator], SensorEntity, RestoreEntity):
    """Production cumulée, mise à jour au fil de la journée (pour le tableau Énergie)."""

    _attr_has_entity_name = True
    _attr_translation_key = "energy_production"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_suggested_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: SolensoCoordinator, station: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._sid: int = station["id"]
        self._attr_unique_id = f"{self._sid}_energy_production"
        self._attr_device_info = station_device(station)
        self._counter = Counter()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (extra := await self.async_get_last_extra_data()) is not None:
            if (stored := CounterStoredData.from_dict(extra.as_dict())) is not None:
                self._counter = stored.counter
        self._update_counter()

    @property
    def extra_restore_state_data(self) -> CounterStoredData:
        return CounterStoredData(self._counter)

    def _update_counter(self) -> None:
        station = (self.coordinator.data or {}).get(self._sid)
        if station is None:
            return
        last = _last_update(station.real)
        self._counter = accumulate(
            self._counter, _float(station.real, "today_eq"), last.date() if last else None
        )

    def _handle_coordinator_update(self) -> None:
        self._update_counter()
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        return self._counter.total is not None

    @property
    def native_value(self) -> float | None:
        total = self._counter.total
        return None if total is None else round(total, 1)


# --- Micro-onduleurs ---------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class MicroSensorDescription(SensorEntityDescription):
    """Description d'un capteur de micro-onduleur."""

    value_fn: Callable[[MicroState], Any]
    live: bool = False


@dataclass(frozen=True, kw_only=True)
class PortSensorDescription(SensorEntityDescription):
    """Description d'un capteur par entrée PV."""

    value_fn: Callable[[PortState], Any]
    live: bool = True


MICRO_SENSORS: tuple[MicroSensorDescription, ...] = (
    MicroSensorDescription(
        key="power",
        translation_key="power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda m: m.power,
        live=True,
    ),
    MicroSensorDescription(
        key="energy_today",
        translation_key="energy_today",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value_fn=lambda m: m.energy,
    ),
    MicroSensorDescription(
        key="temperature",
        translation_key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda m: m.temperature,
    ),
    MicroSensorDescription(
        key="grid_voltage",
        translation_key="grid_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
        value_fn=lambda m: m.grid_voltage,
    ),
    MicroSensorDescription(
        key="grid_frequency",
        translation_key="grid_frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        value_fn=lambda m: m.grid_frequency,
    ),
    MicroSensorDescription(
        key="last_update",
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.last_update,
    ),
)

PORT_SENSORS: tuple[PortSensorDescription, ...] = (
    PortSensorDescription(
        key="pv_voltage",
        translation_key="pv_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda p: p.voltage,
    ),
    PortSensorDescription(
        key="pv_current",
        translation_key="pv_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda p: p.current,
    ),
)
# Avec plusieurs panneaux par onduleur, puissance et énergie sont aussi détaillées par entrée.
MULTI_PORT_SENSORS: tuple[PortSensorDescription, ...] = (
    PortSensorDescription(
        key="pv_power",
        translation_key="pv_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda p: p.power,
    ),
    PortSensorDescription(
        key="pv_energy",
        translation_key="pv_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value_fn=lambda p: p.energy,
        live=False,
    ),
)


def _ports(micro: dict[str, Any]) -> list[int]:
    ports = micro.get("port_array")
    if isinstance(ports, list) and ports:
        return [int(p) for p in ports]
    return [1]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolensoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Créer les capteurs de chaque centrale et de chaque micro-onduleur."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for station in coordinator.stations:
        sid = station["id"]
        entities.extend(SolensoSensor(coordinator, station, d) for d in SENSORS)
        entities.append(SolensoProductionCounter(coordinator, station))
        # Capteurs du compteur d'achat/vente, seulement s'il est installé sur la centrale.
        station_data = coordinator.data.get(sid)
        if station_data is not None and isinstance(station_data.real.get("reflux_station_data"), dict):
            entities.extend(SolensoSensor(coordinator, station, d) for d in REFLUX_SENSORS)
        for micro in coordinator.micros.get(sid, []):
            entities.extend(MicroSensor(coordinator, sid, micro, d) for d in MICRO_SENSORS)
            ports = _ports(micro)
            multi = len(ports) > 1
            for port in ports:
                for description in PORT_SENSORS + (MULTI_PORT_SENSORS if multi else ()):
                    entities.append(PortSensor(coordinator, sid, micro, port, description, multi))
    async_add_entities(entities)


class SolensoSensor(CoordinatorEntity[SolensoCoordinator], SensorEntity):
    """Capteur d'une centrale Solenso."""

    _attr_has_entity_name = True
    entity_description: SolensoSensorDescription

    def __init__(
        self,
        coordinator: SolensoCoordinator,
        station: dict[str, Any],
        description: SolensoSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._sid: int = station["id"]
        self._attr_unique_id = f"{self._sid}_{description.key}"
        self._attr_device_info = station_device(station)

    @property
    def _station(self) -> StationData | None:
        return (self.coordinator.data or {}).get(self._sid)

    @property
    def _data(self) -> dict[str, Any] | None:
        station = self._station
        return station.real if station else None

    @property
    def available(self) -> bool:
        station = self._station
        if not super().available or station is None:
            return False
        # En plein jour, une puissance figée n'est pas une puissance nulle.
        return not (self.entity_description.live and station.daylight and _is_stale(station.real))

    @property
    def native_value(self) -> Any:
        data = self._data
        return None if data is None else self.entity_description.value_fn(data)


class _MicroEntity(CoordinatorEntity[SolensoCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SolensoCoordinator, sid: int, micro: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._sid = sid
        self._micro_id: int = micro["id"]
        self._sn: str = micro.get("sn") or str(micro["id"])
        # Entrées PV de l'onduleur : une par panneau raccordé.
        self._ports: list[int] = _ports(micro)
        self._attr_device_info = link_only(micro_device(sid, micro))

    @property
    def _state(self) -> MicroState | None:
        station = (self.coordinator.data or {}).get(self._sid)
        return station.micros.get(self._micro_id) if station else None

    @property
    def available(self) -> bool:
        state = self._state
        if not super().available or state is None:
            return False
        # Onduleur muet alors que le soleil est haut : mesure inconnue, pas zéro.
        station = (self.coordinator.data or {}).get(self._sid)
        live = getattr(self.entity_description, "live", False)
        return not (live and state.asleep and station is not None and station.daylight)


class MicroSensor(_MicroEntity):
    """Capteur global d'un micro-onduleur."""

    entity_description: MicroSensorDescription

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        # Les entrées PV servent à la carte : une case par panneau, avec sa puissance
        # et sa position sur le plan (page Agencement), même avec n panneaux par onduleur.
        if self.entity_description.key != "power":
            return None
        state = self._state
        layout = self.coordinator.layout.get(self._sid, {}).get(self._micro_id, {})
        panels = []
        for port in self._ports:
            port_state = state.ports.get(port) if state else None
            position = layout.get(port)
            panels.append(
                {
                    "port": port,
                    "power": round(port_state.power, 2) if port_state else 0.0,
                    "layout_row": position["row"] if position else None,
                    "layout_column": position["column"] if position else None,
                    "layout_array": position["array"] if position else 0,
                }
            )
        return {"panel_count": len(panels), "panels": panels}

    def __init__(self, coordinator, sid, micro, description: MicroSensorDescription) -> None:
        super().__init__(coordinator, sid, micro)
        self.entity_description = description
        self._attr_unique_id = f"micro_{self._sn}_{description.key}"

    @property
    def native_value(self) -> Any:
        state = self._state
        return None if state is None else self.entity_description.value_fn(state)


class PortSensor(_MicroEntity):
    """Capteur d'une entrée PV d'un micro-onduleur."""

    entity_description: PortSensorDescription

    def __init__(self, coordinator, sid, micro, port: int, description, multi: bool) -> None:
        super().__init__(coordinator, sid, micro)
        self.entity_description = description
        self._port = port
        self._attr_unique_id = f"micro_{self._sn}_{description.key}_{port}"
        if multi:
            self._attr_translation_key = f"{description.translation_key}_port"
            self._attr_translation_placeholders = {"port": str(port)}

    @property
    def native_value(self) -> Any:
        state = self._state
        if state is None:
            return None
        port = state.ports.get(self._port)
        if port is None:
            # Pas encore de mesure aujourd'hui : panneau à l'arrêt.
            return 0.0
        return self.entity_description.value_fn(port)
