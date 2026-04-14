# NAS Project
## Correspondance avec les attendus

### Phase 0

Implémenté :
- adressage IPv4 des interfaces core
- loopbacks provider
- OSPF
- `router-id` OSPF

### Phase 1

Implémenté :
- `ip cef`
- MPLS sur les liens core
- LDP avec `Loopback0`

### Phase 2

Implémenté :
- iBGP entre PE
- sessions loopback à loopback
- address-family `vpnv4`
- `send-community both`

### Phase 3

Implémenté :
- ajout des CE
- création des VRF
- association VRF aux interfaces PE-CE
- eBGP PE-CE
- annonces des réseaux clients

### Phase 4.a

Partiellement implémenté :
- lecture du `running-config`
- calcul de diff
- push ciblé des commandes de convergence
- mise à jour incrémentale du customer onboarding sans wipe global
- changement d'AS BGP local côté CE sans reload

Non implémenté à ce stade :
- convergence incrémentale complète du core provider
- gestion incrémentale de tous les services phase 4

## Commandes utiles

### Générer toutes les configurations

```powershell
python config_auto.py
```

Effet :
- génère les configurations par phase dans `generated_configs/`
- génère les configurations complètes `*_full.cfg`
- régénère `intent_visualization.json`

### Déployer les phases dans l'ordre

```powershell
python config_auto.py --push-phases --project projet_NAS_GNS.gns3
```

Effet :
- nettoie le runtime Dynamips
- efface les `startup-config`
- pousse les phases 0, 1, 2 puis 3

### Déployer la configuration complète sur tous les routeurs

```powershell
python config_auto.py --push-full ALL --project projet_NAS_GNS.gns3
```

### Déployer la configuration complète sur un seul routeur

```powershell
python config_auto.py --push-full PE1 --project projet_NAS_GNS.gns3
```

### Réconcilier la phase 4.a sans reload ni wipe global

Tous les routeurs :

```powershell
python config_auto.py --reconcile-phase4a ALL --project projet_NAS_GNS.gns3
```

Un seul routeur :

```powershell
python config_auto.py --reconcile-phase4a CE2 --project projet_NAS_GNS.gns3
```

Effet :
- lit le `running-config` courant
- calcule le diff avec l'état désiré issu de `intent.json`
- pousse uniquement les commandes nécessaires
- ne fait ni `erase startup-config`, ni nettoyage GNS3, ni reload

### Pousser manuellement un fichier généré

```powershell
python telnet_push.py PE1 --suffix full --project projet_NAS_GNS.gns3
```

Suffixes utiles :
- `phase0_setup`
- `phase1_mpls`
- `phase2_vpnv4`
- `phase3_clients`
- `full`
- `reconcile_phase4a`

### Utiliser plusieurs workers en parallèle

```powershell
python config_auto.py --push-full ALL --project projet_NAS_GNS.gns3 --workers 4
```

Ou :

```powershell
python config_auto.py --reconcile-phase4a ALL --project projet_NAS_GNS.gns3 --workers 4
```

### Fournir un mot de passe `enable`

```powershell
python config_auto.py --push-full ALL --project projet_NAS_GNS.gns3 --enable-pass monmotdepasse
```

## Modèle d'intention

Le réseau est décrit dans `intent.json`.

La source de vérité actuelle est :
- `loopback_index` pour les routeurs provider `PE` et `P`
- `ce_loopback_index` pour les routeurs `CE`
- `ce_as` pour l'AS BGP local des clients
- `vrfs` pour les RD et RT
- `links` pour les liens core et PE-CE
- `customer_sites` pour les LAN clients

## Fonctionnalités implémentées

### Réseau

- topologie provider `PE1 - P1 - P2 - PE2`
- ajout de 4 CE
- adressage automatique des liens core
- adressage automatique des liens PE-CE
- adressage automatique des LAN clients
- loopbacks provider calculées depuis `loopback_index`
- loopbacks CE calculées depuis `ce_loopback_index`
- `router-id` provider dérivé automatiquement de `Loopback0`
- OSPFv2 dans le core
- MPLS dans le core
- LDP avec `Loopback0`
- iBGP `vpnv4` entre PE via les loopbacks
- VRF sur les PE
- association VRF aux interfaces PE-CE
- eBGP PE-CE
- annonces des LAN clients
- annonces des loopbacks CE

### Automatisation

- génération des configurations par phase
- génération d'une configuration complète fusionnée par routeur
- génération de `intent_visualization.json`
- push Telnet automatique sur les consoles GNS3
- découverte des ports console via le fichier `.gns3`
- push parallèle sur plusieurs routeurs
- nettoyage runtime Dynamips pour les déploiements complets
- reset des `startup-config` pour les déploiements complets
- réconciliation incrémentale partielle sans wipe global

## Récapitulatif des phases

### Phase 0 : setup

Allocation des adresses core et des loopbacks provider, configuration des interfaces, puis construction d'OSPF sur les routeurs `PE` et `P`.

### Phase 1 : core MPLS

Activation de `ip cef`, configuration LDP avec `Loopback0` comme identité MPLS, puis activation de `mpls ip` sur les interfaces core.

### Phase 2 : core BGP/MPLS VPN

Configuration des sessions iBGP entre les PE via les loopbacks, puis activation de l'address-family `vpnv4` avec échange des communautés étendues.

### Phase 3 : customer onboarding

Création des VRF sur les PE, association des interfaces PE-CE aux bonnes VRF, configuration eBGP PE-CE, puis annonce des LAN clients et des loopbacks CE.

### Phase 4.a : mise à jour sans reload

Lecture de la configuration courante des routeurs ciblés, reconstruction de l'état désiré depuis `intent.json`, calcul des différences utiles, puis push des seules commandes nécessaires pour converger vers la nouvelle intention sans `erase startup-config` ni reload.
