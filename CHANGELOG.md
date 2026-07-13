# Changelog

Toutes les modifications notables de ce projet sont documentées dans ce fichier.

Le format s'appuie sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).

## [Non publié]

### Added

- 2026-07-13 — Service d'assets custom déposés par des process externes (le repo ne les produit ni ne les déploie) :
  - **Datasets de tuiles additionnels** : arborescences `{z}/{x}/{y}.{ext}` déposées dans `/data/ofm/http_host/tiles/{dataset}/`, servies sur `/tiles/{dataset}/{z}/{x}/{y}.{ext}` (cache 10 ans, datasets immuables versionnés par leur nom). Vecteur (`.pbf` pré-gzippé, servi avec `Content-Encoding: gzip`, tuile manquante → 200 vide) et raster (`.webp`/`.png`/`.jpg`/`.jpeg`/`.avif`, servis tels quels sans gzip, tuile manquante → 404). TileJSON optionnel servi sur `/tiles/{dataset}` si un `tilejson.json` est déposé à la racine du dataset (placeholder `__TILEJSON_DOMAIN__` substitué par nginx).
  - **Styles MapLibre custom** : fichiers `{name}.json` déposés dans `assets/styles/custom/`, servis sur `/styles/{name}` avec priorité sur les styles OFM du même nom. Le dossier n'est jamais touché par la synchro OFM.
  - Les locations nginx sont génériques (regex) : ajouter un dataset ou un style ne nécessite aucun reload nginx. Les dossiers `tiles/` et `assets/styles/custom/` sont créés au deploy (`prepare_http_host`) sans jamais effacer leur contenu.
  - Documentation : section « Custom assets » dans `docs/self_hosting.md` (protocole de dépôt, sprites custom inclus) et requêtes de test dans `examples/requests.http`.

### Changed

- 2026-07-13 — Les styles (`/styles/{name}`) sont désormais cachés **5 minutes** au lieu d'1 jour : ils évoluent régulièrement et le `sub_filter` nginx empêche la revalidation conditionnelle (ETag/Last-Modified supprimés).

### Fixed

- `ssh_lib/pkg_base.py` : retrait du paquet `ctop` de la liste des paquets de base. Il n'est plus disponible dans les dépôts Ubuntu récents (26.04 « resolute ») et faisait échouer `apt-get install` (donc `prepare_shared`) lors du déploiement http-host.
