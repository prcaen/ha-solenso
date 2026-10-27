<p align="center">
  <img src="custom_components/solenso/brand/icon@2x.png" alt="" width="128">
</p>

<h1 align="center">Solenso pour Home Assistant</h1>

<p align="center">
  <a href="https://github.com/pismache/ha-solenso/releases"><img src="https://img.shields.io/github/v/release/pismache/ha-solenso?label=version" alt="Version"></a>
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-d%C3%A9p%C3%B4t%20personnalis%C3%A9-41BDF5" alt="HACS"></a>
  <a href="https://github.com/pismache/ha-solenso/actions/workflows/validate.yml"><img src="https://github.com/pismache/ha-solenso/actions/workflows/validate.yml/badge.svg" alt="Validation"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/pismache/ha-solenso" alt="Licence"></a>
</p>

Intégration non officielle pour les centrales photovoltaïques **Solenso** suivies sur [monitor.solenso.net](https://monitor.solenso.net) : production de la centrale et détail de chaque micro-onduleur, directement dans Home Assistant et son tableau Énergie.

> Solenso est une marque de micro-onduleurs basée sur la plateforme Hoymiles. Ce projet n'est ni affilié ni soutenu par Solenso ou Hoymiles.

## Installation

### Avec HACS (recommandé)

[![Ouvrir dans HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pismache&repository=ha-solenso&category=integration)

Ou à la main : HACS → menu ⋮ → **Dépôts personnalisés** → `https://github.com/pismache/ha-solenso`, catégorie **Intégration** → installer **Solenso** → redémarrer Home Assistant.

### Manuelle

Copier le dossier `custom_components/solenso` dans `/config/custom_components/`, puis redémarrer Home Assistant.

## Configuration

[![Ajouter l'intégration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=solenso)

Paramètres → Appareils et services → **Ajouter une intégration** → **Solenso**, puis l'e-mail et le mot de passe du compte monitor.solenso.net.

Les centrales du compte sont détectées automatiquement. Sinon, renseigner l'**identifiant de la centrale** : c'est le nombre après `id=` dans l'adresse de sa page (`…/station/view/detail?id=1234567`).

## Ce que vous obtenez

Les appareils sont organisés comme l'installation : **centrale → passerelle DTU → micro-onduleurs**.

### Centrale

| Capteur | Unité | Remarque |
|---|---|---|
| Puissance | W | 0 sans remontée depuis 1 h la nuit ; **indisponible** si les remontées s'arrêtent en plein jour |
| Production du jour / du mois / de l'année | kWh | |
| Production totale | kWh | recalculée par le cloud une fois par nuit |
| Production cumulée | kWh | **à utiliser dans le tableau Énergie** : suit la production heure par heure |
| Dernière remontée | horodatage | diagnostic |

Avec un **compteur d'achat/vente** installé (page « Contrôle du reflux » du cloud Solenso), la centrale expose en plus :

| Capteur | Unité | Remarque |
|---|---|---|
| Puissance consommée | W | consommation instantanée de la maison |
| Puissance réseau | W | négative en injection |
| Achat réseau du jour / du mois / de l'année / total | kWh | énergie achetée au réseau |
| Vente réseau du jour / du mois / de l'année / total | kWh | énergie injectée au réseau |

Ces capteurs apparaissent seuls : sans compteur, rien n'est créé ; un compteur ajouté plus tard est détecté au bout de quelques minutes (rechargement automatique de l'intégration).

### Micro-onduleurs

| Capteur | Unité | Remarque |
|---|---|---|
| Puissance | W | |
| Production du jour | kWh | |
| Tension panneau / Courant panneau | V / A | par entrée PV si l'onduleur en a plusieurs |
| Température | °C | |
| Tension réseau / Fréquence réseau | V / Hz | diagnostic |
| Dernière remontée | horodatage | diagnostic |

Les mesures des onduleurs arrivent par pas de 15 minutes. Sans nouveau point depuis 45 minutes, puissance, tension et courant passent à 0 la nuit, mais deviennent **indisponibles** en plein jour (soleil à plus de 10° au-dessus de l'horizon) : un onduleur muet sous le soleil signale une coupure, pas une production nulle. La température devient inconnue ; la production du jour reste acquise jusqu'à minuit.

### Passerelle DTU

| Capteur | Remarque |
|---|---|
| Connexion au cloud | la DTU communique-t-elle avec les serveurs ? |

## Carte « Panneau solaire »

L'intégration fournit sa propre carte et l'ajoute elle-même aux ressources des tableaux de bord (Paramètres → Tableaux de bord → Ressources) : rien à installer en plus. Elle la retire si l'intégration est supprimée.

Dans un tableau de bord : **Ajouter une carte** → chercher **Solenso**. Le panneau s'éclaire selon la production de chaque panneau PV ; un panneau sombre produit moins que les autres. Un clic sur un panneau, la puissance ou un chiffre de la plaque ouvre l'historique correspondant.

```yaml
type: custom:solenso-card
device_id: …          # la centrale (choisie dans l'éditeur visuel)
columns: 8            # facultatif : grille simple au lieu du plan Solenso
peak_power: 350       # puissance crête d'un panneau, pour l'éclairage
show_panels: true     # false : un seul grand panneau pour toute la centrale
show_values: true     # watts affichés sur chaque panneau
```

Les panneaux sont placés **comme sur le toit**, d'après la page « Agencement » de monitor.solenso.net : l'intégration lit la position de chaque entrée PV (attributs `panels` du capteur de puissance de chaque micro-onduleur). **Une cellule par panneau PV**, même quand un micro-onduleur en alimente plusieurs : compteur de panneaux, puissance affichée et éclairage sont ceux de chaque panneau. Avec plusieurs toitures, chacune forme un bloc. Les panneaux sans position, ou toute la centrale si l'agencement n'est pas renseigné chez Solenso, sont rangés par nom d'appareil. Indiquer `columns` impose une grille simple à la place du plan.

## Tableau Énergie

Dans Paramètres → Tableaux de bord → Énergie → **Production solaire**, choisir la statistique **« <centrale> Production »** (identifiant `solenso:production_<n° de centrale>`), et la **Puissance** de la centrale pour la puissance instantanée.

Cette statistique est écrite heure par heure par l'intégration, comme le fait l'add-on Linky pour le compteur, à partir des mesures par onduleur que le cloud garde par pas de 15 minutes :

- **Données en retard.** Si la passerelle DTU perd Internet, elle garde ses mesures et les renvoie plus tard. L'intégration relit la veille toutes les heures et réécrit les deux dernières journées : l'énergie arrivée en différé s'inscrit à l'heure où elle a été produite, et l'autoconsommation reste juste.
- **Historique.** Au premier lancement, l'historique conservé par le cloud est repris jour par jour (au plus un an, à raison d'une requête par seconde). Le journal indique la date de départ : « Historique de production repris depuis le … ».
- **Arrêt de Home Assistant.** Au redémarrage, les journées manquées sont relues (jusqu'à 31 jours).

Le capteur **Production cumulée** reste disponible, mais ne peut pas corriger le passé : après une coupure de la DTU, il manque la production arrivée en différé. La statistique le remplace dans le tableau Énergie.

## Fonctionnement

- Données lues dans le cloud toutes les 5 minutes (pas d'accès local à la DTU).
- Connexion comme la page web : le mot de passe est envoyé haché, jamais en clair. Le jeton de session est renouvelé automatiquement ; si le mot de passe change, Home Assistant propose de le ressaisir.
- Les données par onduleur viennent de l'API Hoymiles utilisée par la page « Agencement » de monitor.solenso.net (réponse protobuf décodée dans `module_pb.py`).

## Développement

```bash
pip install -r requirements-test.txt
pytest
```

Les versions sont listées dans le [journal des versions](CHANGELOG.md).
