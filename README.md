# GPS Track Video - Windows 10/11

Crea video 3D da traccia GPS + foto/video geolocalizzati.

## Funzionalita'
- Tracce: import GPX / KML / GeoJSON
- Media: foto e video; GPS letto dagli EXIF delle foto
- Posizionamento manuale: drag & drop dalla lista sulla mappa (con snap alla traccia)
- Mappa 3D: MapLibre, stili gratuiti OSM / OpenTopoMap / CARTO (Voyager, Dark), pitch 60
- Timeline: riordino drag & drop, titolo, commento con colore sfondo, durata, transizioni
- Transizioni: per elemento, tutte uguali, o random
  (crossfade, slide sinistra, slide su, zoom/circle)
- Musica: da file locale, BPM rilevati automaticamente (onset detection)
- Sync automatico: durate foto e punti di transizione agganciati ai battiti
- Export MP4 (H.264/AAC) con preset:
  Reels/TikTok/Shorts 1080x1920, YouTube 1920x1080, quadrato 1080x1080
- Effetto Ken Burns sulle foto, commenti sovrapposti con sfondo colorato,
  sfondo mappa 3D sotto ogni media (opzionale)

## Come ottenere l'.exe (build automatica, gratis, senza installare nulla)
1. Crea un account gratuito su github.com
2. Crea un repository nuovo (es. gps-video) e carica TUTTO il contenuto
   di questa cartella (anche la cartella nascosta .github)
3. Vai su tab "Actions": il workflow "Build Windows EXE" parte da solo
4. Dopo 10-15 min: apri il run completato e scarica l'artifact
   "GpsTrackVideo-Windows" (cartella con GpsTrackVideo.exe)
5. Estrai lo zip, avvia GpsTrackVideo.exe
   - Windows SmartScreen (exe non firmato): "Ulteriori informazioni" -> "Esegui comunque"
6. Richiede connessione internet per le tiles della mappa (gratuite)

## Uso
1. Tab "Traccia": carica il file GPX/KML
2. Tab "Media": aggiungi foto e video
3. Trascina gli elementi senza GPS dalla lista sulla mappa
4. Tab "Timeline": doppio click per titolo/commento/durata/transizione/colori
5. Tab "Export": stile mappa, visuale 3D, formato, musica, sync battiti -> ESPORTA

## Requisiti
Windows 10/11 64 bit, ~8 GB RAM consigliati, internet per mappe e build.
FFmpeg incluso automaticamente nell'exe (via imageio-ffmpeg).

## Note
- I brani "di Instagram/TikTok" non sono liberamente scaricabili: l'app
  suggerisce librerie royalty-free (YouTube Audio Library, FMA, Pixabay, Mixkit).
- Lo sfondo mappa 3D usa snapshot MapLibre ad alta inclinazione; un render
  3D puro frame-by-frame richiederebbe un motore OpenGL dedicato.
