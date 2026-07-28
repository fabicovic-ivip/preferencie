#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse Slovak opinion-poll data transcribed from Wikipedia into a clean dataset."""
import re, json, sys, datetime, csv, os

# koreň priečinka Preferencie (skript býva v podpriečinku skript/)
BASE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RAW  = os.path.join(BASE, "zdrojove_data")

MONTHS = {m: i+1 for i, m in enumerate(
    ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"])}
MONTHS.update({"June":6,"July":7,"February":2,"January":1,"March":3,"April":4,
               "August":8,"September":9,"October":10,"November":11,"December":12})

def parse_date_range(s, default_year=None):
    s = s.replace("–","-").replace("—","-").strip()
    # forms: "15-20 May 2026", "30 Apr-4 May 2025", "27 Oct-1 Nov 2024", "Mar 2023", "12 Mar 2023", "29 February 2020", "1 Apr-6 May 2023"
    m = re.match(r"^(\d{1,2})-(\d{1,2}) (\w+) (\d{4})$", s)
    if m:
        d1,d2,mo,y = m.groups(); mo=MONTHS[mo]; y=int(y)
        return (datetime.date(y,mo,int(d1)), datetime.date(y,mo,int(d2)))
    m = re.match(r"^(\d{1,2}) (\w+)-(\d{1,2}) (\w+) (\d{4})$", s)
    if m:
        d1,mo1,d2,mo2,y = m.groups(); y=int(y)
        mo1,mo2 = MONTHS[mo1],MONTHS[mo2]
        y1 = y-1 if mo1>mo2 else y
        return (datetime.date(y1,mo1,int(d1)), datetime.date(y,mo2,int(d2)))
    m = re.match(r"^(\d{1,2}) (\w+) (\d{4})$", s)
    if m:
        d,mo,y = m.groups()
        dt = datetime.date(int(y), MONTHS[mo], int(d)); return (dt,dt)
    m = re.match(r"^(\w+) (\d{4})$", s)
    if m:
        mo,y = m.groups()
        dt = datetime.date(int(y), MONTHS[mo], 15); return (dt,dt)
    raise ValueError("date? "+s)

def parse_cell(tok):
    """'3.9@2' -> (3.9, 2, False) ; '–' -> (None, 1, False) ; '!4.0' -> (4.0, 1, True).
    Predpona '!' znamena, ze hodnota v zdroji nie je na rovnakej zakladni ako zvysok riadku
    (stlpec Others u niektorych agentur 2021-2022 zahrna aj nerozhodnutych) — do datasetu
    sa nezapisuje, ale jej rozpatie sa pocita. Zoznam a doklady: QA_report.md, sekcia B."""
    t = tok.strip()
    span = 1
    skip = False
    if t.startswith("!"):
        skip = True
        t = t[1:].strip()
    if "@" in t:
        t, s = t.split("@", 1)
        span = int(s)
        t = t.strip()
    if t in ("–", "—", "-", "", "—N/a", "N/a"):
        return None, span, skip
    if t.lower() == "tie":
        return "TIE", span, skip
    t = t.replace("%", "").strip()
    v = t.split()[0].replace(",", ".")
    try:
        return float(v), span, skip
    except ValueError:
        return None, span, skip

def load_rows(path):
    """Vracia zoznam ('SECTION', nazov) / (agentura, datum, vzorka, cells).
    cells = [(hodnota, rozpatie, preskocit), ...]. COLS jednotlivych sekcii sa vracia zvlast."""
    rows = []
    cols = {}
    cur = None
    for ln in open(path, encoding="utf-8"):
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            if ln.startswith("# SECTION"):
                cur = ln.split()[-1]
                rows.append(("SECTION", cur))
            elif ln.startswith("# COLS:"):
                cols[cur] = ln.split(":", 1)[1].split()
            continue
        head, rest = ln.split(":", 1)
        parts = [p.strip() for p in rest.split(";")]
        cells = [parse_cell(p) for p in parts[2:]]
        rows.append((head.strip(), parts[0], parts[1] if len(parts) > 1 else "", cells))
    return rows, cols

# ---- listove stlpce -> stlpce datasetu -------------------------------------
# Jedna bunka, ktora pokryva viac listovych stlpcov, sa neda rozdelit. Kam hodnota patri,
# je dane entitou, ktora v tom mesiaci realne existovala / bola merana; nie je to odhad,
# ale rozhodnutie doslovne prevzate z overenych riadkov v QA_report.md, sekcia A.
SPAN_MAP = {
    ("olano", "zl"):          "olano",  # koalicia OLaNO (ZL do nej vstupila)
    ("smk", "mko"):           "ali",    # Aliancia / Szovetseg
    ("smk", "mko", "mh"):     "ali",    # Aliancia vratane Most-Hidu
    ("mh", "modri"):          "mh",     # Most-Hid + Modri, rozdelit sa neda
    ("modri", "dem"):         "modri",  # Demokrati vtedy este neexistovali
    ("dv", "hlas"):           "hlas",   # Dobra volba sa samostatne neuvadzala
    ("ps", "spolu"):          "ps",     # koalicia PS-SPOLU
    ("mh", "smk", "os"):      "ali",    # sekcia B: ALI cez tri podstlpce
    ("smk", "os", "mh"):      "ali",    # sekcia C: ALI cez tri podstlpce
    ("mf", "smk", "os"):      "ali",    # sekcia C do 3/2020: madarsky blok
}
# Jednotlive listove stlpce, ktore sa v datasete volaju inak.
LEAF_MAP = {"smk": "ali", "mko": "ali"}
# Listove stlpce, ktore sa pripocitavaju do 'oth'.
LEAF_TO_OTH = {"os", "x1"}
# Listove stlpce, ktore sa zahadzuju.
LEAF_DROP = {"lead"}

def expand(cells, cols, where):
    """Rozbali @N a priradi hodnoty zlava doprava. Sucet rozpati musi presne sediet."""
    span = sum(n for _, n, _ in cells)
    if span != len(cols):
        raise SystemExit(f"CHYBA {where}: sucet rozpati {span} != {len(cols)} stlpcov v # COLS")
    rec = {}
    i = 0
    for val, n, skip in cells:
        leaves = tuple(cols[i:i + n])
        i += n
        if val is None or val == "TIE" or skip:
            continue
        if n == 1:
            leaf = leaves[0]
            if leaf in LEAF_DROP:
                continue
            if leaf in LEAF_TO_OTH:
                rec["oth"] = round(rec.get("oth", 0.0) + val, 2)
                continue
            col = LEAF_MAP.get(leaf, leaf)
        else:
            if leaves not in SPAN_MAP:
                raise SystemExit(f"CHYBA {where}: nezname zlucenie stlpcov {leaves}")
            col = SPAN_MAP[leaves]
        if col in rec and col != "oth":
            raise SystemExit(f"CHYBA {where}: stlpec {col} priradeny dvakrat")
        rec[col] = val
    return rec

# ---------------- File 1: next election (Oct 2023 - present) ----------------
F1_COLS = ["smer","ps","hlas","olano","zl","ku","kdh","sas","sns","rep","ali","dem","sr","lsns","oth","lead"]

def parse_file1():
    rows, _ = load_rows(f"{RAW}/raw_next_election.txt")
    out=[]; problems=[]
    for row in rows:
        if row[0]=="SECTION": continue
        head, date_s, sample, cells = row
        vals=[v for v,_,_ in cells]
        n=len(vals)
        rec=dict.fromkeys(F1_COLS, None)
        if n==16:
            for c,v in zip(F1_COLS, vals): rec[c]=v
        elif n==14:
            cols=[c for c in F1_COLS if c not in ("zl","ku")]
            for c,v in zip(cols, vals): rec[c]=v
        elif n==15:
            # one of zl/ku present at position 4
            x=vals[4]
            key = "zl" if (x is not None and x>=1.4) else "ku"
            cols=["smer","ps","hlas","olano",key,"kdh","sas","sns","rep","ali","dem","sr","lsns","oth","lead"]
            for c,v in zip(cols, vals): rec[c]=v
        else:
            problems.append((head,date_s,"badcount",n)); continue
        # validation
        parties=[rec[c] for c in F1_COLS if c not in ("oth","lead") and rec[c] is not None]
        tot=sum(parties)+(rec["oth"] or 0)
        lead=rec["lead"]
        srt=sorted(parties, reverse=True)
        ndash=sum(1 for v in vals if v is None)
        ok_sum = (100-3.5-2.5*ndash)<=tot<=101.5
        ok_lead = (lead=="TIE" and abs(srt[0]-srt[1])<0.05) or \
                  (isinstance(lead,float) and abs((srt[0]-srt[1])-lead)<0.16) or lead is None
        if not (ok_sum and ok_lead):
            problems.append((head,date_s,"failcheck",n,round(tot,1),lead)); continue
        d1,d2 = parse_date_range(date_s)
        typ = "election" if "election" in head.lower() else "poll"
        out.append(dict(agency=head, d1=d1, d2=d2, sample=sample, typ=typ,
                        vals={c:rec[c] for c in F1_COLS if c not in ("lead",) and rec[c] is not None}))
    return out, problems

# ---------------- File 2: 2020-2023 (sekcie A, B, C) ----------------
def parse_file2():
    return _parse_sections(f"{RAW}/raw_2023_election.txt")

# ---------------- File 3: 2016-2020 (sekcie SA, SB) ----------------
def parse_file3():
    return _parse_sections(f"{RAW}/raw_2020_election.txt")

def _parse_sections(path):
    rows, cols = load_rows(path)
    out=[]; problems=[]; section=None
    for row in rows:
        if row[0]=="SECTION": section=row[1]; continue
        head, date_s, sample, cells = row
        d1,d2 = parse_date_range(date_s)
        rec = expand(cells, cols[section], f"{os.path.basename(path)} [{section}] {head} {date_s}")
        typ = "election" if "election" in head.lower() else "poll"
        out.append(dict(agency=head, d1=d1, d2=d2, sample=sample, typ=typ, vals=rec))
    return out, problems


# ---------------- Extra polls (Infostat + newest, from press sources) ----------------
def D(s): return datetime.date.fromisoformat(s)
EXTRA = [
 dict(agency="Infostat", d1=D("2025-01-27"), d2=D("2025-01-31"), sample="1,098", typ="poll",
      vals=dict(ps=24.6, smer=22.7, hlas=13.7, kdh=8.7, rep=5.2, ali=4.9, olano=3.9, dem=3.8, sas=3.2, sns=3.1, sr=1.7)),
 dict(agency="Infostat", d1=D("2025-02-21"), d2=D("2025-02-28"), sample="1,132", typ="poll",
      vals=dict(ps=24.8, smer=22.5, hlas=11.5, kdh=8.4, rep=6.6, dem=5.0, sas=4.3, ali=4.2, olano=3.4, sr=2.8, sns=1.5)),
 dict(agency="Infostat", d1=D("2025-05-12"), d2=D("2025-05-16"), sample="652", typ="poll",
      vals=dict(hlas=14.4, kdh=8.3, rep=5.0, sas=4.7, dem=4.7, olano=4.4, sns=3.5, ali=3.2, sr=2.4)),  # PS a Smer nezverejnene presne (PS 1., naskok ~5 b.)
 dict(agency="Infostat", d1=D("2025-11-03"), d2=D("2025-11-07"), sample="1,109", typ="poll",
      vals=dict(ps=21.1, smer=16.7, hlas=11.5, kdh=9.1, rep=7.8, sas=7.0, dem=6.0, olano=6.0, ali=5.0, sns=3.0, sr=2.3, oth=1.1)),
 # Infostat január 2026 — PDF tlačová správa CSV pri Infostate (v datasete predtým chýbal)
 dict(agency="Infostat", d1=D("2026-01-12"), d2=D("2026-01-16"), sample="1,085", typ="poll",
      vals=dict(ps=22.2, smer=17.5, rep=11.6, hlas=9.9, kdh=8.8, sas=6.2, olano=5.2, dem=4.1,
                ali=3.5, sns=2.7, sr=2.1)),
 # Infostat marec 2026 — PDF tlačová správa (v datasete predtým chýbal); Právo na pravdu 1,0 % je v oth
 dict(agency="Infostat", d1=D("2026-03-09"), d2=D("2026-03-13"), sample="1,159", typ="poll",
      vals=dict(ps=21.1, smer=20.2, rep=9.7, kdh=9.4, hlas=8.3, sas=7.0, dem=5.5, olano=4.0,
                ali=3.8, sns=3.3, sr=1.4)),
 dict(agency="Infostat", d1=D("2026-05-11"), d2=D("2026-05-15"), sample="1,003", typ="poll",
      vals=dict(ps=22.2, smer=18.4, rep=9.8, hlas=9.0, kdh=8.9, olano=7.6, sas=7.3, dem=4.3, ali=3.5, sns=2.5)),
 dict(agency="Infostat", d1=D("2026-06-01"), d2=D("2026-06-05"), sample="1,020", typ="poll",
      vals=dict(smer=19.8, ps=18.6, kdh=9.9, hlas=9.4, rep=8.7, sas=8.1, olano=6.9, dem=4.5, ali=4.1, sr=3.5, lsns=2.0, oth=1.5)),
 # --- máj–júl 2026 (zdroje: STVR/TASR, SME/SITA, NMS) ---
 dict(agency="AKO", d1=D("2026-05-14"), d2=D("2026-05-21"), sample="1,000", typ="poll",
      vals=dict(ps=19.7, smer=18.9, rep=9.1, hlas=8.9, sas=8.6, olano=8.2, kdh=7.9, dem=5.1,
                sns=4.8, ali=4.0, sr=2.2, oth=2.6)),
 dict(agency="NMS", d1=D("2026-06-03"), d2=D("2026-06-08"), sample="1,002", typ="poll",
      vals=dict(ps=19.7, smer=16.4, rep=12.9, olano=8.3, sas=7.4, hlas=7.2, dem=5.5, kdh=4.8,
                ali=4.5, sr=3.1, zl=2.9, sns=2.4, oth=2.7)),
 dict(agency="AKO", d1=D("2026-06-10"), d2=D("2026-06-18"), sample="1,000", typ="poll",
      vals=dict(ps=20.0, smer=18.6, rep=9.5, hlas=8.6, sas=8.4, kdh=8.0, olano=7.3, dem=6.2,
                sns=4.9, ali=3.2, sr=2.5, oth=2.6)),
 dict(agency="Ipsos", d1=D("2026-06-17"), d2=D("2026-06-23"), sample="1,035", typ="poll",
      vals=dict(ps=20.2, smer=19.0, rep=10.0, sas=8.8, olano=7.3, hlas=6.8, kdh=6.5, dem=5.1,
                ali=4.7, sns=3.4, sr=2.8, oth=2.3)),
 dict(agency="Focus", d1=D("2026-06-22"), d2=D("2026-06-29"), sample="1,027", typ="poll",
      vals=dict(ps=18.1, smer=17.7, rep=11.6, olano=9.0, hlas=8.0, sas=7.0, kdh=6.7, dem=4.8,
                sns=4.2, ali=3.9, sr=2.8)),
 # SANEP júl 2026 — pre ta3 (zber 13.–19. 7., n=2150); v článku „vyše 67 %“ deklarovalo účasť,
 # nepresná hodnota, preto sa do stĺpca ucast nezapisuje
 dict(agency="SANEP", d1=D("2026-07-13"), d2=D("2026-07-19"), sample="2,150", typ="poll",
      vals=dict(smer=19.6, ps=18.1, rep=11.6, olano=8.2, sas=7.7, hlas=7.6, kdh=6.3, dem=5.6,
                ali=4.6, sns=4.5)),
 # AKO júl 2026 — tlačová správa agentúry (zber 8.–14. 7., pre JOJ 24), 65,3 % rozhodnutých
 # v oth: Právo na pravdu 2,8 · Strana vidieka 0,3 · KSS 0,2 · Spravodlivosť 0,1
 dict(agency="AKO", d1=D("2026-07-08"), d2=D("2026-07-14"), sample="1,000", typ="poll",
      vals=dict(ps=20.8, smer=17.3, rep=9.7, sas=8.8, hlas=8.5, olano=8.0, kdh=7.7, dem=5.8,
                sns=4.8, ali=2.8, sr=2.2, lsns=0.2)),
 # Infostat júl 2026 — PDF tlačová správa CSV pri Infostate (zber 13.–17. 7. 2026, CAPI,
 # n = 1 049). V datasete predtým chýbal. Tabuľka uvádza len strany nad 1 %, preto súčet
 # 98,7 % a zvyšok ide do `oth` (Právo na pravdu 1,5 sa vyčleňuje cez PRAVDA).
 dict(agency="Infostat", d1=D("2026-07-13"), d2=D("2026-07-17"), sample="1,049", typ="poll",
      vals=dict(ps=19.2, smer=18.8, hlas=8.5, olano=8.5, sas=8.2, kdh=8.0, dem=7.0, rep=6.7,
                ali=5.4, sns=3.8, sr=3.1)),
 dict(agency="NMS", d1=D("2026-07-01"), d2=D("2026-07-06"), sample="1,002", typ="poll",
      vals=dict(ps=18.8, smer=15.4, rep=12.8, olano=9.1, sas=8.0, hlas=7.7, kdh=5.8, dem=5.1,
                ali=4.2, sr=3.5, sns=2.7, oth=2.3)),
]


# ---------------- Opravy po QA kontrole (27. 7. 2026) ----------------
# Podrobnosti a zdroje: QA_report.md. Kľúč = (agentúra, koniec zberu).
CORRECTIONS = {
 # Zostavaju uz len opravy HODNOT proti primarnym zdrojom. Vsetkych 49 poloziek, ktore
 # riesili priradenie stlpcov, sa 28. 7. 2026 zmazalo — po prepise raw_*.txt so znackou
 # zlucenia (@N) ich parser odvodi priamo zo zdroja. Overene regresne: bez nich je
 # polls_sk.csv bajt na bajt rovnaky. Detaily: QA_report.md, sekcia A.
 ("AKO",        "2023-04-11"): {"dv": None},                          # Wikipedia uvadza DV 0,5; tlacova sprava AKO ju neuvadza — ponechane bez DV
 ("Vo\u013eby 2020", "2020-02-29"): {"vlast": 2.93},                       # Vlast: sekcia C stlpec `vlast` nema, doplnene zo SU SR
 ("Vo\u013eby 2016", "2016-03-05"): {"ali": 4.04},                         # Wikipedia zaokruhluje na 4,1; SU SR uvadza 4,04
 ("Focus",      "2026-05-11"): {"olano": 9.6},                        # bolo 9,4; potvrdene Focusom aj STVR
 ("Focus",      "2023-02-08"): {"sr": 8.0},                           # bolo 7,7; potvrdene tlacovou spravou

 # SANEP 9.–16. 7. 2023 (n = 1 542) — Wikipédia malé strany neuvádza, doplnené z článku
 # Pravda/ta3 (21. 7. 2023). Všetkých 9 hodnôt, ktoré dataset už mal, sedí do desatiny.
 # Po doplnení riadok sčíta 98,5 %, `oth` sa dopočíta na 1,5 (OTH_RESIDUAL_EXTRA).
 ("SANEP",      "2023-07-16"): {"dem": 3.4, "ali": 2.9, "lsns": 2.0, "mh": 1.3},

 # SANEP 21.–28. 6. 2023 (n = 1 671) — doplnené z článku Pravda/ta3 (673398). Sedí 8 z 8
 # hodnôt, ktoré dataset mal (vrátane Smer 18,6). Modrí+Most-Híd a Za ľudí článok uvádza
 # dvakrát a protirečivo (0,8/0,8 vs 1,2/1,1), preto sa NEDOPĹŇAJÚ a riadok zostáva neúplný.
 ("SANEP",      "2023-06-28"): {"sns": 4.8, "dem": 3.1, "lsns": 2.2, "ali": 2.1},

 # NMS 4.–9. 3. 2026 — Za ľudí a KÚ mal dataset prehodené. Primárny zdroj (graf Flourish
 # v článku NMS „Volebný model marec 2026", vizualizácia 27959720) uvádza Za ľudí 1,9
 # a Kresťanskú úniu 0,6; zvyšných 13 hodnôt riadku sedí do desatiny. Kontrola: rovnaký graf
 # pre 1/2026, 2/2026 a 4/2026 sedí s datasetom presne, odchýlka je len v marci.
 ("NMS",        "2026-03-09"): {"zl": 1.9, "ku": 0.6},
}

# Riadky, kde zdroj preukázateľne neuvádza všetky strany, takže súčet nedosahuje 100 %.
# V CSV/JSON dostanú stĺpec `neuplny` = 1, aby sa dali v dashboarde odlíšiť od chýb.
INCOMPLETE = {
 ("SANEP",   "2023-06-28"),  # Modrí+Most-Híd a Za ľudí zdroj uvádza protirečivo; sčíta 95,0 %
 ("Infostat","2025-05-16"),  # PS a Smer neboli zverejnené; sčíta 50,6 %
}

# Prieskumy publikované v deň volieb = exit polly, nie predvolebné prieskumy.
EXITPOLLS = {("Median","2020-02-29"), ("Focus","2020-02-29"),
             ("Median","2023-09-30"), ("Focus","2023-09-30")}

# Riadky overené proti zdroju -> 'oth' sa dopočíta ako zvyšok do 100 %.
OTH_RESIDUAL_FROM  = "2026-01-01"
OTH_RESIDUAL_EXTRA = {("Ipsos","2025-03-14"), ("Focus","2025-11-18"),
                      ("NMS","2023-05-17"),   ("NMS","2023-09-09"),
                      ("Focus","2023-02-08"), ("AKO","2023-04-11"),
                      ("SANEP","2023-07-16")}

def apply_qa(recs, party_cols):
    """Opravy hodnôt, zlúčenie SMK->ali, typy riadkov, dopočet 'oth'."""
    log=[]
    for r in recs:
        key=(r["agency"], r["end"])
        for k,v in CORRECTIONS.get(key, {}).items():
            old=r.get(k)
            if v is None: r.pop(k, None)
            else: r[k]=v
            log.append(f"  oprava {r['agency']} {r['end']}: {k} {old} -> {v}")
        # SMK a Aliancia = jedna súvislá séria (SMK-MKP -> Szövetseg/Aliancia).
        # Mapovanie sa robí už v parseri (LEAF_MAP / SPAN_MAP), tu zostáva len poistka.
        if r.get("smk") is not None:
            raise SystemExit(f"stĺpec smk sa nemá objaviť po parseri: {key}")
        if key in INCOMPLETE:
            r["neuplny"]=1
        if key in EXITPOLLS:
            r["type"]="exitpoll"; log.append(f"  typ {r['agency']} {r['end']}: poll -> exitpoll")
        if r["agency"]=="Eurovoľby 2024":
            r["type"]="ep_election"; log.append("  typ Eurovoľby 2024: election -> ep_election")
        # dopočet 'oth'
        if key in INCOMPLETE:
            pass
        elif r["type"] in ("poll","exitpoll") and (r["end"]>=OTH_RESIDUAL_FROM or key in OTH_RESIDUAL_EXTRA):
            s=sum(v for c,v in r.items() if c in party_cols and c!="oth" and isinstance(v,(int,float)))
            res=round(100.0-s, 2)
            if 0.0<=res<=9.0:
                if abs((r.get("oth") or 0)-res)>0.049:
                    log.append(f"  oth {r['agency']} {r['end']}: {r.get('oth')} -> {res}")
                r["oth"]=res
            else:
                log.append(f"  ! oth {r['agency']} {r['end']}: zvysok {res} mimo rozsahu, nechávam {r.get('oth')}")
    print("QA opravy:")
    for l in log: print(l)
    return recs


# ---------------- Účasť / nerozhodnutí (z tlačových správ agentúr) ----------------
# Agentúry to publikujú rôzne, preto sa ukladá tak, ako to zverejňujú, a `ucast_typ` hovorí čo to je:
#   "rozhodnuti"  = podiel rozhodnutých voličov (AKO: 100 - nerozhodnutí - nešli by - odmietli)
#   "deklarovana" = deklarovaná volebná účasť (NMS)
#   "volby"       = skutočná účasť vo voľbách (ŠÚ SR)
# POZOR: okrem AKO júl 2026 (overené z PDF tlačovej správy) je séria zozbieraná zo sekundárnych
# zhrnutí a čaká na kontrolu proti primárnym zdrojom — viď QA_report.md.

# AKO — plný rozpad, kľúč = koniec zberu
TURNOUT_AKO = {
 "2025-05-26": dict(nerozhodnuti=12.1, nesli_by=13.4, odmietli=3.2),
 "2026-03-18": dict(nerozhodnuti=19.3, nesli_by=12.1, odmietli=4.9),
 "2026-05-21": dict(nerozhodnuti=21.2, nesli_by=10.3, odmietli=4.0),
 "2026-06-18": dict(nerozhodnuti=23.5, nesli_by=10.5, odmietli=3.2),
 "2026-07-14": dict(nerozhodnuti=23.0, nesli_by=9.9,  odmietli=1.8),
}

# NMS — deklarovaná volebná účasť, kľúč = rok-mesiac konca zberu
TURNOUT_NMS = {
 # overené priamo z článkov NMS (crawl 27. 7. 2026); "*" = nepodarilo sa vyčítať z textu článku
 "2024-05":61.3, "2024-06":54.8, "2024-07":58.4, "2024-08":59.6, "2024-09":58.9,
 "2024-10":58.9, "2024-11":59.2, "2024-12":59.2,
 "2025-01":60.1, "2025-03":59.8, "2025-04":59.8, "2025-08":62.5, "2025-09":61.1,
 "2025-10":62.6, "2025-11":61.9, "2025-12":60.2,
 "2026-02":60.8, "2026-03":59.7, "2026-04":61.9, "2026-05":60.9, "2026-06":59.0,
 "2026-07":60.8,
 # doplnené 28. 7. 2026 z primárnych článkov NMS:
 "2025-02":62.0,   # „Deklarovaná volebná účasť je na úrovni 62 %" — primárny článok NMS.
                   # Sekundárne zhrnutie uvádzalo 59,8; rozpor rozhodnutý v prospech NMS.
 "2025-05":59.9,   # „Deklarovaná volebná účasť je na úrovni 59,9 %" — primárny článok NMS
 # Stále chýbajú (hodnota sa v zdroji neuvádza ako absolútne číslo):
 #   2025-06 — článok hovorí len „narástla o 2 p.b."
 #   2026-01 — článok hovorí len „takmer nezmenená oproti decembru"
 #   2025-07 — článok účasť vôbec nespomína
 #   2024-01, 2024-02 — články na nms.global/sk už nie sú dostupné (HTTP 404)
}

# Focus — agentúra zverejňuje len podiel „nešlo by voliť" a „neviem"; `ucast` je zvyšok,
# teda podiel respondentov, ktorí menovali stranu. POZOR: Focus pod tabuľkou výslovne píše
# „Tento údaj NIE JE odhadom predpokladanej účasti na voľbách" — preto `ucast_typ`
# = "rozhodnuti", nie "deklarovana". Kľúč = koniec zberu. Zdroj: focus-research.sk/archiv.
TURNOUT_FOCUS = {
 "2022-05-31": dict(nesli_by=15.8, nerozhodnuti=18.0),  # Focus uvádza rozhodnutých 66,2 %
 "2025-11-18": dict(nesli_by=14.5, nerozhodnuti=15.2),
 "2025-12-09": dict(nesli_by=16.7, nerozhodnuti=12.2),
 "2026-02-09": dict(nesli_by=16.8, nerozhodnuti=14.0),
 "2026-03-27": dict(nesli_by=16.0, nerozhodnuti=10.4),
 "2026-05-11": dict(nesli_by=13.8, nerozhodnuti=15.3),
 "2026-06-29": dict(nesli_by=13.3, nerozhodnuti=13.4),
}

# Infostat / CSV — deklarovaná účasť vrátane odpovede „pravdepodobne by som išiel“,
# preto vychádza systematicky vyššie než u AKO aj NMS. Kľúč = rok-mesiac konca zberu.
TURNOUT_INFOSTAT = {"2025-11":68.6, "2026-01":66.4, "2026-03":68.0,
                    "2026-07":64.0}  # PDF 7/2026: 64 % (32,4 určite + 31,6 pravdepodobne)

# Skutočná účasť vo voľbách (ŠÚ SR)
TURNOUT_ELECTION = {"2016-03-05":59.82, "2020-02-29":65.80, "2023-09-30":68.51}


# Právo na pravdu (Zoroslav Kollár, od 2025) — vlastný stĺpec od r. 2026.
# Primárne: AKO jún a júl 2026 (PDF), Infostat marec 2026 (PDF), NMS júl 2026 (článok).
# Ostatné z agregátora PolitPro — označené v QA_report.md ako čakajúce na kontrolu.
PRAVDA = {
 ("NMS","2026-01-11"):1.5, ("Ipsos","2026-01-20"):1.7, ("AKO","2026-01-20"):2.6,
 ("NMS","2026-02-08"):2.0, ("Focus","2026-02-09"):1.9, ("AKO","2026-02-19"):2.2,
 ("Ipsos","2026-02-20"):1.3,
 ("NMS","2026-03-09"):1.6, ("Infostat","2026-03-13"):1.0, ("AKO","2026-03-18"):2.3,
 ("NMS","2026-04-13"):1.6, ("Ipsos","2026-04-22"):1.4,
 ("NMS","2026-05-10"):2.7, ("Focus","2026-05-11"):2.0, ("AKO","2026-05-21"):2.1,
 ("AKO","2026-06-18"):2.3, ("Focus","2026-06-29"):2.7,
 ("NMS","2026-07-06"):2.3, ("AKO","2026-07-14"):2.8, ("Infostat","2026-07-17"):1.5,
}

def apply_pravda(recs):
    n=0
    for r in recs:
        v=PRAVDA.get((r["agency"], r["end"]))
        if v is not None:
            r["pravda"]=v
            if r.get("oth") is not None: r["oth"]=round(r["oth"]-v, 2)
            n+=1
    print(f"Právo na pravdu doplnené pri {n} riadkoch")
    return recs

def apply_turnout(recs):
    n=0
    for r in recs:
        end=r["end"]
        if r["agency"]=="AKO" and end in TURNOUT_AKO:
            d=TURNOUT_AKO[end]
            r.update(d)
            r["ucast"]=round(100.0-d["nerozhodnuti"]-d["nesli_by"]-d["odmietli"], 2)
            r["ucast_typ"]="rozhodnuti"; n+=1
        elif r["agency"]=="NMS" and end[:7] in TURNOUT_NMS:
            r["ucast"]=TURNOUT_NMS[end[:7]]; r["ucast_typ"]="deklarovana"; n+=1
        elif r["agency"]=="Focus" and end in TURNOUT_FOCUS:
            d=TURNOUT_FOCUS[end]
            r.update(d)
            r["ucast"]=round(100.0-d["nerozhodnuti"]-d["nesli_by"], 2)
            r["ucast_typ"]="rozhodnuti"; n+=1
        elif r["agency"]=="Infostat" and end[:7] in TURNOUT_INFOSTAT:
            r["ucast"]=TURNOUT_INFOSTAT[end[:7]]; r["ucast_typ"]="deklarovana_sirsia"; n+=1
        elif r["type"]=="election" and end in TURNOUT_ELECTION:
            r["ucast"]=TURNOUT_ELECTION[end]; r["ucast_typ"]="volby"; n+=1
    print(f"účasť doplnená pri {n} riadkoch")
    return recs

# farby zosvetlené pre tmavý fialový podklad (aby boli čitateľné na #3b2359)
PARTY_META = {
 "smer": ("Smer-SD", "#ff7b80"),
 "hlas": ("Hlas-SD", "#e0a0a0"),
 "ps":   ("PS", "#2ccbef"),
 "olano":("Slovensko (OĽaNO)", "#a9dc5f"),
 "sas":  ("SaS", "#d3ec3a"),
 "kdh":  ("KDH", "#f5bb64"),
 "sns":  ("SNS", "#6f9fe0"),
 "rep":  ("Republika", "#4faa4a"),
 "ali":  ("SMK / Maď. aliancia", "#c79ada"),
 "dem":  ("Demokrati", "#5fb2ea"),
 "sr":   ("Sme rodina", "#f8d34a"),
 "lsns": ("ĽSNS", "#3aa87a"),
 "zl":   ("Za ľudí", "#f07ec0"),
 "ku":   ("KÚ", "#b6b6b6"),
 "spolu":("SPOLU", "#8dbdea"),
 "modri":("Modrí, Európske Slovensko", "#8a7ff0"),
 "dv":   ("Dobrá voľba", "#93c9c9"),
 "mf":   ("Maď. fórum", "#cfb2e4"),
 "mh":   ("Most-Híd", "#ff9d55"),
 "siet": ("SIEŤ", "#57c3ef"),
 "vlast":("Vlasť", "#c78d52"),
 "pravda":("Právo na pravdu", "#d98f6a"),
 "oth":  ("Iné", "#c8c8c8"),
}

VERZIA = "2026-07-28.3"   # meniť pri každej zmene dát; zapisuje sa do JSON aj do index.html

def main():
    f1,p1=parse_file1()
    f2,p2=parse_file2()
    f3,p3=parse_file3()
    # dedupe: 2023 election in both files1&2; 2020 election in files2&3 -> keep one
    f2=[r for r in f2 if not (r["typ"]=="election" and r["d1"].year==2023)]
    f3=[r for r in f3 if not (r["typ"]=="election" and r["d1"].year==2020)]
    NORM={"Ako":"AKO","Polis Slovakia":"Polis","2023 elections":"Voľby 2023",
          "2023 election":"Voľby 2023","2020 elections":"Voľby 2020",
          "2016 elections":"Voľby 2016","European election":"Eurovoľby 2024"}
    for r in f1+f2+f3: r["agency"]=NORM.get(r["agency"], r["agency"])
    print(f"file3 rows: {len(f3)} ok, {len(p3)} problems")
    for p in p3: print("  P3!", p)
    allr = f3+f2+f1+EXTRA
    allr.sort(key=lambda r:(r["d2"],r["d1"]))
    print(f"file1 rows: {len(f1)} ok, {len(p1)} problems")
    for p in p1: print("  P1!", p)
    print(f"file2 rows: {len(f2)} ok, {len(p2)} problems")
    for p in p2: print("  P2!", p)
    # dedupe check + export
    recs=[]
    for r in allr:
        mid = r["d1"] + (r["d2"]-r["d1"])//2
        recs.append(dict(agency=r["agency"], start=r["d1"].isoformat(), end=r["d2"].isoformat(),
                         mid=mid.isoformat(), sample=r["sample"].replace("—",""), type=r["typ"],
                         **{k:v for k,v in r["vals"].items()}))
    PARTY_COLS=set(PARTY_META.keys())
    recs=apply_qa(recs, PARTY_COLS)
    recs=apply_pravda(recs)
    recs=apply_turnout(recs)
    TURN_COLS=["ucast","ucast_typ","nerozhodnuti","nesli_by","odmietli","neuplny"]
    cols=["agency","start","end","mid","sample","type"]+list(PARTY_META.keys())+TURN_COLS
    with open(f"{BASE}/polls_sk.csv","w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in recs: w.writerow({c:r.get(c,"") for c in cols})
    with open(f"{BASE}/polls_sk.json","w",encoding="utf-8") as f:
        json.dump(dict(verzia=VERZIA, meta={k:dict(name=v[0],color=v[1]) for k,v in PARTY_META.items()},
                       polls=recs), f, ensure_ascii=False, indent=1)
    print(f"VERZIA {VERZIA}")
    print(f"TOTAL exported: {len(recs)} rows, {recs[0]['mid']} .. {recs[-1]['mid']}")
    # per-agency counts
    from collections import Counter
    print(Counter(r["agency"] for r in recs))

if __name__=="__main__":
    main()
