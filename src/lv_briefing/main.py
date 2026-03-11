"""Read starter_sources.csv and print a summary grouped by section."""

from lv_briefing.config import SECTION_ORDER
from lv_briefing.load_sources import load_sources


def main() -> None:
    sources = load_sources()

    grouped: dict[str, list[dict]] = {}
    for src in sources:
        section = src.get("section", "unknown")
        grouped.setdefault(section, []).append(src)

    print("Lehigh Valley Morning Briefing — Source Summary")
    print("=" * 50)

    for section in SECTION_ORDER:
        items = grouped.get(section, [])
        if not items:
            continue
        print(f"\n[{section}]")
        for item in sorted(items, key=lambda x: int(x.get("priority", 9))):
            prio = item.get("priority", "?")
            src_type = item.get("type", "?")
            print(f"  P{prio} ({src_type:>10}) {item['name']}")
            print(f"       {item['url']}")

    print(f"\nTotal sources: {len(sources)}")


if __name__ == "__main__":
    main()
