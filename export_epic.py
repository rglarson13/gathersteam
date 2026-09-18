#!/usr/bin/env python3

import base64
import csv
import json
from collections import defaultdict
from pathlib import Path


INPUT = Path.home() / "gathersteam/input/epic-catcache.bin"
OUTPUT = Path.home() / "gathersteam/output/epic_games.csv"


def decode_catcache(path):
    raw = path.read_bytes()

    # catcache.bin is Base64-encoded JSON.
    decoded = base64.b64decode(raw)

    return json.loads(decoded)


def get_platforms(record):
    platforms = set()

    for release in record.get("releaseInfo", []):
        for platform in release.get("platform", []):
            platforms.add(platform)

    # Some records may have platform information elsewhere.
    if not platforms:
        attrs = record.get("customAttributes", {})
        platform = attrs.get("platform")
        if platform:
            platforms.add(platform)

    return platforms


def get_categories(record):
    categories = record.get("categories", [])

    result = set()

    for category in categories:
        path = category.get("path")
        if path:
            result.add(path)

    return result


def main():
    records = decode_catcache(INPUT)

    print(f"Total catalog records: {len(records):,}")

    # Epic has a bunch of software, engines, DLC, etc. in the catalog.
    # We only want records categorized as games for this first pass.
    game_records = [
        r for r in records
        if "games" in get_categories(r)
    ]

    print(f"Game records:          {len(game_records):,}")

    # First group by namespace + normalized title.
    #
    # This intentionally does NOT group merely by namespace:
    # a namespace can contain multiple products (e.g. a base game,
    # expansion, map, editor, etc.).
    grouped = defaultdict(list)

    for record in game_records:
        namespace = record.get("namespace", "")

        title = (
            record.get("title")
            or record.get("entitlementName")
            or record.get("id")
            or ""
        ).strip()

        if not namespace or not title:
            continue

        key = (namespace, title.casefold())
        grouped[key].append(record)

    print(f"Namespace/title groups: {len(grouped):,}")

    output_rows = []

    for (namespace, _), group in grouped.items():

        # Prefer the longest/most complete title representation.
        titles = {
            (r.get("title") or r.get("entitlementName") or "").strip()
            for r in group
        }
        titles.discard("")

        title = max(titles, key=len) if titles else ""

        platforms = set()
        epic_ids = set()
        app_ids = set()
        developers = set()

        for record in group:
            platforms.update(get_platforms(record))

            record_id = record.get("id")
            if record_id:
                epic_ids.add(str(record_id))

            developer = record.get("developer")
            if developer:
                developers.add(str(developer))

            for release in record.get("releaseInfo", []):
                app_id = release.get("appId")
                if app_id:
                    app_ids.add(str(app_id))

        output_rows.append({
            "title": title,
            "epic_namespace": namespace,
            "platforms": "; ".join(sorted(platforms)),
            "developer": "; ".join(sorted(developers)),
            "epic_ids": "; ".join(sorted(epic_ids)),
            "app_ids": "; ".join(sorted(app_ids)),
            "catalog_records": len(group),
        })

    output_rows.sort(key=lambda r: r["title"].casefold())

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "title",
                "epic_namespace",
                "platforms",
                "developer",
                "epic_ids",
                "app_ids",
                "catalog_records",
            ],
        )
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"\nExported rows:        {len(output_rows):,}")
    print(f"Output:               {OUTPUT}")

    # Show groups that actually consolidated multiple catalog records.
    multi = [
        row for row in output_rows
        if row["catalog_records"] > 1
    ]

    print(f"Consolidated groups:  {len(multi):,}")

    if multi:
        print("\nExamples of consolidated records:")
        for row in multi[:20]:
            print(
                f"  {row['title']} "
                f"[{row['platforms']}] "
                f"({row['catalog_records']} catalog records)"
            )


if __name__ == "__main__":
    main()