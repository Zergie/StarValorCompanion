---
name: star-valor-companion
description: Inspect the running Star Valor ship, equipment, modifications, cargo and resource stash, and plan trades, cargo sales or purchases through the local Star Valor Companion CLI. Use for this game's live state and companion preferences, rather than general game knowledge.
---

Use the installed Windows CLI at `C:\GIT\StarValorCompanion\cli.py`. It reuses the desktop app's readers and calculations. No game state is modified.

Run commands with Python and parse stdout as JSON. Progress goes to stderr. Successful results have `{ "command": "...", "data": ... }`. Use `python C:\GIT\StarValorCompanion\cli.py COMMAND --help` for options. Each read is a single snapshot; commands never start automatic market refreshes.

## Choose a command

```powershell
python C:\GIT\StarValorCompanion\cli.py ship
python C:\GIT\StarValorCompanion\cli.py ship --section equipment --search drone
python C:\GIT\StarValorCompanion\cli.py ship --section modifications
python C:\GIT\StarValorCompanion\cli.py ship --section crew
python C:\GIT\StarValorCompanion\cli.py ship --section memory --search cargo
python C:\GIT\StarValorCompanion\cli.py inventory
python C:\GIT\StarValorCompanion\cli.py trades --min-profit 1000 --limit 10
python C:\GIT\StarValorCompanion\cli.py sell --limit 5
python C:\GIT\StarValorCompanion\cli.py buy --search "mining drone" --sort price
python C:\GIT\StarValorCompanion\cli.py catalog --category equipment --search drone
```

- `ship` defaults to overview summary values. `--section all` returns the complete ship snapshot, including equipment, modifications, crew and memory map. `--output PATH` also saves the returned JSON.
- `inventory` includes every living ship in the player's fleet roster, including docked/hangar/remote ships. Rows expose `active_loaded`, `fleet_loaded`, combined `loaded`, `stash`, and per-ship `sources`; the shared stash is counted once. Stable keys and saved exclusions apply across the fleet, including absent items. Crew and station storage are ignored. Sale plans assume fleet cargo is brought to the chosen station; retrieval costs and fleet docking restrictions are not calculated.
- `trades` ranks current → buy → sell by straight-line sector distance, local travel, then profit; it does not maximize profit first. At most 5,000 candidates are retained. `--limit 0` returns all retained matches.
- `sell` ranks single-station plans by total proceeds, then distance. Complete quotes precede incomplete quotes. SELL lines are ordered by total value descending; rejected goods and unknown quotes remain distinct.
- `buy` includes goods, equipment, weapons and ships. Search also matches quality, station, faction and sector. Quantity is station stock, not affordable/loadable quantity; `null` quantity means unlimited, `null` price means unknown. Sort by distance, price or quantity, optionally `--descending`.
- `catalog` works without the game and returns static data. Catalog prices are base prices, not live station quotes.

## Preferences and exclusions

`sell` respects saved GUI exclusions and the discard checkbox. Prefer per-command overrides for one-off scenarios; persist changes when the user asks to change their preferences.

```powershell
# Temporary overrides; do not save preference changes.
python C:\GIT\StarValorCompanion\cli.py sell --allow-discard --exclude 3:23:1
python C:\GIT\StarValorCompanion\cli.py sell --no-allow-discard --include 3:23:1

# Persistent preferences shared with the desktop app.
python C:\GIT\StarValorCompanion\cli.py preferences
python C:\GIT\StarValorCompanion\cli.py preferences --allow-discard
python C:\GIT\StarValorCompanion\cli.py preferences --no-allow-discard
python C:\GIT\StarValorCompanion\cli.py exclude list
python C:\GIT\StarValorCompanion\cli.py exclude add 3:23:1 --name "Drone Parts"
python C:\GIT\StarValorCompanion\cli.py exclude remove 3:23:1
```

Copy keys from `inventory`; they are `TYPE:ID:QUALITY`. Add/remove is idempotent. The GUI loads saved preferences on startup; reopen an already-running GUI to pick up CLI changes. Excluded items are kept. Allowing discard produces a plan only; do not claim cargo was sold or destroyed.

## Snapshots, failures and GUI

For repeated comparisons, export a reusable raw snapshot, then use `--input` to avoid game reads:

```powershell
python C:\GIT\StarValorCompanion\cli.py sell --raw --output sale-snapshot.json
python C:\GIT\StarValorCompanion\cli.py sell --input sale-snapshot.json --allow-discard --limit 5
python C:\GIT\StarValorCompanion\cli.py ship --section all --output ship-snapshot.json
```

`--raw` is available on all read commands. Use the matching command's raw snapshot for `--input`; ship-only exports lack market data. Filtered outputs usually omit information needed to re-plan.

Live reads require the game running with a loaded save. Initial discovery can take a minute. `--fresh` forces player rediscovery; `--timeout 120` cancels discovery/scans after that duration. Failure leaves stdout empty, with a JSON error on stderr: exit 2 means game/memory unavailable, 124 means cancelled/timed out, 1 means invalid data or I/O, and 130 means interrupted. Usage errors also exit 2. Report unavailable data rather than presenting saved data as current. Do not retry discovery indefinitely.

Markets cover discovered, intact, non-hostile NPC stations with loaded listings. Prices/stock may change before arrival; travel costs are excluded. REVIEW means missing valuation, never a discard recommendation. Energy consumption is passive draw, and speed is the ship's speed stat, not instantaneous velocity.

`gui --tab "Sell cargo"`, `gui --tab Overview`, `gui --paused`, and `gui --keep-on-top` launch the desktop app. Pause/resume, window placement, and changing an existing window remain GUI controls. One-shot reads stop when they return, so no CLI pause is needed. The CLI cannot control an already-running GUI session.
