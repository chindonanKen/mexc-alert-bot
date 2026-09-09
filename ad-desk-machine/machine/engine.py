"""Decision loop on simulated prints: wait / sit / buy / sell / kill with why."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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
    live_reds_5m: int | None = None
    live_vol_usd_5m: float | None = None
    play_usd: float = 100.0
    exit_facts: ExitFacts = field(default_factory=ExitFacts)
    exit_live: ExitLiveState = field(default_factory=ExitLiveState)
    watch_only: bool = False
    out_draft_pinged: bool = False
    play_path: Path | None = None

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
        # Kenneth 2026-09-07 Path RECUT: habit_ready / red fields are not hang Lock gates.
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
            play_path=play_path,
        )
        # Persist-kill / outcome writeback: load closed or killed as non-reacting.
        outcome = play.get("outcome") if isinstance(play.get("outcome"), dict) else {}
        killed_flag = bool(play.get("killed")) or str(play.get("status") or "") == "killed_out"
        closed_flag = bool(outcome.get("closed")) or str(play.get("state") or "") == "out"
        if killed_flag or closed_flag:
            plan.state = "out"
            if killed_flag:
                plan.killed = True
            plan.last_decision = str(
                outcome.get("last_decision")
                or ("kill" if killed_flag else play.get("last_decision") or "out")
            )
            plan.last_why = str(
                outcome.get("last_why")
                or play.get("killed_why")
                or play.get("kill_note")
                or outcome.get("reason")
                or ("killed_out" if killed_flag else "closed")
            )
        if sells and not plan.killed:
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
                "volume_usd_5m": getattr(pr, "volume_usd_5m", 0.0),
                "reds_5m": getattr(pr, "reds_5m", 0),
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
        plan.live_reds_5m = int(getattr(pr, "reds_5m", 0) or 0)
        plan.live_vol_usd_5m = (
            float(pr.volume_usd_5m)
            if getattr(pr, "volume_usd_5m", None) is not None
            else None
        )
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
        tagged_hung_ad = False
        for ly in plan.fills.buy_layers:
            if ly.role != "AD" or ly.status not in ("empty", "next"):
                continue
            if pr.price <= ly.price:
                tagged_hung_ad = True
                break
        snap = PathSnapshot(
            chosen_tf_reds=pr.chosen_tf_reds,
            faster_tf_reds=dict(pr.faster_tf_reds),
            volume_at_ad_usd=pr.volume_usd,
            volume_usd_5m=float(getattr(pr, "volume_usd_5m", 0.0) or 0.0),
            reds_5m=int(getattr(pr, "reds_5m", 0) or 0),
            at_ad=price_at,
            ad_met=plan.met,
            board_panic=self.board_panic,
            tagged_hung_ad_buy=tagged_hung_ad,
        )
        path_dec = evaluate_path(plan.habit, snap)

        # Fail: break of AD = add panic half (not flatten). Owns under-B after already-met
        # even when Path would also buy on tagged AD layers. Require was_met: first touch
        # under B that first-enters the met band is Chart met, not Fail-add.
        fail_add_panic = False
        if (
            not plan.watch_only
            and was_met
            and pr.price < plan.ad.bottom
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
            sell_price = pr.high if pr.high is not None else pr.price
            sell_events = try_fill_sells(plan.fills, sell_price)
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
                plan.last_decision = "paper-sell"
                plan.last_why = adapt.reasons[0] if adapt.reasons else (
                    sell_events[0].why or "sell layer filled"
                )
                result["action"] = "sell"
                result["why"] = plan.last_why
                if plan.state == "out":
                    return result

        if path_dec.action == "wait":
            # Still surface sell action if exit live-read just filled.
            if result["action"] == "sell":
                return result
            # Path RECUT: tagged hung AD buy → Path buys (above). Wait why stays clear.
            plan.last_decision = "wait"
            plan.last_why = path_dec.why
            result["why"] = path_dec.why
            return result

        if path_dec.action == "sit":
            if result["action"] == "sell":
                return result
            plan.last_decision = "sit-out"
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
                plan.last_decision = "sit-out"
                plan.last_why = "watch_only — do not buy until watch lifts"
                result["action"] = "sit"
                result["why"] = plan.last_why
                # G6: sit-with-why on tape (decision print sit-out)
                self.log.append(
                    "sit-out",
                    plan.last_why,
                    name=plan.name,
                    price=pr.price,
                )
                return result

            # Size owns volume at fill: grind-wait / skip no-volume / 0.5× late volume.
            # Optional require_5m_volume_spike: Size weighs 5m dollar volume (not Path sit).
            path_take_at_ad = bool(path_dec.habit_match) and price_at
            if self.board_panic and price_at:
                path_take_at_ad = True
            size_vol = float(pr.volume_usd or 0)
            if plan.habit.require_5m_volume_spike:
                size_vol = float(getattr(pr, "volume_usd_5m", 0.0) or 0.0)
            gate = gate_buy_layers(
                plan.fills.buy_layers,
                print_price=pr.price,
                volume_usd=size_vol,
                vol_at_bottom_usd=plan.habit.vol_at_bottom_usd,
                at_ad=price_at,
                path_take_at_ad=path_take_at_ad,
                band_high=plan.ad.band_high,
                board_grind=self.board_grind,
            )
            # Path-tag buy fills AD only; Fail / board panic may fill panic half.
            if fail_add_panic and gate.layer_idxs:
                panic_idxs = {
                    ly.idx for ly in plan.fills.buy_layers
                    if ly.role == "panic" and ly.idx in gate.layer_idxs
                }
                gate.layer_idxs = panic_idxs
                if not panic_idxs:
                    from machine.size import SizeGateResult
                    gate = SizeGateResult(action="wait", why="Fail add-panic — no panic layer reached", layer_idxs=set())
            elif (
                not fail_add_panic
                and not self.board_panic
                and gate.layer_idxs
            ):
                ad_idxs = {
                    ly.idx for ly in plan.fills.buy_layers
                    if ly.role == "AD" and ly.idx in gate.layer_idxs
                }
                gate.layer_idxs = ad_idxs
                if not ad_idxs:
                    from machine.size import SizeGateResult
                    gate = SizeGateResult(
                        action="wait",
                        why="Path buy — no AD buy layer reached",
                        layer_idxs=set(),
                    )
            if gate.action != "buy" or not gate.layer_idxs:
                # Row keeps wait (not tape). Tape sit-out carries Size why (G4 CLOSE).
                plan.last_decision = "wait"
                plan.last_why = gate.why
                result["action"] = "wait"
                result["why"] = gate.why
                size_why = gate.why if gate.why.startswith("Size") or "Size" in gate.why else f"Size — {gate.why}"
                if price_at or self.board_grind:
                    self.log.append(
                        "sit-out",
                        size_why,
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
                print_action = "paper-buy" if events[0].role == "AD" else "add-panic"
                # Row + tape use print shape; API result action stays buy for Path/Fail take.
                plan.last_decision = print_action
                if print_action == "add-panic":
                    fill_why = path_dec.why if "Fail" in path_dec.why else (
                        "Fail — current price broke AD; add panic half"
                    )
                else:
                    # Path spoke the tag/panic; Size filled — one speaker: Path why on paper-buy
                    fill_why = path_dec.why
                plan.last_why = fill_why
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
                    print_action,
                    fill_why,
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
                sell_price = pr.high if pr.high is not None else pr.price
                sell_events = try_fill_sells(plan.fills, sell_price)
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
            # API action stays buy/sit/wait; plan.last_decision holds print shape
            api_map = {
                "paper-buy": "buy",
                "add-panic": "buy",
                "paper-sell": "sell",
                "sit-out": "sit",
                "exit-live": "sell",
            }
            result["action"] = api_map.get(plan.last_decision, plan.last_decision)
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
            self._write_outcome(plan, reason="layers flat")

    def kill(self, plan_id: str, why: str = "kill") -> None:
        plan = self.plans.get(plan_id)
        if not plan:
            return
        plan.killed = True
        plan.state = "out"
        plan.last_decision = "kill"
        plan.last_why = why
        killed_at = datetime.now(ZoneInfo("Asia/Manila")).strftime("%Y-%m-%d %H:%M PHT")
        plan.play["killed"] = True
        plan.play["state"] = "out"
        plan.play["status"] = "killed_out"
        plan.play["killed_why"] = why
        plan.play["killed_at"] = killed_at
        self._write_outcome(plan, reason=why, persist=True)
        self.log.append("kill", why, name=plan.name, price=plan.current_price, force=True)
        self.closes.append({"name": plan.name, "reason": why})

    def _fills_summary(self, plan: PlanState) -> dict[str, int]:
        buys = plan.fills.buy_layers
        sells = plan.fills.sell_layers
        return {
            "buys_filled": sum(1 for b in buys if b.status == "filled"),
            "buys_remaining": len(plan.fills.remaining_buys()),
            "sells_filled": sum(1 for s in sells if getattr(s, "status", "") == "filled"),
            "sells_remaining": len(plan.fills.remaining_sells()),
        }

    def _write_outcome(self, plan: PlanState, *, reason: str, persist: bool = True) -> None:
        """Durable close on the same play JSON DecisionLoop loads. No invent money."""
        closed_at = datetime.now(ZoneInfo("Asia/Manila")).strftime("%Y-%m-%d %H:%M PHT")
        outcome = {
            "closed": True,
            "closed_at": closed_at,
            "reason": reason,
            "last_decision": plan.last_decision,
            "last_why": plan.last_why,
            "state": plan.state,
            "killed": bool(plan.killed),
            "fills": self._fills_summary(plan),
            "price": plan.current_price,
        }
        plan.play["outcome"] = outcome
        plan.play["state"] = plan.state
        if persist:
            self._persist_play(plan)

    def _resolve_play_path(self, plan: PlanState) -> Path | None:
        if plan.play_path is not None:
            return plan.play_path
        candidate = PLAYS_DIR / f"{plan.id}.json"
        if candidate.exists():
            return candidate
        return None

    def _persist_play(self, plan: PlanState) -> None:
        """Persist kill / outcome stamps to data/plays. No invent Size.

        If the plan was loaded from a file (play_path set), write plan.play.
        If only an id match exists on disk, merge kill fields into that file
        so an in-memory hang cannot overwrite Lock Size.
        """
        path = self._resolve_play_path(plan)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        kill_keys = ("killed", "state", "status", "killed_why", "killed_at", "outcome")
        if plan.play_path is not None and path == plan.play_path:
            path.write_text(json.dumps(plan.play, indent=2) + "\n")
            return
        # Id-only match: stamp kill fields onto existing file; keep Size / AD.
        existing = json.loads(path.read_text()) if path.exists() else dict(plan.play)
        for k in kill_keys:
            if k in plan.play:
                existing[k] = plan.play[k]
        path.write_text(json.dumps(existing, indent=2) + "\n")
        plan.play_path = path

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
            "killed": plan.killed,
            "layers": [b.to_dict() for b in plan.fills.buy_layers],
            "sell_layers": [s.to_dict() for s in sells],
        }
        # Reds + $vol on ranked and sheet (Kenneth overview). Bounce why stays sheet-only.
        row["chosen_tf_reds"] = plan.live_chosen_tf_reds
        fts = plan.play.get("faster_tfs") or []
        row["faster_tf"] = plan.live_faster_tf or (str(fts[0]) if fts else None)
        row["faster_tf_reds"] = plan.live_faster_tf_reds
        row["vol_usd"] = plan.live_vol_usd
        row["reds_5m"] = plan.live_reds_5m
        row["vol_usd_5m"] = plan.live_vol_usd_5m
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
