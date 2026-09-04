"""Decision loop on simulated prints: wait / sit / buy / sell / kill with why."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import LIVE_ORDERS_ALLOWED
from .chart import AD, at_ad, update_met
from .exit import ExitFacts, ExitLiveState, load_exit_facts, live_read_exit, snapshot_sells
from .feeds import Print
from .fills import FillState, remaining_cost_from_state, try_fill_buys, try_fill_sells
from .log import MachineLog
from .path import PathHabit, PathSnapshot, evaluate_path
from .size import BuyLayer, build_buy_layers, gate_buy_layers, load_sell_layers


ROOT = Path(__file__).resolve().parent.parent
PLAYS_DIR = ROOT / "data" / "plays"


@dataclass
class PlanState:
    play: dict[str, Any]
    ad: AD
    habit: PathHabit
    fills: FillState
    met: bool = False
    state: str = "watch"  # watch | met | live | out
    last_decision: str = "wait"
    last_why: str = "waiting for prints"
    killed: bool = False
    current_price: float | None = None
    live_chosen_tf_reds: int | None = None
    live_faster_tf: str | None = None
    live_faster_tf_reds: int | None = None
    live_vol_usd: float | None = None
    play_usd: float = 100.0
    exit_facts: ExitFacts = field(default_factory=ExitFacts)
    exit_live: ExitLiveState = field(default_factory=ExitLiveState)
    watch_only: bool = False
    out_draft_pinged: bool = False

    @property
    def id(self) -> str:
        return str(self.play.get("id") or self.play.get("name"))

    @property
    def name(self) -> str:
        return str(self.play.get("name") or self.id)

    @property
    def tf(self) -> str:
        return str(self.play.get("chosen_tf") or self.play.get("tf") or "?")


@dataclass
class Engine:
    plans: dict[str, PlanState] = field(default_factory=dict)
    log: MachineLog = field(default_factory=MachineLog)
    board_grind: bool = False
    board_panic: bool = False
    book_usd: float = 200.0
    max_live: int = 2
    feed: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    closes: list[dict[str, Any]] = field(default_factory=list)
    needs_you: list[dict[str, Any]] = field(default_factory=list)

    live_orders_allowed: bool = False  # hard-coded false; never place live orders

    def __post_init__(self) -> None:
        self.live_orders_allowed = False
        assert LIVE_ORDERS_ALLOWED is False

    # --- loading ---
    def load_play_file(self, path: str | Path) -> PlanState:
        p = Path(path)
        data = json.loads(p.read_text())
        return self.hang_play(data, play_path=p)

    def load_plays_dir(self, directory: str | Path | None = None) -> list[PlanState]:
        d = Path(directory) if directory else PLAYS_DIR
        out: list[PlanState] = []
        if not d.exists():
            return out
        for f in sorted(d.glob("*.json")):
            out.append(self.load_play_file(f))
        return out

    def hang_play(self, play: dict[str, Any], play_path: Path | None = None) -> PlanState:
        top = float(play["ad_top"])
        bottom = float(play["ad_bottom"])
        play_usd = float(play.get("play_usd") or self.book_usd * 0.5)
        ad = AD(top=top, bottom=bottom)
        habit = PathHabit.from_play(play)
        # Prefer explicit layers if written; else build Size set once
        if play.get("layers"):
            buys: list[BuyLayer] = []
            for row in play["layers"]:
                buys.append(
                    BuyLayer(
                        idx=int(row.get("idx", len(buys) + 1)),
                        price=float(row["price"]),
                        usd=float(row["usd"]),
                        share_pct=float(row.get("share_pct") or 0),
                        role=row.get("role") or "AD",
                        status=row.get("status") or "empty",
                    )
                )
            from .size import _refresh_next

            _refresh_next(buys)
        else:
            buys = build_buy_layers(
                top,
                bottom,
                play_usd,
                high_magnet=bool(play.get("high_magnet", False)),
                copy_count=int(play.get("copy_count") or 0),
            )
        sells = load_sell_layers(play.get("sell_layers"))
        fills = FillState(buy_layers=buys, sell_layers=sells, buy_set_id="1")
        # Reed exit facts (bounce / base / volume). Missing → blank; do not invent.
        facts_src = play.get("exit_facts") or play.get("exit_facts_path")
        exit_facts = load_exit_facts(facts_src, play_path=play_path)
        exit_live = ExitLiveState(original_sells=snapshot_sells(sells) if sells else [])
        plan = PlanState(
            play=play,
            ad=ad,
            habit=habit,
            fills=fills,
            play_usd=play_usd,
            current_price=float(play["current_price"]) if "current_price" in play else None,
            exit_facts=exit_facts,
            exit_live=exit_live,
            watch_only=bool(play.get("watch_only", False)),
        )
        if sells:
            self.log.append(
                "sell-layers",
                "sell layers hung on written plan",
                name=plan.name,
                force=True,
            )
        self.plans[plan.id] = plan
        return plan

    # --- board flags ---
    def set_board_grind(self, on: bool) -> None:
        if on == self.board_grind:
            return
        self.board_grind = on
        self.log.append(
            "board-grind",
            "board-wide grind on" if on else "board-wide grind off",
            force=True,
        )

    def set_board_panic(self, on: bool) -> None:
        if on == self.board_panic:
            return
        self.board_panic = on
        self.log.append(
            "board-panic",
            "board-wide panic on" if on else "board-wide panic off",
            force=True,
        )

    # --- decision loop ---
    def on_print(self, pr: Print) -> dict[str, Any]:
        """Process one simulated print. Never places live orders."""
        assert self.live_orders_allowed is False
        self.feed.append(
            {
                "name": pr.name,
                "price": pr.price,
                "volume_usd": pr.volume_usd,
                "ts": pr.ts.isoformat() if pr.ts else None,
            }
        )
        plan = self._find_plan(pr.name)
        if plan is None:
            return {"action": "wait", "why": f"no hung plan for {pr.name}"}
        if plan.killed or plan.state == "out":
            return {"action": "wait", "why": "plan out or killed"}

        plan.current_price = pr.price
        plan.live_chosen_tf_reds = int(pr.chosen_tf_reds)
        plan.live_vol_usd = float(pr.volume_usd) if pr.volume_usd is not None else None
        if pr.faster_tf_reds:
            # one faster TF id from the print map
            ft = next(iter(pr.faster_tf_reds.items()))
            plan.live_faster_tf = str(ft[0])
            plan.live_faster_tf_reds = int(ft[1])
        else:
            # fall back to play faster_tfs[0] with null count
            fts = plan.play.get("faster_tfs") or []
            plan.live_faster_tf = str(fts[0]) if fts else None
            plan.live_faster_tf_reds = None
        low = pr.low if pr.low is not None else pr.price
        was_met = plan.met
        plan.met = update_met(plan.met, low, plan.ad)
        if plan.met and not was_met:
            plan.state = "met" if plan.state == "watch" else plan.state
            self.log.append(
                "met",
                "low entered AD met band — met stays met",
                name=plan.name,
                price=pr.price,
                force=True,
            )

        price_at = at_ad(pr.price, plan.ad)
        snap = PathSnapshot(
            chosen_tf_reds=pr.chosen_tf_reds,
            faster_tf_reds=dict(pr.faster_tf_reds),
            volume_at_ad_usd=pr.volume_usd,
            at_ad=price_at,
            ad_met=plan.met,
            board_panic=self.board_panic,
        )
        path_dec = evaluate_path(plan.habit, snap)

        # Fail: break of AD = add panic half (not flatten). Path sit must not block under-B panic adds.
        fail_add_panic = False
        if (
            not plan.watch_only
            and plan.met
            and pr.price < plan.ad.bottom
            and path_dec.action in ("sit", "wait")
        ):
            fail_add_panic = True
            path_dec = type(path_dec)(
                action="buy",
                why="Fail — current price broke AD; add panic half",
                habit_match=False,
            )

        result: dict[str, Any] = {
            "name": plan.name,
            "action": path_dec.action,
            "why": path_dec.why,
            "price": pr.price,
            "met": plan.met,
            "fills": [],
            "exit_live": [],
        }

        # Exit live-read while in a position with remaining sell layers.
        # Re-read as price moves — do not freeze sell layers at entry.
        if plan.state == "live" and plan.fills.remaining_sells():
            # weak_bounce Print flag is optional override; live_read scores kind from facts.
            weak = bool(getattr(pr, "weak_bounce", False))
            adapt = live_read_exit(
                plan.fills.sell_layers,
                plan.exit_facts,
                plan.exit_live,
                current_price=pr.price,
                low=low,
                volume_usd=pr.volume_usd,
                ad_bottom=plan.ad.bottom,
                board_panic=self.board_panic,
                weak_bounce=weak,
                at_ad=price_at,
                candles_since_ad_tag=getattr(pr, "candles_since_ad_tag", None),
                ad_band_high=plan.ad.band_high,
                remaining_cost=remaining_cost_from_state(plan.fills),
            )
            result["exit_live"] = list(adapt.reasons)
            result["bounce_kind"] = adapt.bounce_kind
            if adapt.reasons:
                self.log.append(
                    "exit-live",
                    "; ".join(adapt.reasons),
                    name=plan.name,
                    price=pr.price,
                    force=True,
                )
            sell_events = try_fill_sells(plan.fills, pr.price)
            if sell_events:
                self._record_sells(plan, sell_events)
                result["fills"].extend(
                    {
                        "side": "sell",
                        "layer_idx": e.layer_idx,
                        "usd": e.usd,
                        "price": e.price,
                        "why": e.why,
                    }
                    for e in sell_events
                )
                plan.last_decision = "sell"
                plan.last_why = adapt.reasons[0] if adapt.reasons else (
                    sell_events[0].why or "sell layer filled"
                )
                result["action"] = plan.last_decision
                result["why"] = plan.last_why
                if plan.state == "out":
                    return result

        if path_dec.action == "wait":
            # Still surface sell action if exit live-read just filled.
            if result["action"] == "sell":
                return result
            plan.last_decision = "wait"
            plan.last_why = path_dec.why
            # no log spam
            return result

        if path_dec.action == "sit":
            if result["action"] == "sell":
                return result
            plan.last_decision = "sit"
            plan.last_why = path_dec.why
            # Tape sit-out only when at AD (SPEC: off-AD first/second red sit stays off strip)
            if price_at:
                self.log.append(
                    "sit-out",
                    path_dec.why,
                    name=plan.name,
                    price=pr.price,
                )
            return result

        if path_dec.action == "buy":
            # watch_only hung plans must not buy until watch lifts (even board panic)
            if plan.watch_only:
                plan.last_decision = "sit"
                plan.last_why = "watch_only — do not buy until watch lifts"
                result["action"] = "sit"
                result["why"] = plan.last_why
                if price_at:
                    self.log.append(
                        "sit-out",
                        plan.last_why,
                        name=plan.name,
                        price=pr.price,
                    )
                return result

            # Size owns volume at fill: grind-wait / skip no-volume / 0.5× late volume
            path_take_at_ad = bool(path_dec.habit_match) and price_at
            if self.board_panic and price_at:
                path_take_at_ad = True
            gate = gate_buy_layers(
                plan.fills.buy_layers,
                print_price=pr.price,
                volume_usd=float(pr.volume_usd or 0),
                vol_at_bottom_usd=plan.habit.vol_at_bottom_usd,
                at_ad=price_at,
                path_take_at_ad=path_take_at_ad,
                band_high=plan.ad.band_high,
                board_grind=self.board_grind,
            )
            if fail_add_panic and gate.layer_idxs:
                panic_idxs = {
                    ly.idx for ly in plan.fills.buy_layers
                    if ly.role == "panic" and ly.idx in gate.layer_idxs
                }
                gate.layer_idxs = panic_idxs
                if not panic_idxs:
                    from machine.size import SizeGateResult
                    gate = SizeGateResult(action="wait", why="Fail add-panic — no panic layer reached", layer_idxs=set())
            if gate.action != "buy" or not gate.layer_idxs:
                plan.last_decision = "wait"
                plan.last_why = gate.why
                result["action"] = "wait"
                result["why"] = gate.why
                # Sit-out on Size grind wait when at AD; board_grind adds its own note
                if price_at:
                    self.log.append(
                        "sit-out",
                        gate.why,
                        name=plan.name,
                        price=pr.price,
                    )
                if self.board_grind:
                    self.log.append(
                        "sit-out",
                        "board-wide grind — Size wait for volume",
                        name=plan.name,
                        price=pr.price,
                        force=True,
                    )
                return result

            events = try_fill_buys(
                plan.fills,
                pr.price,
                layer_idxs=gate.layer_idxs,
                ad_usd_scale=gate.ad_usd_scale,
            )
            buy_fills = [
                {"layer_idx": e.layer_idx, "usd": e.usd, "price": e.price, "role": e.role}
                for e in events
            ]
            result["fills"] = buy_fills + result["fills"]
            if events:
                plan.state = "live"
                plan.last_decision = "buy"
                plan.last_why = path_dec.why
                # Seed bounce tracking from the fill print.
                if plan.exit_live.bounce_low is None:
                    plan.exit_live.bounce_low = low
                if plan.exit_live.bounce_high is None:
                    plan.exit_live.bounce_high = pr.price
                if plan.exit_live.session_low is None:
                    plan.exit_live.session_low = low
                if not plan.exit_live.original_sells and plan.fills.sell_layers:
                    plan.exit_live.original_sells = snapshot_sells(plan.fills.sell_layers)
                total_usd = sum(e.usd for e in events)
                self.log.append(
                    "paper-buy" if events[0].role == "AD" else "add-panic",
                    path_dec.why,
                    name=plan.name,
                    price=pr.price,
                    size_pct=round(100.0 * total_usd / plan.play_usd, 2) if plan.play_usd else None,
                    force=True,
                )
                for e in events:
                    self.trades.append(
                        {
                            "side": "buy",
                            "name": plan.name,
                            "price": e.price,
                            "usd": e.usd,
                            "role": e.role,
                            "live_order": False,
                        }
                    )
                # Empty OUT: ping Reed/Gauge once — draft sells from bounce/base, no invent.
                if not plan.fills.remaining_sells() and not plan.out_draft_pinged:
                    plan.out_draft_pinged = True
                    msg = (
                        f"{plan.name} {plan.tf} paper-buy filled at {pr.price}; "
                        "OUT empty — Reed bounce/base then Gauge draft sell layers (no invent)"
                    )
                    self.needs_you.append(
                        {
                            "kind": "empty_out_after_buy",
                            "id": plan.id,
                            "name": plan.name,
                            "tf": plan.tf,
                            "price": pr.price,
                            "why": msg,
                        }
                    )
                    self.log.append(
                        "needs-you",
                        msg,
                        name=plan.name,
                        price=pr.price,
                        force=True,
                    )
                # After buys, exit live-read then static fill when hung sells present.
                if plan.fills.remaining_sells():
                    weak = bool(getattr(pr, "weak_bounce", False))
                    adapt = live_read_exit(
                        plan.fills.sell_layers,
                        plan.exit_facts,
                        plan.exit_live,
                        current_price=pr.price,
                        low=low,
                        volume_usd=pr.volume_usd,
                        ad_bottom=plan.ad.bottom,
                        board_panic=self.board_panic,
                        weak_bounce=weak,
                        at_ad=price_at,
                        candles_since_ad_tag=getattr(pr, "candles_since_ad_tag", None),
                        ad_band_high=plan.ad.band_high,
                        remaining_cost=remaining_cost_from_state(plan.fills),
                    )
                    result["bounce_kind"] = adapt.bounce_kind
                    if adapt.reasons:
                        result["exit_live"] = list(adapt.reasons)
                        self.log.append(
                            "exit-live",
                            "; ".join(adapt.reasons),
                            name=plan.name,
                            price=pr.price,
                            force=True,
                        )
                sell_events = try_fill_sells(plan.fills, pr.price)
                if sell_events:
                    self._record_sells(plan, sell_events)
                    result["fills"].extend(
                        {
                            "side": "sell",
                            "layer_idx": e.layer_idx,
                            "usd": e.usd,
                            "price": e.price,
                            "why": e.why,
                        }
                        for e in sell_events
                    )
            else:
                # Path said buy but no layer at/through — Size miss; off the decision tape
                plan.last_decision = "sit-out"
                plan.last_why = "Path buy but no Size layer at or through current price"
            result["action"] = plan.last_decision
            result["why"] = plan.last_why
            return result

        return result

    def _record_sells(self, plan: PlanState, sell_events: list) -> None:
        for e in sell_events:
            self.log.append(
                "paper-sell",
                e.why or "sell layer filled",
                name=plan.name,
                price=e.price,
                size_pct=None,
                force=True,
            )
            self.trades.append(
                {
                    "side": "sell",
                    "name": plan.name,
                    "price": e.price,
                    "usd": e.usd,
                    "why": e.why,
                    "live_order": False,
                }
            )
        if sell_events and not plan.fills.remaining_buys() and not plan.fills.remaining_sells():
            plan.state = "out"
            self.closes.append({"name": plan.name, "reason": "layers flat"})

    def kill(self, plan_id: str, why: str = "kill") -> None:
        plan = self.plans.get(plan_id)
        if not plan:
            return
        plan.killed = True
        plan.state = "out"
        self.log.append("kill", why, name=plan.name, price=plan.current_price, force=True)
        self.closes.append({"name": plan.name, "reason": why})

    def _find_plan(self, name: str) -> PlanState | None:
        for p in self.plans.values():
            if p.name == name or p.id == name:
                return p
        return None

    # --- API serializers ---
    def status(self) -> dict[str, Any]:
        live = [p for p in self.plans.values() if p.state == "live"]
        return {
            "live_orders_allowed": False,
            "book_usd": self.book_usd,
            "live_count": len(live),
            "max_live": self.max_live,
            "board_grind": self.board_grind,
            "board_panic": self.board_panic,
        }

    def plan_row(self, plan: PlanState, *, sheet: bool = True) -> dict[str, Any]:
        """Serialize one plan. Sheet path includes bounce_kind + last_sell_why; ranked omits them."""
        remaining = plan.fills.remaining_buys()
        next_buy = remaining[0] if remaining else None
        sells = plan.fills.remaining_sells()
        if plan.state == "live" and sells:
            next_label = f"{len(sells)} out"
        elif next_buy:
            next_label = f"${next_buy.usd:g}"
        else:
            next_label = "—"
        row: dict[str, Any] = {
            "id": plan.id,
            "name": plan.name,
            "tf": plan.tf,
            "price": plan.current_price,
            "state": plan.state,
            "next": next_label,
            "ad_top": plan.ad.top,
            "ad_bottom": plan.ad.bottom,
            "met": plan.met,
            "why": plan.last_why,
            "habit_ready": plan.habit.habit_ready,
            "watch_only": plan.watch_only,
            "layers": [b.to_dict() for b in plan.fills.buy_layers],
            "sell_layers": [s.to_dict() for s in sells],
        }
        # Reds + $vol on ranked and sheet (Kenneth overview). Bounce why stays sheet-only.
        row["chosen_tf_reds"] = plan.live_chosen_tf_reds
        fts = plan.play.get("faster_tfs") or []
        row["faster_tf"] = plan.live_faster_tf or (str(fts[0]) if fts else None)
        row["faster_tf_reds"] = plan.live_faster_tf_reds
        row["vol_usd"] = plan.live_vol_usd
        if sheet:
            # Exit-why thin paint fields — sheet only. Do not invent sell why.
            row["bounce_kind"] = plan.exit_live.last_bounce_kind
            row["last_sell_why"] = self.log.last_sell_why(plan.name)
        return row

    def ranked(self) -> list[dict[str, Any]]:
        # Ranked includes reds+$vol; bounce_kind / last_sell_why stay off this list.
        rows = [self.plan_row(p, sheet=False) for p in self.plans.values()]
        order = {"live": 0, "met": 1, "watch": 2, "out": 3}
        rows.sort(key=lambda r: (order.get(r["state"], 9), r["name"]))
        for i, r in enumerate(rows, start=1):
            r["rank"] = i
        return rows
