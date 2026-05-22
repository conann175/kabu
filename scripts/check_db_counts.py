import argparse
import os
import sys

from supabase import create_client


def count_query(query):
    result = query.execute()
    return result.count or 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Supabase row counts for a race date.")
    parser.add_argument("race_date", help="Race date in YYYY-MM-DD format.")
    args = parser.parse_args()

    supabase_url = os.environ["SUPABASE_URL"]
    supabase_key = os.environ["SUPABASE_KEY"]
    supabase = create_client(supabase_url, supabase_key)

    races = (
        supabase.table("races")
        .select("race_id", count="exact")
        .eq("race_date", args.race_date)
        .execute()
    )
    race_ids = [row["race_id"] for row in (races.data or [])]

    entry_count = 0
    payout_count = 0
    if race_ids:
        entry_count = count_query(
            supabase.table("race_entries")
            .select("entry_id", count="exact")
            .in_("race_id", race_ids)
        )
        payout_count = count_query(
            supabase.table("payouts")
            .select("payout_id", count="exact")
            .in_("race_id", race_ids)
        )

    print(f"race_date={args.race_date}")
    print(f"races={races.count or 0}")
    print(f"race_entries={entry_count}")
    print(f"payouts={payout_count}")
    print("sample_race_ids=" + ",".join(race_ids[:5]))

    if not race_ids:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
