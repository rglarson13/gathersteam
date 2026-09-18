import os
import base64
import json
from collections import Counter

path = os.environ.get("EPIC_CATCACHE", "/mnt/c/ProgramData/Epic/EpicGamesLauncher/Data/Catalog/catcache.bin")

with open(path, "rb") as f:
    data = json.loads(base64.b64decode(f.read()))

print(f"Total records: {len(data):,}")

print("\n=== Categories ===")
categories = Counter()
for x in data:
    for c in x.get("categories", []):
        categories[c.get("path", "")] += 1

for k, v in categories.most_common():
    print(f"{v:5}  {k}")

print("\n=== Records with key fields ===")
for field in ["id", "namespace", "entitlementName", "title", "developer",
              "releaseInfo", "customAttributes", "mainGameItem", "dlcItemList"]:
    count = sum(1 for x in data if field in x)
    print(f"{field:25} {count:5}")

print("\n=== Custom attribute keys ===")
attrs = Counter()
for x in data:
    attrs.update(x.get("customAttributes", {}).keys())

for k, v in attrs.most_common():
    print(f"{v:5}  {k}")

print("\n=== Sample titles ===")
for x in data[:30]:
    print(f"{x.get('title', '')}")

print("\n=== Likely game records ===")
games = []

for x in data:
    cats = {c.get("path", "") for c in x.get("categories", [])}
    title = x.get("title", "")

    # Show records that look like games rather than generic software.
    if "games" in cats or "game" in cats:
        games.append(x)

print(f"Records categorized as games: {len(games):,}")

for x in games[:50]:
    print(f"{x.get('title', '')}  [{x.get('namespace', '')}]")

print("\n=== Titles containing common free-game examples ===")
needles = [
    "A Short Hike",
    "Dredge",
    "Unpacking",
    "Mini Metro",
    "Death Stranding",
    "Subnautica",
    "Civilization",
    "Control",
]

for needle in needles:
    matches = [x for x in data if needle.lower() in x.get("title", "").lower()]
    print(f"\n{needle}: {len(matches)} match(es)")
    for x in matches:
        print(f"  {x.get('title')} [{x.get('namespace')}]")
