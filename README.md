# Lecteur Pro Logic II

Lecteur vidéo pour Windows (Python, PyQt6, libmpv) qui décode le son stéréo
Dolby Surround (Lt/Rt) vers du 5.1, avec les modes et réglages de type Pro Logic II.

## Fonctionnalités

- Lecture de tous les formats courants (MKV, MP4, AVI, TS, M2TS, MOV, WMV, WEBM…), décodage matériel
- Sources multicanal (AC-3, E-AC-3, DTS, AAC 5.1…) lues telles quelles
- Décodage des sources stéréo vers 5.1 via WASAPI :
  - **Pro Logic II Movie** : steering actif, surrounds pleine bande
  - **Pro Logic II Music** : largeur du centre (0–7), dimension (-3/+3), panorama
  - **Dolby Surround Pro Logic** : matrice passive classique, surround limité à 7 kHz
- Retard des surrounds (0–30 ms), caisson de basses (LFE 120 Hz), limiteur anti-saturation
- Plein écran, glisser-déposer, raccourcis clavier

## Installation

1. Installer Python 3.10+ (64 bits) en cochant « Add Python to PATH »
2. `pip install -r requirements.txt`
3. Télécharger **libmpv** (build « mpv-dev-x86_64 », fichier `libmpv-2.dll` ou `mpv-2.dll`)
   et le placer à côté de `lecteur_prologic.py`
4. Windows : Paramètres > Son > votre sortie > configuration des haut-parleurs : **5.1**
5. Lancer :
   ```
   python lecteur_prologic.py
   python lecteur_prologic.py "C:\Videos\film.mkv"
   ```

## Créer un .exe

Placer `libmpv-2.dll` dans le dossier puis lancer `build.bat`. L'exécutable est généré dans `dist/`.

## Raccourcis

| Touche | Action |
|---|---|
| Espace | Lecture / pause |
| F / Échap | Plein écran |
| ← / → | -10 s / +10 s |
| ↑ / ↓ | Volume |

## Limites

- Ce n'est **pas** un décodeur Dolby certifié : le décodeur Pro Logic II officiel est propriétaire.
  Il s'agit d'une implémentation ouverte (matrice Lt/Rt + steering du filtre FFmpeg `surround`)
  qui reproduit le principe et les réglages. Dolby® et Pro Logic® sont des marques de Dolby Laboratories.
- Sur casque ou enceintes stéréo, le 5.1 est re-mixé en stéréo et l'effet surround est en grande partie perdu.
- Le filtre `surround` dépend de la version de libmpv/FFmpeg utilisée.

## Dépannage

- `python n'est pas reconnu` : réinstaller Python en cochant « Add Python to PATH »
- `Cannot find mpv-2.dll` : DLL absente du dossier ou en 32 bits alors que Python est en 64 bits
- Pas de son surround : vérifier la configuration 5.1 de Windows et le message sous les contrôles

## Licence

PyQt6 est distribué sous GPL v3 : il est recommandé de publier ce projet sous **GPL-3.0**
(sur GitHub : *Add file > Create new file > nom `LICENSE` > Choose a license template*).
libmpv est sous LGPL/GPL selon le build : ne pas le versionner dans le dépôt.
