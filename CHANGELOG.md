# Journal des versions

## 0.7.0 — 2026-10-27

- **Consommation de la maison** : avec un compteur d'achat/vente installé (page « Contrôle du reflux » du cloud Solenso), la centrale expose désormais la puissance consommée, la puissance réseau, et les énergies achetée et vendue au réseau (jour, mois, année, total). Sans compteur, aucun capteur n'est créé ; un compteur ajouté plus tard est détecté et l'intégration se recharge seule pour créer ses capteurs. Ces valeurs arrivent avec les données temps réel déjà récupérées : aucune requête supplémentaire.
- La carte affiche un panneau par panneau PV, pas par micro-onduleur (voir 0.6.2).

## 0.6.2 — 2026-10-18

- **La carte compte et affiche les panneaux PV, pas les micro-onduleurs.** Un micro-onduleur peut alimenter plusieurs panneaux : la carte dessine désormais une cellule par panneau (une par entrée PV), avec la puissance, l'éclairage et le compteur « N panneaux » de chaque panneau. La position sur le plan « Agencement » est lue pour chaque entrée (attributs `panels` du capteur de puissance de l'onduleur) ; le réglage « puissance crête par panneau » s'applique désormais à un panneau réel.

## 0.6.1 — 2026-10-16

- Si la liste des micro-onduleurs est injoignable au démarrage (panne DNS, cloud lent), elle est retentée toutes les 5 minutes au lieu de 6 heures, et l'intégration se recharge d'elle-même dès qu'elle arrive : les capteurs d'onduleur et la statistique de production ne restent plus bloqués jusqu'au soir.
- Hauteur du soleil calculée sans la fonction dépréciée de Home Assistant (retirée en 2027.7).

## 0.6.0 — 2026-10-16

- **Statistique de production heure par heure pour le tableau Énergie** (`solenso:production_<centrale>`), calculée d'après les mesures par onduleur du cloud. Quand la passerelle DTU renvoie ses mesures en différé après une coupure Internet, les deux dernières journées sont réécrites : l'énergie s'inscrit à l'heure où elle a été produite. Au premier lancement, l'historique conservé par le cloud est repris (au plus un an). À sélectionner à la place de la Production cumulée dans le tableau Énergie.
- **Plus de faux 0 W en plein jour.** Quand les remontées s'arrêtent alors que le soleil est haut (plus de 10°), la puissance de la centrale et les mesures instantanées des onduleurs (puissance, tension et courant panneau) passent indisponibles au lieu de 0. La nuit, elles restent à 0 comme avant.
- Home Assistant 2026.1 minimum.

## 0.5.0 — 2026-10-11

- Nouveau capteur **Production cumulée** pour le tableau Énergie : il suit la production heure par heure, au lieu de la « Production totale » que le cloud ne recalcule qu'une fois par nuit (toute la production de la veille apparaissait d'un bloc vers 2 h). Compteur toujours croissant, sans à-coup à minuit, qui rattrape la production manquée pendant un redémarrage ou une coupure du cloud.
- README : le tableau Énergie doit désormais utiliser la Production cumulée.

## 0.4.2 — 2026-10-09

- La carte est ajoutée automatiquement aux **ressources** des tableaux de bord (comme le fait HACS) : elle reste disponible après un rechargement de page. Une ressource ajoutée à la main est reprise, sans doublon. Les tableaux de bord en YAML gardent l'ancien mode de chargement.
- La ressource est retirée quand l'intégration est supprimée.

## 0.4.1 — 2026-10-09

- La carte est déclarée dès le démarrage de Home Assistant, même si le cloud Solenso ne répond pas encore : plus d'« Erreur de configuration » à la place de la carte après un redémarrage.

## 0.4.0 — 2026-10-09

- La carte place les panneaux comme sur le toit, d'après la page « Agencement » de Solenso ; un bloc par toiture. Position exposée en attributs `layout_row`, `layout_column` et `layout_array` sur la puissance de chaque micro-onduleur.
- La carte est rechargée par le navigateur dès qu'elle change (empreinte du fichier dans son adresse).

## 0.3.0 — 2026-10-09

- Carte « Panneau solaire » livrée avec l'intégration et chargée automatiquement : un panneau par micro-onduleur, éclairé selon sa production, avec la puissance et une plaque jour / mois / total.
- Éditeur visuel de la carte (centrale, colonnes, puissance crête, affichage des watts).
- Rattachement des appareils compatible avec les prochaines versions de Home Assistant (`via_device` déprécié).

## 0.2.0 — 2026-10-09

- Un appareil par micro-onduleur : puissance, production du jour, tension et courant panneau, température, tension et fréquence réseau, dernière remontée.
- Appareil DTU avec un capteur « Connexion au cloud ».
- Onduleurs endormis : puissance, tension et courant à 0, production du jour conservée jusqu'à minuit.
- Une panne des données par onduleur ne bloque plus les capteurs de la centrale.
- Icône de l'intégration.

## 0.1.0 — 2026-10-09

- Première version : configuration par l'interface, détection automatique des centrales, puissance et productions jour / mois / année / totale, reconnexion automatique.
