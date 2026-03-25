Phase 0 : adressage + OSPF + loopbacks

Phase 1 : MPLS/LDP sur le core

Phase 2 : iBGP vpnv4 entre PE

Phase 3 : ajout futur des CE/VRF/eBGP

commande pour exécuter le code en entier : python config_auto.py --push-full ALL --project projet_NAS_GNS.gns3

ou 

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python config_auto.py --push-full ALL --project projet_NAS_GNS.gns3