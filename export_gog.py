#!/usr/bin/env python3

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path.home() / "gathersteam/input/gog-galaxy.db"
OUTPUT_PATH = Path.home() / "gathersteam/output/gog_games.csv"


def parse_json(value):
    if not value:
        return {}

    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}


def extract_title(value):
    data = parse_json(value)

    if isinstance(data, dict):
        title = data.get("title")
        if isinstance(title, str):
            return title.strip()

    if isinstance(value, str):
        return value.strip()

    return ""


def extract_names(value):
    if not value:
        return []

    if isinstance(value, str):
        return [value.strip()] if value.strip() else []

    if isinstance(value, list):
        result = []

        for item in value:
            if isinstance(item, str) and item.strip():
                result.append(item.strip())

            elif isinstance(item, dict):
                name = (
                    item.get("name")
                    or item.get("title")
                    or item.get("value")
                )

                if isinstance(name, str) and name.strip():
                    result.append(name.strip())

        return result

    if isinstance(value, dict):
        name = (
            value.get("name")
            or value.get("title")
            or value.get("value")
        )

        if isinstance(name, str) and name.strip():
            return [name.strip()]

    return []


def extract_field(meta, *keys):
    for key in keys:
        if key not in meta:
            continue

        value = meta[key]

        if isinstance(value, str):
            value = value.strip()
            if value:
                return value

        elif isinstance(value, (int, float)):
            return str(value)

        elif isinstance(value, list):
            values = extract_names(value)
            if values:
                return "; ".join(dict.fromkeys(values))

        elif isinstance(value, dict):
            values = extract_names(value)
            if values:
                return "; ".join(dict.fromkeys(values))

    return ""


def epoch_to_date(value):
    if value in (None, "", 0):
        return ""

    try:
        timestamp = float(value)
        return datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc,
        ).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError):
        return str(value)


def epoch_to_datetime(value):
    if value in (None, "", 0):
        return ""

    try:
        timestamp = float(value)
        return datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc,
        ).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError):
        return str(value)


def get_game_piece_types(conn):
    rows = conn.execute(
        "SELECT id, type FROM GamePieceTypes"
    ).fetchall()

    return {row[1]: row[0] for row in rows}


def get_game_pieces(conn, release_keys, type_id):
    if not release_keys or type_id is None:
        return {}

    placeholders = ",".join("?" for _ in release_keys)

    rows = conn.execute(
        f"""
        SELECT releaseKey, value
        FROM GamePieces
        WHERE gamePieceTypeId = ?
          AND releaseKey IN ({placeholders})
        """,
        [type_id, *release_keys],
    ).fetchall()

    return {row[0]: row[1] for row in rows}


def main():
    if not DB_PATH.exists():
        raise SystemExit(f"Database not found: {DB_PATH}")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)

    try:
        piece_types = get_game_piece_types(conn)

        title_type = (
            piece_types.get("originalTitle")
            or piece_types.get("title")
        )

        meta_type = piece_types.get("meta")

        if title_type is None:
            raise SystemExit("Could not find title GamePiece type.")

        if meta_type is None:
            raise SystemExit("Could not find meta GamePiece type.")

        # ------------------------------------------------------------
        # Determine the Galaxy user ID.
        # ------------------------------------------------------------

        user_row = conn.execute(
            """
            SELECT lr.userId
            FROM LicensedReleases owned
            JOIN LibraryReleases lr
                ON lr.id = owned.libraryId
            WHERE owned.isOwned = 1
              AND lr.releaseKey LIKE 'gog_%'
            LIMIT 1
            """
        ).fetchone()

        if not user_row:
            raise SystemExit("Could not determine Galaxy user ID.")

        user_id = user_row[0]

        print(f"Galaxy user ID: {user_id}")

        # ------------------------------------------------------------
        # Get owned GOG releases.
        # ------------------------------------------------------------

        rows = conn.execute(
            """
            SELECT lr.releaseKey
            FROM LicensedReleases owned
            JOIN LibraryReleases lr
                ON lr.id = owned.libraryId
            WHERE owned.isOwned = 1
              AND lr.releaseKey LIKE 'gog_%'
            ORDER BY lr.releaseKey
            """
        ).fetchall()

        release_keys = [row[0] for row in rows]

        print(f"GOG release records: {len(release_keys)}")

        # ------------------------------------------------------------
        # Titles and metadata.
        # ------------------------------------------------------------

        titles_raw = get_game_pieces(
            conn,
            release_keys,
            title_type,
        )

        metas_raw = get_game_pieces(
            conn,
            release_keys,
            meta_type,
        )

        # ------------------------------------------------------------
        # Playtime.
        # ------------------------------------------------------------

        game_times = {
            row[0]: row[1]
            for row in conn.execute(
                """
                SELECT releaseKey, minutesInGame
                FROM GameTimes
                WHERE userId = ?
                """,
                (user_id,),
            ).fetchall()
        }

        # ------------------------------------------------------------
        # Last played.
        # ------------------------------------------------------------

        last_played = {
            row[0]: row[1]
            for row in conn.execute(
                """
                SELECT gameReleaseKey, lastPlayedDate
                FROM LastPlayedDates
                WHERE userId = ?
                """,
                (user_id,),
            ).fetchall()
        }

        # ------------------------------------------------------------
        # Purchase / added dates.
        # ------------------------------------------------------------

        purchase_dates = {
            row[0]: (row[1], row[2])
            for row in conn.execute(
                """
                SELECT gameReleaseKey, purchaseDate, addedDate
                FROM ProductPurchaseDates
                WHERE userId = ?
                """,
                (user_id,),
            ).fetchall()
        }

        # ------------------------------------------------------------
        # Build games.
        #
        # Filter:
        #   1. Must have a developer
        #   2. Must have at least one genre
        #
        # This removes obvious DLC / bonus / entitlement records while
        # retaining actual games with otherwise sparse metadata.
        # ------------------------------------------------------------

        games = []
        filtered_no_developer = 0
        filtered_no_genre = 0

        for release_key in release_keys:

            title = extract_title(
                titles_raw.get(release_key)
            )

            if not title:
                continue

            meta = parse_json(
                metas_raw.get(release_key)
            )

            if not isinstance(meta, dict):
                meta = {}

            developers = extract_field(
                meta,
                "developers",
                "developer",
            )

            if not developers:
                filtered_no_developer += 1
                continue

            publishers = extract_field(
                meta,
                "publishers",
                "publisher",
            )

            genres = extract_field(
                meta,
                "genres",
                "genre",
            )

            # No genre = almost certainly DLC / bonus / entitlement
            # rather than a standalone game.
            if not genres:
                filtered_no_genre += 1
                continue

            themes = extract_field(
                meta,
                "themes",
                "theme",
            )

            critics_score = extract_field(
                meta,
                "criticsScore",
                "criticScore",
            )

            release_date_raw = extract_field(
                meta,
                "releaseDate",
            )

            release_date = epoch_to_date(
                release_date_raw
            )

            # --------------------------------------------------------
            # Playtime
            # --------------------------------------------------------

            minutes = game_times.get(
                release_key,
                0,
            )

            try:
                minutes = float(minutes or 0)
            except (TypeError, ValueError):
                minutes = 0

            playtime_hours = round(
                minutes / 60,
                1,
            )

            # --------------------------------------------------------
            # Last played
            # --------------------------------------------------------

            last_played_value = last_played.get(
                release_key,
                "",
            )

            if isinstance(last_played_value, (int, float)):
                last_played_value = epoch_to_datetime(
                    last_played_value
                )

            # --------------------------------------------------------
            # Purchase / added dates
            # --------------------------------------------------------

            purchase_date, added_date = purchase_dates.get(
                release_key,
                ("", ""),
            )

            if isinstance(purchase_date, (int, float)):
                purchase_date = epoch_to_datetime(
                    purchase_date
                )

            if isinstance(added_date, (int, float)):
                added_date = epoch_to_datetime(
                    added_date
                )

            # --------------------------------------------------------
            # GOG ID / URL
            # --------------------------------------------------------

            gog_id = release_key.removeprefix("gog_")

            games.append(
                {
                    "title": title,
                    "gog_id": gog_id,
                    "developers": developers,
                    "publishers": publishers,
                    "genres": genres,
                    "themes": themes,
                    "critics_score": critics_score,
                    "release_date": release_date,
                    "playtime_hours": playtime_hours,
                    "last_played": last_played_value or "",
                    "purchase_date": purchase_date or "",
                    "added_date": added_date or "",
                    "gog_url": f"https://www.gog.com/game/{gog_id}",
                }
            )

        # ------------------------------------------------------------
        # Sort alphabetically.
        # ------------------------------------------------------------

        games.sort(
            key=lambda game: game["title"].lower()
        )

        # ------------------------------------------------------------
        # Write CSV.
        # ------------------------------------------------------------

        fieldnames = [
            "title",
            "gog_id",
            "developers",
            "publishers",
            "genres",
            "themes",
            "critics_score",
            "release_date",
            "playtime_hours",
            "last_played",
            "purchase_date",
            "added_date",
            "gog_url",
        ]

        with OUTPUT_PATH.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as f:
            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
            )

            writer.writeheader()
            writer.writerows(games)

        # ------------------------------------------------------------
        # Statistics.
        # ------------------------------------------------------------

        games_with_playtime = sum(
            1
            for game in games
            if game["playtime_hours"] > 0
        )

        games_with_last_played = sum(
            1
            for game in games
            if game["last_played"]
        )

        games_with_publisher = sum(
            1
            for game in games
            if game["publishers"]
        )

        games_with_release_date = sum(
            1
            for game in games
            if game["release_date"]
        )

        print(f"Filtered out (no developer): {filtered_no_developer}")
        print(f"Filtered out (no genre):     {filtered_no_genre}")
        print(f"Exported games:              {len(games)}")
        print(f"With playtime > 0:           {games_with_playtime}")
        print(f"With last played:             {games_with_last_played}")
        print(f"With publisher:               {games_with_publisher}")
        print(f"With release date:            {games_with_release_date}")
        print(f"CSV: {OUTPUT_PATH}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()