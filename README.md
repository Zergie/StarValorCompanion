# Star Valor Companion

A native Windows desktop dashboard written in Python and Tkinter. It reads the
running Star Valor process and displays the active ship's calculated stats,
installed equipment, ship modifications, crew efficiencies, and memory fields.
The Trading tab shows nearby arbitrage routes: what quantity to buy, where to
buy it, where to sell it, prices, estimated profit, and route distance.

## Run

Use **64-bit Python 3.11 or newer**, with Tkinter installed (included in the normal
Windows Python installer). No pip packages are required.

Double-click **Launch.cmd**, or run:

```powershell
python app.py
```

Start Star Valor and load your save. The first connection can take roughly a
minute while the app discovers the Mono player metadata. The window stays
responsive and shows progress. Later connections in the same game session use a
validated cache. Scene changes and ship changes follow the active player again.

**Pause** stops periodic reads. **Reconnect** forces fresh discovery. **Export
snapshot** saves the displayed sample, including its timestamp, as JSON.
The Memory map tab shows field offsets, current addresses, types, and values.

## Trading routes

Open **Trading** and click **Refresh routes** to read live markets. Routes refresh
only when you click that button. A separate worker reads
markets so the ship dashboard stays responsive. The default minimum profit is
1,000 credits; set it to zero to include every positive-margin opportunity.

Routes include discovered, intact NPC stations with non-hostile faction standing.
They account for live stock, buying limits, current credits, free cargo space,
the Cargo Optimization skill, and item-specific cargo reservations. The shortest
total straight-line sector distance (current → buying station → selling station)
ranks first. Same-sector routes use local station distances to break ties. This
is not jumpgate pathfinding and does not deduct warp costs. The local player
position tie-breaker uses the game's latest synchronized position.

Prices are current quotes, not guaranteed arrival prices. A station's buy-only
and sell-only prices use its current stock and the installed game's price formula.
Two-way markets use the live sector trading price. Markets whose sector price is
not loaded are omitted and counted in the tab. Up to the nearest 5,000 candidates
are retained, with 100 matching routes displayed. Player-owned stations are
excluded so moving credits within your own holdings is not presented as profit.

On quit, window position, size, and maximized state are saved in
`.local/window.json`. They are restored on startup. If a monitor was disconnected,
the saved bounds are moved into the nearest available monitor's work area.

## Reading model

The application requests `PROCESS_QUERY_INFORMATION | PROCESS_VM_READ` only.
It never requests write, remote-thread, or VM-operation access, and does not inject
code, call functions in the game, or change saves. Discovery reads private
committed regions and follows Mono class metadata to `PlayerControl.inst`.
The selected player and ship must have live Unity native handles and the ship's
`isPlayerCharacter` flag. Current hull and reactor heat come from `SpaceShip`,
calculated bonuses from `ShipStats`, and cargo capacity from `CargoSystem`.
Shield capacity includes the live shield power setting and engineer modifier.
Energy generation and passive draw are raw stats; they are not a net-energy
calculation and exclude some action costs. Cargo shown is capacity, not used cargo.

Game memory can change during a read. The app validates the player again at the
end of each sample and retries during loading. Old values remain visible with a
disconnected status and their last-snapshot timestamp. Addresses change across
sessions; field offsets come from the running Mono metadata. A future change to
Mono's own native metadata layout may require a reader update.

The name catalog was extracted from Star Valor **2.2.5a, Steam build 25793649**.
Unknown equipment or modifications remain visible by ID. Future game updates may
require a refreshed catalog.

## Selling cargo and resources

Open **Sell cargo** and click **Refresh cargo & prices**. The left list shows
loaded cargo and the shared resource stash separately. Click an item's checkbox
or select it and press Space to exclude it. Exclusions protect both loaded and
stashed copies of that item and quality. They are saved immediately in
`.local/sale-exclusions.json`, survive restarts, and stay visible at zero quantity
when an item is no longer present. Check the item again to include it.

Each plan visits one discovered, intact, non-hostile NPC market station. It sells
all selected items that station buys, ranking by total proceeds, then distance.
Enable **Allow discarding items this station does not buy** to consider stations
that cannot buy the entire selection. The detail panel explicitly lists every
sale and required discard. Excluded items are always kept. This is a read-only
plan: the app never sells, destroys, or moves game items.

Stash resources are included directly, without applying ship cargo capacity.
Goods use current station quotes; equipment and weapons include rarity, resale
skills, and the ship trade modifier. Packaged ships include their model, traits,
equipment and contents where readable. Special-data items and unknown prices
are marked **REVIEW**, never discard. Incomplete quotes rank after complete
plans, and their totals show only the known proceeds. Exclude review items for
a complete comparison. Prices are snapshot estimates before travel costs and
may change before arrival. Refreshes are manual, including on this tab.


## Buying items

Open **Buy items** and click **Refresh items** to list purchase offers from
discovered, intact, non-hostile NPC markets whose listings are loaded in memory.
Goods, equipment, weapons and ships are included. Each row shows item, quality,
available station quantity, unit price, station/sector and straight-line distance
from your position at the time of refresh. Unlimited stock is labeled explicitly.
Quantity is not restricted by your current credits or cargo capacity.

Search matches item names, types, qualities, station names, factions, IDs and
sector coordinates. Multiple search words must all match. Click column headings
to sort, for example by lowest unit price or nearest station. Search and sorting
use the current snapshot; they never refresh game memory. Refresh is manual.

Prices for equipment, weapons and ships include reputation rank, purchase skill
and ship trade modifiers. Reputation and docking requirements still apply.
Special item data and unavailable quotes remain listed with **Unknown** prices.
Unexplored stations and markets without loaded listings cannot be listed. Quotes
and quantities may change before arrival.

## Diagnostics

```powershell
python app.py --probe --output snapshot.json
python app.py --probe --fresh --output snapshot.json
python -m unittest discover -s tests -v
```

Connection cache and rotating diagnostic logs live in `.local` beside the app.
If the game runs as administrator, Windows may require launching this app at the
same privilege level. Normal launches do not request administrator access.
