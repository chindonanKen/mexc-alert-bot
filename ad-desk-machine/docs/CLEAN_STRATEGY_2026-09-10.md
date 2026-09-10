# Machine process — clean preferred three (Kenneth 2026-09-10)

Standing process on this Machine. `live_orders_allowed` stays false.

## Charts

ETHUSDT 1h · XPINUSDT 4h · SYNUSDT 1h only.

## Size (buys)

Five equal-spaced prices across the AD met-band (last 5% of dump L above B). Equal 20% of play each.

Not standing: Size dump-depth, panic under B, grind-wait as entry gate.

## Exit (sells)

Five prices at `B + q×L` where `q` is the 20 / 35 / 50 / 65 / 80th percentile of `bounce_frac_of_L` on paid mets (tape). Equal 20% each.

Not standing: Exit bounce-floor, fat 3rd/4th Exit sizes.

## Paid filters (tape)

- ETHUSDT 1h / XPINUSDT 4h: `reds_into_met>=1`
- SYNUSDT 1h: `dump_speed_vs_mid>=1.0`

## Hang

Load play-file tape prices. Do not call dump-depth or panic generators for these plays.

Old dump-depth hangs live under `data/plays/archive/`. Not auto-loaded.

This PR does not touch droplet Telegram / AD Desk databases.
