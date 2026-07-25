#!/usr/bin/env python3
import os
import sys
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:
    import googlemaps
except ImportError:
    print("Missing 'googlemaps' library. Install it: pip install googlemaps")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from config import CONFIG


VEHICLE_NAMES = {
    "TRAM": "tram",
    "BUS": "bus",
    "TROLLEYBUS": "trolleybus",
    "SUBWAY": "subway",
    "METRO_RAIL": "metro",
    "RAIL": "train",
    "HEAVY_RAIL": "train",
    "COMMUTER_TRAIN": "commuter train",
    "FERRY": "ferry",
}

VEHICLE_ICONS = {
    "tram": "🚊",
    "bus": "🚌",
    "subway": "🚇",
    "train": "🚆",
    "ferry": "⛴",
    "tram + bus": "🚊🚌",
    "walking": "🚶",
}


@dataclass
class TransitStep:
    line: str
    vehicle: str
    departure_stop: str
    arrival_stop: str
    duration_min: int
    headsign: str

    def pretty_vehicle(self) -> str:
        return VEHICLE_NAMES.get(self.vehicle, self.vehicle.lower())


@dataclass
class Route:
    duration_min: int
    walk_min: int
    steps: List[TransitStep] = field(default_factory=list)
    departure_time: str = "?"
    arrival_time: str = "?"

    @property
    def transfers(self) -> int:
        return max(0, len(self.steps) - 1)

    @property
    def is_direct(self) -> bool:
        return len(self.steps) <= 1

    def vehicle_summary(self) -> str:
        if not self.steps:
            return "walking"
        kinds = {VEHICLE_NAMES.get(s.vehicle, s.vehicle.lower()) for s in self.steps}
        if kinds == {"tram"}:
            return "tram"
        if kinds == {"bus"}:
            return "bus"
        if kinds == {"tram", "bus"}:
            return "tram + bus"
        return " + ".join(sorted(kinds))


def next_weekday_at(hour: int, minute: int) -> datetime:
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target


def parse_route(raw_route: dict) -> Optional[Route]:
    if not raw_route or not raw_route.get("legs"):
        return None

    leg = raw_route["legs"][0]
    duration_min = round(leg["duration"]["value"] / 60)
    departure_time = leg.get("departure_time", {}).get("text", "?")
    arrival_time = leg.get("arrival_time", {}).get("text", "?")

    walk_seconds = 0
    steps: List[TransitStep] = []

    for step in leg["steps"]:
        if step["travel_mode"] == "WALKING":
            walk_seconds += step["duration"]["value"]
        elif step["travel_mode"] == "TRANSIT":
            t = step["transit_details"]
            line_info = t["line"]
            line = line_info.get("short_name") or line_info.get("name", "?")
            vehicle = line_info["vehicle"]["type"]
            steps.append(TransitStep(
                line=line,
                vehicle=vehicle,
                departure_stop=t["departure_stop"]["name"],
                arrival_stop=t["arrival_stop"]["name"],
                duration_min=round(step["duration"]["value"] / 60),
                headsign=t.get("headsign", ""),
            ))

    return Route(
        duration_min=duration_min,
        walk_min=round(walk_seconds / 60),
        steps=steps,
        departure_time=departure_time,
        arrival_time=arrival_time,
    )


def find_route(gmaps, origin: str, destination: str,
               target_time: datetime, mode: str) -> Optional[Route]:
    kwargs = {
        "origin": origin,
        "destination": destination,
        "mode": "transit",
        "transit_mode": CONFIG.transit_mode_param,
        "language": CONFIG.language,
    }
    if CONFIG.region:
        kwargs["region"] = CONFIG.region
    if mode == "arrival":
        kwargs["arrival_time"] = target_time
    else:
        kwargs["departure_time"] = target_time

    try:
        result = gmaps.directions(**kwargs)
    except googlemaps.exceptions.ApiError as e:
        print(f"  ! API error: {e}")
        return None
    except Exception as e:
        print(f"  ! Network error: {e}")
        return None

    if not result:
        return None
    return parse_route(result[0])


def direct_route(gmaps, origin: str, destination: str, mode: str) -> Optional[tuple]:
    """Door-to-door by a single mode ('walking', 'bicycling', 'driving'): (minutes, km).

    Distinct from Route.walk_min, which is only the walking legs *inside* a
    transit journey (getting to the stop and away from it).
    """
    kwargs = {"origin": origin, "destination": destination,
              "mode": mode, "language": CONFIG.language}
    if CONFIG.region:
        kwargs["region"] = CONFIG.region
    try:
        result = gmaps.directions(**kwargs)
    except Exception as e:
        print(f"  ! {mode} route error: {e}")
        return None
    if not result:
        return None
    leg = result[0]["legs"][0]
    return round(leg["duration"]["value"] / 60), round(leg["distance"]["value"] / 1000, 1)


def walking_route(gmaps, origin: str, destination: str) -> Optional[tuple]:
    return direct_route(gmaps, origin, destination, "walking")


def bicycling_route(gmaps, origin: str, destination: str) -> Optional[tuple]:
    """Krakow is flat and has riverside paths — a bike often halves the transit time."""
    return direct_route(gmaps, origin, destination, "bicycling")


def score_location(routes: Dict[str, Optional[Route]]) -> float:
    valid = [r for r in routes.values() if r is not None]
    if not valid:
        return 0.0
    avg = sum(r.duration_min for r in valid) / len(valid)
    transfers = sum(r.transfers for r in valid)
    s = CONFIG.scoring
    score = 10.0 - (avg / s.minutes_divisor) - transfers * s.transfer_penalty
    return max(0.0, min(10.0, score))


def score_label(score: float) -> str:
    if score >= 8:
        return "great location!"
    if score >= 6:
        return "good location"
    if score >= 4:
        return "average location"
    if score >= 2:
        return "poor location"
    return "very poor location"


def print_route(name: str, route: Optional[Route]) -> None:
    print(f"\n→ {name}")
    print("─" * 64)
    if route is None:
        print("  ✗ No route found.")
        return

    if route.is_direct and route.steps:
        direct_info = "direct"
    elif route.is_direct:
        direct_info = "walking only"
    else:
        n = route.transfers
        direct_info = f"{n} transfer" + ("s" if n != 1 else "")

    icon = VEHICLE_ICONS.get(route.vehicle_summary(), "🚍")

    print(f"  ⏱  Time:       {route.duration_min} min  ({direct_info})")
    print(f"  🚶 Walking:    {route.walk_min} min")
    print(f"  🕗 Departure:  {route.departure_time}")
    print(f"  🏁 Arrival:    {route.arrival_time}")
    print(f"  {icon}  Route:")
    if not route.steps:
        print("     (entire distance on foot)")
    for i, s in enumerate(route.steps, 1):
        print(f"     {i}. {s.pretty_vehicle()} {s.line}  →  {s.headsign}")
        print(f"        {s.departure_stop}  →  {s.arrival_stop}  ({s.duration_min} min)")


def print_summary(routes: Dict[str, Optional[Route]]) -> float:
    print("\n" + "=" * 64)
    print(" SUMMARY")
    print("=" * 64)
    valid = {n: r for n, r in routes.items() if r is not None}
    if not valid:
        print("  No data.")
        return 0.0
    total = sum(r.duration_min for r in valid.values())
    avg = total / len(valid)
    transfers = sum(r.transfers for r in valid.values())
    direct = sum(1 for r in valid.values() if r.is_direct)
    score = score_location(routes)
    print(f"  Total commute time:    {total} min")
    print(f"  Average time:          {avg:.1f} min")
    print(f"  Direct routes:         {direct}/{len(valid)}")
    print(f"  Total transfers:       {transfers}")
    print(f"\n  🏆 SCORE: {score:.1f}/10  -  {score_label(score)}")
    return score


def analyze_address(gmaps, origin: str, target: datetime) -> Dict[str, Optional[Route]]:
    origin = CONFIG.ensure_city(origin)
    sched = CONFIG.schedule

    print("=" * 64)
    print(f" Address: {origin}")
    print(f" Day:     {target.strftime('%A %Y-%m-%d')}")
    if sched.mode == "arrival":
        print(f" Goal:    arrive by {sched.hour:02d}:{sched.minute:02d}")
    else:
        print(f" Depart:  {sched.hour:02d}:{sched.minute:02d}")
    print("=" * 64)

    routes: Dict[str, Optional[Route]] = {}
    for name, dest in CONFIG.destinations.items():
        route = find_route(gmaps, origin, dest, target, sched.mode)
        routes[name] = route
        print_route(name, route)
    return routes


def main():
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not key:
        print("MISSING GOOGLE MAPS API KEY.")
        print("Set the GOOGLE_MAPS_API_KEY environment variable (or put it in .env).")
        print("Get a key at: https://console.cloud.google.com/")
        print("Enable: Directions API + Geocoding API.")
        sys.exit(1)

    if not CONFIG.destinations:
        print("No destinations configured. Edit config.json -> 'destinations'.")
        sys.exit(1)

    gmaps = googlemaps.Client(key=key)
    target = next_weekday_at(CONFIG.schedule.hour, CONFIG.schedule.minute)

    print("\n" + "=" * 64)
    print(" HOME LOCATION ATTRACTIVENESS CALCULATOR")
    print("=" * 64)

    if "--compare" in sys.argv:
        print("\nCompare mode. Enter addresses line by line.")
        print("An empty line ends input.\n")
        addresses = []
        while True:
            a = input(f"Address #{len(addresses)+1}: ").strip()
            if not a:
                break
            addresses.append(a)
        if not addresses:
            print("No addresses given.")
            sys.exit(0)
        results = []
        for addr in addresses:
            routes = analyze_address(gmaps, addr, target)
            score = print_summary(routes)
            results.append((addr, score))
        print("\n" + "█" * 64)
        print(" ADDRESS RANKING")
        print("█" * 64)
        for i, (addr, score) in enumerate(sorted(results, key=lambda x: -x[1]), 1):
            print(f" {i}. {score:.1f}/10  -  {addr}")
        return

    if len(sys.argv) > 1:
        origin = " ".join(a for a in sys.argv[1:] if not a.startswith("--"))
    else:
        origin = input("\nEnter the home address (street, number): ").strip()
    if not origin:
        print("No address given.")
        sys.exit(1)

    routes = analyze_address(gmaps, origin, target)
    print_summary(routes)


if __name__ == "__main__":
    main()
