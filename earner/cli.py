"""Command line. You talk to the CEO; the CEO runs the company."""

from __future__ import annotations

import argparse
import time
import json
import sys
from pathlib import Path

from . import config, goals
from .platform import Platform


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def cmd_status(platform: Platform, args) -> int:
    print("Readiness")
    for name, ok, why in platform.readiness():
        print(f"  [{'x' if ok else ' '}] {name:<16} {why}")

    print("\nBooks (settled money only)")
    summary = platform.ledger.summary(list(platform.staff))
    print(f"  {'agent':<14} {'revenue':>11} {'spend':>10} {'net':>11} {'unpaid':>11} {'jobs':>5}")
    for name, row in summary.items():
        print(
            f"  {name:<14} {_money(row['revenue_cents']):>11} {_money(row['cost_cents']):>10} "
            f"{_money(row['net_cents']):>11} {_money(row['outstanding_cents']):>11} {row['jobs']:>5}"
        )

    status = platform.target_status()
    if status:
        print(f"\nTarget\n  {status.brief()}")
    return 0


def cmd_target(platform: Platform, args) -> int:
    if args.amount is None:
        status = platform.target_status()
        print(status.brief() if status else "No target set. Try: earner target 5000 --hours 50")
        return 0
    target = platform.set_target(int(round(args.amount * 100)), args.hours)
    print(f"Target set: {_money(target.amount_cents)} in {target.hours:g}h")
    status = platform.target_status()
    plan = goals.plan_for(status, platform.ceo._average_deal_cents())
    print(
        f"  That is {plan['deals_needed']} deal(s) at ~{_money(plan['average_deal_cents'])} each,\n"
        f"  or about {plan['leads_needed_at_30pct_close']} real leads at a 30% close rate."
    )
    return 0


def cmd_run(platform: Platform, args) -> int:
    def on_report(reports):
        print(platform.ceo.debrief(reports))
        print("-" * 60)

    reports = platform.run(loops=args.loops, interval=args.interval, on_report=on_report)
    if args.brief and platform.cfg.anthropic_api_key:
        print("\nCEO:\n" + platform.ceo.report())
    return 0 if not any(r.errors for r in reports) else 1


def cmd_lead(platform: Platform, args) -> int:
    """Put a real prospect into the pipeline."""
    from .leads import InvalidLead, Lead, from_posting, import_file, load_all, save

    if args.action == "list":
        leads = load_all(platform.cfg.inbox)
        if not leads:
            print("No leads. Add one: earner lead add --company X --email y@z.com --notes '...'")
            return 0
        for lead in leads:
            worked = platform.ledger.conn.execute(
                "SELECT status FROM opportunities WHERE external_ref=?", (lead.ref,)
            ).fetchone()
            print(
                f"{lead.ref}  {lead.company[:28]:<28} {lead.email[:30]:<30} "
                f"{(worked['status'] if worked else 'unworked')}"
            )
        return 0

    if args.action == "import":
        added, skipped = import_file(args.file, platform.cfg.inbox)
        for lead in added:
            print(f"  + {lead.company[:60]}")
        for reason in skipped:
            print(f"  - skipped {reason}")
        print(f"\nImported {len(added)} lead(s), skipped {len(skipped)}.")
        return 0

    try:
        if args.action == "from-url":
            lead = from_posting(args.url, platform.ceo.llm, platform.cfg.inbox)
        else:
            lead = Lead(
                company=args.company or "",
                email=args.email or "",
                contact_name=args.contact or "",
                website=args.website or "",
                notes=args.notes or "",
                evidence=args.evidence or "",
                service=args.service or platform.cfg.offer,
            )
            save(lead, platform.cfg.inbox)
    except InvalidLead as exc:
        print(f"Rejected: {exc}")
        return 1

    print(f"Added {lead.ref}: {lead.company}" + (f" <{lead.email}>" if lead.email else ""))
    print("Acquisition will research and draft a pitch on the next run:")
    print("  earner run --approve cli")
    return 0


def cmd_trade(platform: Platform, args) -> int:
    """Run the trading engine."""
    from .trading.live import preflight, run_live
    from .trading.session import load_session
    from .trading.simulate import run as run_sim

    cfg = platform.cfg
    session = load_session()
    print(session.banner)

    if args.mode == "simulate":
        run_sim(capital=args.capital, aggressive=args.aggressive,
                workdir=str(cfg.workdir), preset=args.preset or "")
        return 0

    universe = [s.strip().upper() for s in (args.symbols or cfg.trading_universe).split(",")
                if s.strip()]

    if args.mode == "check":
        problems = preflight(cfg, universe)
        if problems:
            print("\nNot ready for live:")
            for problem in problems:
                print(f"  ✗ {problem}")
            return 1
        print(f"\n  ✓ ready — gate open, credentials present, {len(universe)} symbols")
        print("  Run `earner trade --mode live` to start. You will be asked for a")
        print("  TOTP and MPIN; neither is stored.")
        return 0

    try:
        run_live(cfg, universe=universe, capital=args.capital,
                 aggressive=args.aggressive, workdir=str(cfg.workdir),
                 max_minutes=args.max_minutes)
    except RuntimeError as exc:
        print(f"\n{exc}")
        return 1
    return 0


def cmd_backtest(platform: Platform, args) -> int:
    """Replay real NSE days through the same engine live trading uses."""
    from .trading.backtest import run as run_backtest

    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    result = run_backtest(symbols, capital=args.capital, preset=args.preset,
                          interval=args.interval, days=args.days,
                          workdir=str(platform.cfg.workdir))
    print("\n" + result.report())
    if result.gross < 0:
        print("\n  The gross line is negative: these strategies lose on price movement")
        print("  alone, before a rupee of brokerage. No cost saving fixes that.")
    return 0


def cmd_research(platform: Platform, args) -> int:
    """Search the hypothesis space against real history, and reject nearly all of it."""
    from .trading.research import search

    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    verdict = search(symbols, capital=args.capital, interval=args.interval,
                     days=args.days, workdir=str(platform.cfg.workdir))
    print("\n" + verdict.report())
    return 0 if verdict.survived else 1



FREIGHT_CAMPAIGN = dict(
    name="freight-recon",
    service="a fixed-price audit of your last 200 loads, matching every rate "
            "confirmation against its carrier invoice",
    price_low=1500, price_high=2500,
    hook="rate con vs carrier invoice",
)


def _pipeline_parts(platform: Platform, args):
    """Pipeline, campaign and credentials, assembled once for every subcommand."""
    from .outreach import Campaign, Pipeline

    cfg = platform.cfg
    pipeline = Pipeline(Path(cfg.workdir) / "outreach.db")
    campaign = Campaign(
        from_name=getattr(args, "from_name", "") or cfg.gmail_user or "",
        signature=getattr(args, "signature", "") or "",
        **FREIGHT_CAMPAIGN)
    return pipeline, campaign, cfg


def cmd_pipeline(platform: Platform, args) -> int:
    """Find leads, mail them, track replies, invoice, collect. One loop.

    Default is a dry run: it builds every message and opens no socket. Real
    sending needs --live AND the environment confirmation, because mail to a
    stranger from your own address cannot be recalled.
    """
    import csv as csvlib

    from .outreach import (DRAFTED, OutreachError, Prospect, check_replies,
                           collect, quote, run_sends, sending_enabled)

    pipeline, campaign, cfg = _pipeline_parts(platform, args)
    step = args.step

    try:
        if step == "import":
            path = Path(args.file)
            if not path.exists():
                print(f"no such file: {path}")
                return 1
            added = skipped = 0
            with path.open(newline="", encoding="utf-8") as handle:
                for row in csvlib.DictReader(handle):
                    try:
                        prospect = Prospect(
                            company=row.get("company", ""), email=row.get("email", ""),
                            evidence=row.get("evidence", ""),
                            contact_name=row.get("contact_name", ""),
                            website=row.get("website", ""), role=row.get("role", ""),
                            notes=row.get("notes", ""))
                        added += 1 if pipeline.add(prospect, campaign.name) else 0
                    except OutreachError as exc:
                        print(f"  ! skipped: {exc}")
                        skipped += 1
            print(f"imported {added}, skipped {skipped}")

        elif step == "draft":
            print(f"drafted {pipeline.draft_all(campaign)} messages")

        elif step == "preview":
            rows = pipeline.at_stage(DRAFTED, campaign.name)
            if not rows:
                print("nothing drafted — run: earner pipeline draft")
            for row in rows[: args.limit]:
                print(f"\n{'=' * 72}\nTo: {row['email']}  ({row['company']})")
                print(f"Subject: {row['subject']}\n{'-' * 72}\n{row['body']}")

        elif step == "send":
            if args.live and not sending_enabled():
                print("Refusing to send. This puts real mail in strangers' inboxes "
                      "from your own address and cannot be undone.\n"
                      "  export OUTREACH_SEND_CONFIRMATION=I_UNDERSTAND_THIS_EMAILS_REAL_PEOPLE")
                return 1
            if args.live and not (cfg.gmail_user and cfg.gmail_app_password):
                print("GMAIL_USER and GMAIL_APP_PASSWORD are not set — run: earner connect")
                return 1
            mode = "LIVE" if args.live else "dry run (no socket opened)"
            print(f"Sending — {mode}. Cap {args.cap}/day, "
                  f"{pipeline.remaining_today(args.cap)} left today.")
            count = run_sends(pipeline, campaign, user=cfg.gmail_user or "you@example.com",
                              app_password=cfg.gmail_app_password or "",
                              cap=args.cap, live=args.live,
                              sleeper=(lambda _: None) if not args.live else time.sleep)
            print(f"{count} sent")

        elif step == "replies":
            if not args.live:
                print("dry run — pass --live to read the inbox")
                return 0
            found = check_replies(pipeline, user=cfg.gmail_user or "",
                                  app_password=cfg.gmail_app_password or "", live=True)
            print(f"{found} replies")

        elif step == "quote":
            from .ledger import Ledger
            from .payments import build_provider

            ledger = Ledger(Path(cfg.workdir) / "ledger.db")
            out = quote(pipeline, args.ref, ledger=ledger,
                        provider=build_provider(cfg), campaign=campaign,
                        amount_cents=int(args.amount * 100), currency=cfg.currency)
            print(f"job {out['job_id']}  invoice {out['provider_ref']}\n  {out['url']}")
            ledger.close()

        elif step == "collect":
            from .ledger import Ledger
            from .payments import build_provider

            ledger = Ledger(Path(cfg.workdir) / "ledger.db")
            print(f"{collect(pipeline, ledger=ledger, provider=build_provider(cfg))} settled")
            print(f"revenue so far: {ledger.revenue_cents() / 100:,.2f} {cfg.currency.upper()}")
            ledger.close()

        elif step == "run":
            # The whole loop. Safe to run on a schedule: every step is
            # idempotent and the database is the memory.
            print("1. drafting");   print(f"   {pipeline.draft_all(campaign)} new")
            print("2. sending")
            run_sends(pipeline, campaign, user=cfg.gmail_user or "you@example.com",
                      app_password=cfg.gmail_app_password or "", cap=args.cap,
                      live=args.live, sleeper=(lambda _: None) if not args.live else time.sleep,
                      log=lambda m: print(f"  {m}"))
            print("3. closing exhausted"); print(f"   {pipeline.close_exhausted(campaign.name)} closed")
            if args.live:
                print("4. replies")
                check_replies(pipeline, user=cfg.gmail_user or "",
                              app_password=cfg.gmail_app_password or "", live=True)
            print("\n" + _pipeline_status(pipeline, campaign))

        else:
            print(_pipeline_status(pipeline, campaign))

    except OutreachError as exc:
        print(f"refused: {exc}")
        return 1
    finally:
        pipeline.close()
    return 0


def _pipeline_status(pipeline, campaign) -> str:
    from .outreach import (BOUNCED, CLOSED, DRAFTED, OPTED_OUT, QUOTED,
                           REPLIED, RESEARCHED, SENT, WON)

    counts = pipeline.summary(campaign.name)
    order = (RESEARCHED, DRAFTED, SENT, REPLIED, QUOTED, WON, CLOSED, OPTED_OUT, BOUNCED)
    lines = [f"Pipeline — {campaign.name}", ""]
    for stage in order:
        lines.append(f"  {stage:<12}{counts.get(stage, 0):>5}")
    lines.append("")
    lines.append(f"  sent in last 24h: {pipeline.sent_today()}")
    replied = counts.get(REPLIED, 0) + counts.get(QUOTED, 0) + counts.get(WON, 0)
    sent = sum(counts.get(s, 0) for s in (SENT, REPLIED, QUOTED, WON, CLOSED))
    if sent:
        lines.append(f"  reply rate:       {replied / sent:.1%}  ({replied}/{sent})")
    return "\n".join(lines)


def cmd_rapid(platform: Platform, args) -> int:
    """Run the rapid-engine study end to end and print every measured number.

    Reads market data and writes nothing but its report. There is no path from
    this command to an order.
    """
    from quant_os.rapid.study import main

    return main(interval=args.interval, days=args.days, capital=args.capital)


def cmd_depth(platform: Platform, args) -> int:
    """Record Level-2 depth. Subscribes and records; places no orders."""
    from getpass import getpass

    from .trading.broker import KotakBroker
    from .trading.session import load_session
    from quant_os.data.depth_feed import DepthFeed

    cfg = platform.cfg
    symbols = [s.strip().upper() for s in (args.symbols or cfg.trading_universe).split(",")
               if s.strip()]
    missing = [n for n, v in (("KOTAK_CONSUMER_KEY", cfg.kotak_consumer_key),
                              ("KOTAK_MOBILE", cfg.kotak_mobile),
                              ("KOTAK_UCC", cfg.kotak_ucc)) if not v]
    if missing:
        print("Cannot subscribe — not set: " + ", ".join(missing))
        return 1

    print("Depth recorder. Read-only: it subscribes and writes, and has no")
    print("reference to an order path.\n")
    broker = KotakBroker(cfg, session=load_session())
    broker.connect(totp=getpass("TOTP: ").strip(), mpin=getpass("MPIN: ").strip())
    print(f"Instrument master: {broker.load_instruments():,} symbols")

    feed = DepthFeed(broker, workdir=str(cfg.workdir))
    feed.subscribe(symbols)
    print(f"Subscribed to depth for {len(symbols)} symbols. Ctrl-C to stop.\n")
    try:
        while True:
            time.sleep(args.every)
            print(f"  {feed.health.line()}")
            for symbol in symbols:
                book = feed.book(symbol)
                if book:
                    print(f"    {symbol:<12} spread {book.relative_spread*10000:>5.1f}bp  "
                          f"imb {book.imbalance:>+6.2f}  depth-imb {book.depth_imbalance:>+6.2f}")
    except KeyboardInterrupt:
        feed.unsubscribe()
        print(f"\nStopped. {feed.health.books:,} book snapshots recorded to "
              f"{cfg.workdir}/depth.db")
    return 0


def cmd_dashboard(platform: Platform, args) -> int:
    """Write the dashboard from the engine's own records."""
    from .trading.dashboard import write

    while True:
        out = write(platform.cfg.workdir, capital=args.capital)
        print(f"  {out}  ({time.strftime('%H:%M:%S')})")
        if not args.watch:
            return 0
        time.sleep(args.every)


def cmd_connect(platform: Platform, args) -> int:
    """Wire up Gmail and Instagram, proving each credential works."""
    from pathlib import Path

    from .connect import run_wizard

    env_path = Path(args.env or ".env")
    connected = run_wizard(env_path, only=args.channel, cfg=platform.cfg)
    if connected:
        print(f"Connected {connected} channel(s). Load them and check:")
        print("  set -a && source .env && set +a")
        print("  earner status")
    else:
        print("Nothing connected. Re-run `earner connect` when you have the credentials.")
    return 0


def cmd_autopilot(platform: Platform, args) -> int:
    """Hands-off: run the firm until the target is met or the deadline passes."""
    blockers = [name for name, ok, _ in platform.readiness() if not ok]
    print("Autopilot starting.")
    if blockers:
        print(f"  Not connected: {', '.join(blockers)}")
        print("  It will run anyway, but anything unconnected earns nothing.\n")

    def on_cycle(n, reports, status):
        print(f"\n── cycle {n} " + "─" * 46)
        print(platform.ceo.debrief(reports))
        if status:
            print(f"\n{status.brief()}")

    outcome = platform.autopilot(
        interval=args.interval, max_cycles=args.max_cycles, on_cycle=on_cycle
    )
    print(f"\nStopped: {outcome['stopped']} after {outcome['cycles']} cycle(s).")
    print(f"Settled: {_money(outcome.get('settled_cents', 0))}")
    if outcome["stopped"] == "spend_guard":
        print(
            f"Spend {_money(outcome['spend_cents'])} outran revenue by more than 3x — "
            "the pricing or the demand is wrong, not the loop."
        )
    if platform.cfg.anthropic_api_key:
        print("\nCEO:\n" + platform.ceo.report())
    return 0


def cmd_ceo(platform: Platform, args) -> int:
    if args.message:
        _ceo_turn(platform, " ".join(args.message))
        return 0

    print("Talking to the CEO. Ctrl-C to leave.\n")
    print(platform.ceo.report() + "\n")
    while True:
        try:
            question = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not question:
            continue
        if question.lower() in ("exit", "quit"):
            return 0
        _ceo_turn(platform, question)


def _ceo_turn(platform: Platform, question: str) -> None:
    decision = platform.ceo.ask(question)
    print(f"\nceo> {decision['reply']}\n")
    if decision["directives"]:
        print("  directives issued:")
        for d in decision["directives"]:
            print(f"    → {d['agent']}: {d['instruction']}")
    if decision["needs_from_owner"]:
        print("  needs you:")
        for need in decision["needs_from_owner"]:
            print(f"    ! {need}")
    print()


def cmd_ledger(platform: Platform, args) -> int:
    for job in platform.ledger.jobs(agent=args.agent):
        paid = "PAID" if platform.ledger.job_is_paid(job.id) else "unpaid"
        print(
            f"#{job.id:<4} {job.agent:<12} {job.status:<18} {_money(job.quote_cents):>10} "
            f"cost {_money(job.cost_cents):>8} {paid:>6}  {job.title[:44]}"
        )
    print(f"\nSettled revenue: {_money(platform.ledger.revenue_cents())}")
    print(f"Outstanding:     {_money(platform.ledger.outstanding_cents())}")
    print(f"Spend:           {_money(platform.ledger.cost_cents())}")
    return 0


def cmd_publish(platform: Platform, args) -> int:
    """Publish rendered content once it has a public URL."""
    if not platform.instagram.enabled:
        print("Instagram is not configured (INSTAGRAM_USER_ID / INSTAGRAM_ACCESS_TOKEN)")
        return 1
    caption_path = platform.cfg.workdir / "content" / args.ref / "caption.txt"
    if not caption_path.exists():
        print(f"No rendered content for ref {args.ref}")
        return 1
    caption = caption_path.read_text()
    if args.video_url:
        media_id = platform.instagram.publish_reel(args.video_url, caption, cover_url=args.cover_url)
    elif args.image_url:
        media_id = platform.instagram.publish_image(args.image_url, caption)
    else:
        print("Give --video-url or --image-url (Instagram fetches media from a public URL)")
        return 1
    platform.ledger.log("agent", None, "published", ref=args.ref, media_id=media_id)
    print(f"Published: {media_id}")
    return 0


def cmd_inbox(platform: Platform, args) -> int:
    """Drop a real request or lead into the queue by hand."""
    folder = platform.cfg.inbox / args.kind
    folder.mkdir(parents=True, exist_ok=True)
    payload = json.loads(sys.stdin.read()) if args.stdin else {
        "email": args.email,
        "title": args.title,
        "brief": args.brief,
    }
    path = folder / f"{args.ref}.json"
    path.write_text(json.dumps(payload, indent=2))
    print(f"Queued {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="earner", description="An AI firm that bills real customers.")
    p.add_argument("--live", action="store_true", help="use real payment rails (needs STRIPE_API_KEY)")
    p.add_argument(
        "--approve",
        choices=["hold", "cli", "auto"],
        default="hold",
        help="approval gate: hold (park for review), cli (prompt), auto (unattended)",
    )
    p.add_argument("--autonomous", action="store_true", help="let agents source their own work")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("status", help="readiness and the books")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("target", help="set or check the revenue target")
    s.add_argument("amount", type=float, nargs="?", help="target in dollars, e.g. 5000")
    s.add_argument("--hours", type=float, default=50.0)
    s.set_defaults(func=cmd_target)

    s = sub.add_parser("run", help="run the company")
    s.add_argument("--loops", type=int, default=1, help="0 = run until the target expires")
    s.add_argument("--interval", type=float, default=900.0, help="seconds between loops")
    s.add_argument("--brief", action="store_true", help="CEO summary at the end")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("lead", help="add or list real prospects")
    s.add_argument("action", choices=["add", "list", "import", "from-url"])
    s.add_argument("--file", help="captured lead batch, for `import`")
    s.add_argument("--company")
    s.add_argument("--email")
    s.add_argument("--contact")
    s.add_argument("--website")
    s.add_argument("--notes", help="why they might buy")
    s.add_argument("--evidence", help="what you observed that says they need this")
    s.add_argument("--service", help="what to sell them (defaults to EARNER_OFFER)")
    s.add_argument("--url", help="a real public job posting, for `from-url`")
    s.set_defaults(func=cmd_lead)

    s = sub.add_parser("trade", help="run the trading engine")
    s.add_argument("--mode", choices=["simulate", "check", "live"], default="simulate",
                   help="simulate: synthetic day; check: live preflight; live: real orders")
    s.add_argument("--capital", type=float, default=200_000.0)
    s.add_argument("--aggressive", action="store_true", help="3%/trade, 30% portfolio ceiling")
    s.add_argument("--preset", choices=["standard", "aggressive", "diversified"],
                   help="diversified: 8 simultaneous positions, tight sector caps")
    s.add_argument("--symbols", help="comma-separated, defaults to TRADING_UNIVERSE")
    s.add_argument("--max-minutes", type=float, default=None,
                   help="flatten and stop after this long")
    s.set_defaults(func=cmd_trade)

    s = sub.add_parser("backtest", help="test the strategies on real NSE history")
    s.add_argument("--capital", type=float, default=100_000.0)
    s.add_argument("--preset", choices=["standard", "aggressive", "diversified"],
                   default="diversified")
    s.add_argument("--interval", default="5m", help="1m (last ~7d) or 5m (last ~60d)")
    s.add_argument("--days", type=int, default=60)
    s.add_argument("--symbols", help="comma-separated NSE symbols")
    s.set_defaults(func=cmd_backtest)

    s = sub.add_parser("research", help="generate strategy hypotheses and reject them honestly")
    s.add_argument("--capital", type=float, default=100_000.0)
    s.add_argument("--interval", default="5m")
    s.add_argument("--days", type=int, default=60)
    s.add_argument("--symbols", help="comma-separated NSE symbols")
    s.set_defaults(func=cmd_research)

    s = sub.add_parser("pipeline", help="find leads, mail them, invoice, collect")
    s.add_argument("step", nargs="?", default="status",
                   choices=["import", "draft", "preview", "send", "replies",
                            "quote", "collect", "run", "status"])
    s.add_argument("--file", help="CSV for import: company,email,evidence,contact_name,website")
    s.add_argument("--live", action="store_true", help="actually send (needs the env confirmation)")
    s.add_argument("--cap", type=int, default=20, help="max emails per 24h")
    s.add_argument("--limit", type=int, default=3, help="how many drafts to preview")
    s.add_argument("--ref", help="prospect ref, for quote")
    s.add_argument("--amount", type=float, default=1500.0, help="quote amount")
    s.add_argument("--from-name", dest="from_name", default="")
    s.add_argument("--signature", default="")
    s.set_defaults(func=cmd_pipeline)

    s = sub.add_parser("rapid", help="microstructure/ML rapid-trading study (read-only)")
    s.add_argument("--capital", type=float, default=100_000.0)
    s.add_argument("--interval", default="5m", help="1m (last ~7d) or 5m (last ~60d)")
    s.add_argument("--days", type=int, default=60)
    s.set_defaults(func=cmd_rapid)

    s = sub.add_parser("depth", help="record Level-2 depth (read-only, no orders)")
    s.add_argument("--symbols", help="comma-separated, defaults to TRADING_UNIVERSE")
    s.add_argument("--every", type=float, default=10.0, help="seconds between status lines")
    s.set_defaults(func=cmd_depth)

    s = sub.add_parser("dashboard", help="render the live dashboard as HTML")
    s.add_argument("--capital", type=float, default=100_000.0)
    s.add_argument("--watch", action="store_true", help="keep rewriting it")
    s.add_argument("--every", type=float, default=5.0, help="seconds between rewrites")
    s.set_defaults(func=cmd_dashboard)

    s = sub.add_parser("connect", help="connect Gmail and Instagram (validates credentials)")
    s.add_argument("--channel", choices=["gmail", "instagram", "upwork"],
                   help="connect just one")
    s.add_argument("--env", help="path to write (default: .env)")
    s.set_defaults(func=cmd_connect)

    s = sub.add_parser("autopilot", help="run unattended until the target is hit")
    s.add_argument("--interval", type=float, default=900.0, help="seconds between cycles")
    s.add_argument("--max-cycles", type=int, default=0, help="0 = until target or deadline")
    s.set_defaults(func=cmd_autopilot)

    s = sub.add_parser("ceo", help="talk to the CEO")
    s.add_argument("message", nargs="*")
    s.set_defaults(func=cmd_ceo)

    s = sub.add_parser("ledger", help="every job and what it earned")
    s.add_argument("--agent")
    s.set_defaults(func=cmd_ledger)

    s = sub.add_parser("publish", help="publish rendered content to Instagram")
    s.add_argument("--ref", required=True)
    s.add_argument("--video-url")
    s.add_argument("--image-url")
    s.add_argument("--cover-url")
    s.set_defaults(func=cmd_publish)

    s = sub.add_parser("inbox", help="queue a request or lead")
    s.add_argument("kind", choices=["requests", "leads", "products"])
    s.add_argument("ref")
    s.add_argument("--email", default="")
    s.add_argument("--title", default="")
    s.add_argument("--brief", default="")
    s.add_argument("--stdin", action="store_true", help="read the JSON payload from stdin")
    s.set_defaults(func=cmd_inbox)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    overrides = {}
    if args.live:
        overrides["mode"] = config.LIVE
    if args.autonomous:
        overrides["autonomous"] = True
    try:
        cfg = config.load(**overrides)
    except ValueError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    platform = Platform(cfg, gate_name=args.approve)
    try:
        return args.func(platform, args)
    finally:
        platform.close()


if __name__ == "__main__":
    raise SystemExit(main())
