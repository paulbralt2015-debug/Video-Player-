# Twins Video Player 2

Lecteur vidéo Windows natif (Python, PySide6, libmpv). Aucune page HTML, aucun `ffmpeg.exe`,
aucun sous-processus : le code ne lance rien d'externe.

## Compiler sur GitHub

1. Poussez ce dossier dans un dépôt GitHub.
2. L'onglet **Actions** lance le workflow « Windows » : tests, téléchargement de `libmpv-2.dll`,
   compilation PyInstaller, archive `TwinsVideoPlayer-windows-x64.zip` (section *Artifacts*).
3. Un tag `v2.0.0` publie en plus l'archive dans les *Releases*.

Dézippez, lancez `TwinsVideoPlayer.exe`.

## Lancer depuis les sources (Windows)

```
pip install -r requirements.txt
```
Placez `libmpv-2.dll` (build « mpv-dev » de shinchiro/mpv-winbuild-cmake) à la racine, puis `python run.py`.
Tests : `python -m pytest`.

## Ce qui remplace l'ancienne page HTML

| Fonction | Implémentation |
|---|---|
| Lecture, liste, dossiers, glisser-déposer | libmpv, liste Qt réordonnable |
| Décodage Dolby Surround / Pro Logic / PL II | filtres `pan`, `adelay`, `lowpass`, `join` de libmpv (`twinsplayer/filters.py`) |
| PAL / SECAM / NTSC | filtres `fps`, `scale`, `gblur` (chroma), `eq`, `drawgrid` |
| Enregistrement de la conversion | mode d'encodage de libmpv → MP4 (MPEG-4 + AAC) |
| Sous-titres, A-B, image par image, capture | mpv (`sub-add`, `ab-loop`, `frame-step`, `screenshot`) |
| Reprise, marque-pages, mots bloqués | `%APPDATA%\TwinsVideoPlayer\state.json` |

## Bugs de la version HTML corrigés

1. Curseur « Salle » sans effet (deux éléments `id="rev"`).
2. Reprise de lecture du fichier suivant effacée à la fin d'un fichier (double écouteur `ended`).
3. Sous-titres de la vidéo précédente conservés ; VTT sans heures ou avec réglages ignorés ; accents cassés en Latin-1.
4. Image par image figée à 1/30 s.
5. Option « Image » sans effet en PAL/SECAM/NTSC.
6. Fichier retiré de la liste pendant la lecture : la vidéo restait chargée.
7. Clés de reprise accumulées sans limite (désormais plafonnées à 300).
8. Marque-pages indexés par nom seul (désormais nom + taille).
9. Mode Surf et plein écran fonctionnent sur la même surface vidéo.

## Limites connues

- libmpv embarque les bibliothèques de décodage FFmpeg : l'application n'appelle jamais `ffmpeg`,
  mais ces bibliothèques sont dans `libmpv-2.dll`.
- Pro Logic reste une matrice passive : pas de logique de direction adaptative, pas de réduction de bruit Dolby B.
- Casque : repli stéréo + crossfeed (pas de HRTF sans fichier SOFA).
- PAL/SECAM/NTSC : approximation de la bande passante couleur, pas de modèle de sous-porteuse.
- Les builds libmpv de shinchiro sont sous licence GPL : la redistribution de l'archive est soumise à la GPL.
