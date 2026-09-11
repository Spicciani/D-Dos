# D-DOS

Dungeon crawl testuale in stile DOS, multiplayer, giocabile da un gruppo
Telegram. Il nome è il gioco: **D**&D + **DOS**.

Una compagnia di 2-6 persone scende in un sotterraneo generato
proceduralmente, combatte a turni con regole d20 semplificate e prova a
uccidere quello che c'è in fondo. Chi muore resta morto, e finisce nel
cimitero del gruppo.

```
     ___     ___   ___  ___
    |   \ __|   \ / _ \/ __|
    | |) |__| |) | (_) \__ \
    |___/   |___/ \___/|___/
  dungeon crawl per compagnie

╔═ TANA DEL BOSS ══════════════╗
║ La sala si apre enorme. In   ║
║ fondo, qualcosa vi           ║
║ aspettava.                   ║
║                              ║
║ Uscite: Ovest'               ║
╚══════════════════════════════╝
╔═ NEMICI ═════════════════════╗
║ A Orcus, Signo~ [████░░]     ║
║                              ║
║ Round 3..........turno: Bard ║
╚══════════════════════════════╝
╔═ COMPAGNIA ══════════════════╗
║  *Grommash GUE L3 [███░░░]   ║
║     14/23 CA16               ║
║   Silfa    LAD AGONIA 0/3    ║
║   Zilla    MAG AGONIA 0/3    ║
║ > Bard     CHI L3 [██████]   ║
║     19/19 CA16 slot5/6       ║
║ Oro comune...............155 ║
╚══════════════════════════════╝
```

## Provalo subito, senza Telegram

Il motore è indipendente dal bot, quindi si gioca da terminale senza token e
senza dipendenze esterne:

```bash
python -m ddos.cli.play --party "Grommash:g,Silfa:l,Zilla:m,Bard:c"
python -m ddos.cli.play --seed ORCUS-4471      # stesso sotterraneo, sempre
python -m ddos.cli.play                        # crea la compagnia a mano
```

Scrivi `aiuto` al prompt per i comandi. In combattimento si gioca hot-seat: la
tastiera passa a chi ha l'iniziativa, e lo dice il prompt (`[R1 Silfa]>`).

Fuori dal combattimento il comando va da sé a chi sa eseguirlo — `spia` al
Ladro, `lancia cura` al Chierico, `usa` a chi ha l'oggetto nello zaino — mentre
muoversi, riposare e scendere restano decisioni di chi guida la compagnia. Per
scegliere a mano, un nome davanti:

```
silfa: spia n
zilla: usa pozione_cura Bard
```

## Il bot Telegram

```bash
pip install -e .
export BOT_TOKEN=...      # da @BotFather
python -m ddos.bot.main
```

Aggiungi il bot a un gruppo, poi:

| Comando | Cosa fa |
|---|---|
| `/nuova [seme] [lento]` | apre una spedizione nel gruppo |
| `/entra <nome> [classe]` | crea il tuo personaggio |
| `/via` | si scende |
| `/scheda` | la tua scheda, in privato |
| `/mappa`, `/stato` | mappa del livello, ridisegna la scena |
| `/capo <nome>` | passa il comando della compagnia |
| `/cimitero` | chi non è tornato |
| `/abbandona` | chiude la spedizione |

Durante il gioco si usano solo i bottoni sotto la scena. Muove chi guida la
compagnia; in combattimento gioca chi ha l'iniziativa; tutti gli altri
guardano.

**Scrivi `/start` al bot anche in privato.** Telegram non permette a un bot di
scrivere per primo a chi non gli ha mai parlato, e le schede e l'avanscoperta
del Ladro arrivano di là.

### Tre scelte che cambiano come si gioca

- **Un solo messaggio-scena**, modificato sul posto e fissato in alto, più una
  riga che chiama per nome chi deve giocare. Un bot che spamma il gruppo viene
  silenziato in due giorni.
- **Segreti in privato.** Quando il Ladro va in avanscoperta, quello che vede
  arriva solo a lui. È la cosa che fa sembrare vero il gioco di ruolo: uno del
  gruppo sa qualcosa che gli altri non sanno.
- **I bottoni non mentono e non impersonano.** Una callback può dichiarare di
  quale personaggio è (`Cura Ferite (Bard)`), ma il server *verifica* che a
  premerla sia chi interpreta Bard invece di fidarsi: nessuno può bruciare gli
  slot di un altro.
- **Semi condivisibili.** Ogni run ha un seme (`KORVAX-4471`). Due gruppi che
  usano lo stesso seme scendono nello stesso identico sotterraneo e possono
  confrontarsi. Viene gratis dal fatto che il motore è deterministico.

Modalità **lento** (`/nuova SEME lento`): 6 ore per turno invece di 90
secondi, per giocare qualche mossa durante la giornata.

## Le quattro classi

Ognuna sa fare qualcosa che nessun'altra sa fare, altrimenti la compagnia non
avrebbe ragione di esistere.

| Classe | PF | Sa fare solo lei |
|---|---|---|
| **Guerriero** | d10 | Colpo Poderoso, e Provocare per attirare i colpi su di sé |
| **Ladro** | d6 | Avanscoperta, disinnescare trappole, attacco furtivo |
| **Mago** | d4 | Dardo Incantato (non manca mai), Sonno, Scudo Arcano |
| **Chierico** | d8 | **L'unico che può rialzare chi è a terra.** Benedizione, Scacciare non-morti |

Il Mago parte con 3-5 PF. È voluto.

## Come è fatto

Il vincolo che guida tutto: **il motore non sa cosa sia Telegram.**

```
ddos/
  engine/     puro, deterministico, nessun I/O
    dice      generatore (seme, contatore) basato su hash
    entities  personaggi, mostri, oggetti
    content   classi, bestiario, incantesimi, tesori, trappole
    rules     colpire, danni, agonia, salvezze, PX
    dungeon   generatore di livelli
    state     stato serializzabile in JSON
    reduce    reduce(stato, azione) -> (nuovo stato, eventi)
  render/     stato -> schermate DOS a 32 colonne
  store/      SQLite: partite, giocatori, cronaca, cimitero
  bot/        Telegram (aiogram 3)
  cli/        client da terminale e banco di prova del bilanciamento
```

### Il motore è un riduttore puro

```python
nuovo_stato, eventi = reduce(stato, azione)
```

Tre garanzie, tutte coperte da test:

1. **Lo stato in ingresso non viene mai modificato.** Si lavora su una copia.
2. **Un'azione invalida non cambia niente e non consuma dadi.** Torna lo stato
   originale e un solo evento di errore.
3. **Niente randomicità nascosta.** L'RNG vive dentro lo stato come
   `(seme, contatore)`: la N-esima estrazione di una partita è sempre la
   stessa. Salvare, ricaricare e riprendere non cambia un solo dado.

Gli `Event` sono i fatti («Grommash colpisce il Goblin per 6 danni»). Il
renderer li traduce in schermate, i test li asseriscono, e se un giorno vorrai
un narratore AI riceverà eventi già risolti dal motore — quindi non potrà
barare sui dadi.

### La concorrenza, tre livelli

Due giocatori che premono insieme sono lo scenario normale, non l'eccezione:

1. un `asyncio.Lock` per chat contro i tap simultanei;
2. il numero di turno dentro ogni `callback_data`: se la scena è andata
   avanti, il tap viene rifiutato invece di applicare un'azione vecchia;
3. scrittura ottimistica su `turn_seq` nel database: se qualcun altro ha già
   scritto, la `save` fallisce invece di sovrascrivere.

Le callback non contengono *chi* agisce: lo decide il server dal `telegram_id`
di chi ha premuto, così un bottone non può impersonare nessuno.

### Le 32 colonne

Telegram rende i blocchi `<pre>` in monospace, ma su un telefono stretto tutto
ciò che supera ~32 caratteri va a capo e sfonda la cornice. Ogni funzione di
rendering rispetta il limite, e un test lo verifica su partite vere invece che
su stringhe inventate.

## Sviluppo

```bash
pip install -e ".[dev]"
python -m pytest                      # 284 test, ~10 secondi
python -m ddos.cli.bench --partite 40 # misura la difficoltà
```

I test che contano davvero:

- `test_fuzz.py` gioca partite casuali e verifica le invarianti a ogni passo.
  Ha già trovato un contatore di morte che sforava e una partita che si
  bloccava per sempre.
- `test_bot.py` usa un finto Telegram per verificare il doppio tap, la
  tastiera scaduta, che un segreto non finisca mai nel gruppo, e che nessuno
  possa agire col personaggio di un altro.
- `test_cli.py` verifica che fuori dal combattimento agisca chi sa fare quella
  cosa, non chi guida la compagnia.
- `test_render.py` verifica le 32 colonne su stati di gioco reali.

`ddos.cli.bench` gioca partite intere senza nessuno alla tastiera. Il bot non
è bravo — attacca il nemico più debole, non fugge mai, non cambia
equipaggiamento — quindi il suo tasso di vittoria va letto come il *pavimento*
della difficoltà, non come il comportamento di giocatori veri. Oggi: ~28% se
la compagnia ripulisce le stanze, ~12% se corre alla scala. Il divario è
voluto: esplorare deve pagare.

## Cosa manca

L'architettura le lascia aperte, ma non ci sono:

- narratore AI che veste di prosa gli eventi già risolti;
- città tra una run e l'altra (taverna, fabbro, personaggi persistenti);
- boss stagionale condiviso tra più gruppi, con classifica;
- voto a maggioranza sul movimento (oggi decide chi guida la compagnia);
- client web che rende lo stesso stato con i fosfori verdi.
