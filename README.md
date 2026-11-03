# SpeleoMap Generator

Plugin QGIS per la **cartografia speleologica automatizzata**: da una nuvola di punti LAS georeferenziata di una grotta genera

- **pianta**, **profilo longitudinale** e **sezioni trasversali**;
- esportazione in **PDF**, **DXF** editabile e **progetto QGIS**;
- sezioni e profili lungo **tracce disegnate** dall'utente;
- **rilievo d'insieme** di più grotte con collegamenti e **vista 3D** percorribile nel browser.

Requisiti: **QGIS 3.40 o successivo**; librerie numpy e scipy (incluse in QGIS per Windows e macOS).

## Installazione (consigliata: repository)

1. QGIS → *Plugin* → *Gestisci e installa plugin* → *Impostazioni*
2. Nella sezione *Repository dei plugin* premere **Aggiungi…**
3. Nome: `SpeleoMap Generator` – URL:
   ```
   https://raw.githubusercontent.com/Stratos915/SpeleoMapGenerator/main/plugins.xml
   ```
4. OK → *Ricarica tutti i repository* → nella scheda *Tutti* cercare **SpeleoMap Generator** → *Installa*.

Gli aggiornamenti futuri compariranno automaticamente nella scheda *Aggiornabili*.

## Installazione da ZIP

Scaricare [`dist/SpeleoMapGenerator.zip`](dist/SpeleoMapGenerator.zip) → QGIS → *Plugin* → *Gestisci e installa plugin* → *Installa da ZIP*.

## Uso

Menu *Plugin* → *SpeleoMap Generator*:
- **Generate Cave Map** – pianta, profilo e sezioni da LAS;
- **Rilievo d'insieme** – più grotte e collegamenti, vista 3D;
- **Sezioni e profili a scelta** – su tracce disegnate.

## Segnalazioni

Problemi e richieste: [Issues](https://github.com/Stratos915/SpeleoMapGenerator/issues) – contatto: stratosdiakatos@yahoo.it

## Licenza

GNU General Public License v2 o successiva (vedi `LICENSE`).
La libreria `vendor/three.min.js` (three.js) e `OrbitControls.js` sono distribuite con licenza MIT.

---
Autore: Stratos Diakatos Arvanitis
