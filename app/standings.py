def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_conference_table(games, conference: str):
    """Compute conference and non-conference W-L-D records for every team
    in `conference`, from a list of final games involving that conference."""
    teams: dict[str, dict] = {}

    for g in games:
        home_score = _to_int(g["home_score"])
        away_score = _to_int(g["away_score"])
        if home_score is None or away_score is None:
            continue

        is_conf_game = g["home_conference"] == conference and g["away_conference"] == conference

        for side, opp_side in (("home", "away"), ("away", "home")):
            if g[f"{side}_conference"] != conference:
                continue

            seo = g[f"{side}_seo"]
            team_score = home_score if side == "home" else away_score
            opp_score = away_score if side == "home" else home_score

            team = teams.setdefault(
                seo,
                {
                    "seo": seo,
                    "name": g[f"{side}_name"],
                    "conf_w": 0,
                    "conf_l": 0,
                    "conf_d": 0,
                    "nc_w": 0,
                    "nc_l": 0,
                    "nc_d": 0,
                    "gf": 0,
                    "ga": 0,
                },
            )

            if team_score > opp_score:
                result = "w"
            elif team_score < opp_score:
                result = "l"
            else:
                result = "d"

            bucket = "conf" if is_conf_game else "nc"
            team[f"{bucket}_{result}"] += 1
            team["gf"] += team_score
            team["ga"] += opp_score

    rows = list(teams.values())
    for t in rows:
        t["conf_pts"] = 3 * t["conf_w"] + t["conf_d"]
        t["overall_w"] = t["conf_w"] + t["nc_w"]
        t["overall_l"] = t["conf_l"] + t["nc_l"]
        t["overall_d"] = t["conf_d"] + t["nc_d"]
        t["gd"] = t["gf"] - t["ga"]

    rows.sort(key=lambda t: (-t["conf_pts"], t["name"]))
    return rows


def build_all_teams_table(games):
    """Compute conference and non-conference W-L-D records for every team
    across every conference, from a list of all final games. Unlike
    build_conference_table, each team's conference-game bucket is determined
    by its own conference rather than a single conference passed in."""
    teams: dict[str, dict] = {}

    for g in games:
        home_score = _to_int(g["home_score"])
        away_score = _to_int(g["away_score"])
        if home_score is None or away_score is None:
            continue

        is_conf_game = (
            g["home_conference"] and g["home_conference"] == g["away_conference"]
        )

        for side, opp_side in (("home", "away"), ("away", "home")):
            conference = g[f"{side}_conference"]
            if not conference:
                continue

            seo = g[f"{side}_seo"]
            team_score = home_score if side == "home" else away_score
            opp_score = away_score if side == "home" else home_score

            team = teams.setdefault(
                seo,
                {
                    "seo": seo,
                    "name": g[f"{side}_name"],
                    "conference": conference,
                    "conf_w": 0,
                    "conf_l": 0,
                    "conf_d": 0,
                    "nc_w": 0,
                    "nc_l": 0,
                    "nc_d": 0,
                    "gf": 0,
                    "ga": 0,
                },
            )

            if team_score > opp_score:
                result = "w"
            elif team_score < opp_score:
                result = "l"
            else:
                result = "d"

            bucket = "conf" if is_conf_game else "nc"
            team[f"{bucket}_{result}"] += 1
            team["gf"] += team_score
            team["ga"] += opp_score

    rows = list(teams.values())
    for t in rows:
        t["conf_pts"] = 3 * t["conf_w"] + t["conf_d"]
        t["overall_w"] = t["conf_w"] + t["nc_w"]
        t["overall_l"] = t["conf_l"] + t["nc_l"]
        t["overall_d"] = t["conf_d"] + t["nc_d"]
        t["gd"] = t["gf"] - t["ga"]

    rows.sort(key=lambda t: t["name"])
    return rows


def build_team_schedule(games, seo: str, conference: str | None = None):
    """Annotate each of a team's games with opponent info and W/L/D result,
    and tally conference / non-conference / overall records."""
    rows = []
    record = {
        "conf_w": 0, "conf_l": 0, "conf_d": 0,
        "nc_w": 0, "nc_l": 0, "nc_d": 0,
    }

    for g in games:
        is_home = g["home_seo"] == seo
        team_score = _to_int(g["home_score"] if is_home else g["away_score"])
        opp_score = _to_int(g["away_score"] if is_home else g["home_score"])
        opponent_conference = g["away_conference"] if is_home else g["home_conference"]

        result = None
        if g["status"] == "final" and team_score is not None and opp_score is not None:
            if team_score > opp_score:
                result = "w"
            elif team_score < opp_score:
                result = "l"
            else:
                result = "d"
            bucket = "conf" if conference and opponent_conference == conference else "nc"
            record[f"{bucket}_{result}"] += 1

        rows.append(
            {
                "game": g,
                "is_home": is_home,
                "opponent_name": g["away_name"] if is_home else g["home_name"],
                "opponent_seo": g["away_seo"] if is_home else g["home_seo"],
                "opponent_conference": opponent_conference,
                "team_score": team_score,
                "opp_score": opp_score,
                "result": result,
            }
        )

    record["overall_w"] = record["conf_w"] + record["nc_w"]
    record["overall_l"] = record["conf_l"] + record["nc_l"]
    record["overall_d"] = record["conf_d"] + record["nc_d"]

    return rows, record


def build_player_game_log(rows):
    """Turn a player's per-game player_stats+games rows into a display-ready
    log plus season totals."""
    log = []
    totals = {"games": 0, "goals": 0, "assists": 0, "shots": 0, "shots_on_goal": 0}

    for r in rows:
        is_home = bool(r["is_home"])
        goals = _to_int(r["goals"]) or 0
        assists = _to_int(r["assists"]) or 0
        shots = _to_int(r["shots"]) or 0
        shots_on_goal = _to_int(r["shots_on_goal"]) or 0

        totals["games"] += 1
        totals["goals"] += goals
        totals["assists"] += assists
        totals["shots"] += shots
        totals["shots_on_goal"] += shots_on_goal

        log.append(
            {
                "game_id": r["game_id"],
                "date": r["date"],
                "is_home": is_home,
                "opponent_name": r["away_name"] if is_home else r["home_name"],
                "opponent_seo": r["away_seo"] if is_home else r["home_seo"],
                "opponent_conference": r["away_conference"] if is_home else r["home_conference"],
                "status": r["status"],
                "minutes_played": r["minutes_played"],
                "goals": goals,
                "assists": assists,
                "shots": shots,
                "shots_on_goal": shots_on_goal,
                "saves": r["saves"],
                "yellow_cards": r["yellow_cards"],
                "red_cards": r["red_cards"],
            }
        )

    return log, totals
