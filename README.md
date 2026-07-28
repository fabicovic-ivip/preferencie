# Preferencie politických strán na Slovensku

Priebežne aktualizovaný dataset volebných preferencií politických strán SR
a interaktívny dashboard, ktorý pripravuje [IVIP — Inštitút vzdelávania
a inovácií v politike](https://ivip.sk). Živý dashboard beží na
[ivip.sk/preferencie](https://ivip.sk/preferencie/).

## Súbory

- `polls_sk.json` — kompletný dataset (kľúče `verzia`, `meta`, `polls`); z tohto súboru si dáta za behu načítava aj dashboard na ivip.sk
- `polls_sk.csv` — ten istý dataset v CSV
- `nms_jadro_potencial.csv` — volebné jadro a potenciál strán (NMS)
- `index.html` — samostatná verzia dashboardu (beží aj tu na GitHub Pages)
- `skript/parse_polls.py` — skript, ktorý dataset generuje
- `zdrojove_data/` — surové zdrojové tabuľky
- `VERSION` — verzia datasetu (formát RRRR-MM-DD.N)

## Štruktúra záznamu

Každý prieskum má: `agency`, `start`, `end`, `mid` (stred zberu), `sample`,
`type` (`poll` · `exitpoll` · `election` · `ep_election`) a hodnoty strán v %.
Účasť: `ucast`, `ucast_typ`, `nerozhodnuti`, `nesli_by`, `odmietli`.

## Zdroje a metodika

Primárnym zdrojom sú tlačové správy agentúr (NMS, AKO, Focus, Ipsos, Median SK,
Infostat/CSV pri ŠÚ SR a i.); podrobná metodika je v pätičke dashboardu.
Dáta jednotlivých prieskumov patria príslušným agentúram.

Našli ste chybu alebo vám tu nejaký prieskum chýba? Napíšte na
[ahoj@ivip.sk](mailto:ahoj@ivip.sk) — ideálne s odkazom na zdroj.
