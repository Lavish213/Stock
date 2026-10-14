# lavish_core/trading/alert_handler.py
# Shared by every alert source (Patreon, Discord, anything added later):
# parse whatever text/image came in, build the signal shape
# trade_handler.execute_trade_from_post() expects, and hand it off.
# Previously Patreon and Discord each had their own parsing logic - Patreon's
# was a bare ticker+BUY/SELL regex with no strike/expiry/target/stop
# awareness at all, so a Patreon-sourced options alert silently downgraded
# into a plain equity guess. Both sources now get the same fidelity.
from __future__ import annotations
import os, re
from lavish_core.utils.safety import finite_number
from pathlib import Path
from typing import Optional

from lavish_core.logger_setup import get_logger
from lavish_core.vision.extract_signal import parse_alert, has_hedge_language, HEDGE_CONFIDENCE_PENALTY
from lavish_core.trading.trade_handler import execute_trade_from_post
from lavish_core.utils.alerts import post_discord

log = get_logger("alert_handler", log_dir="logs")

DEFAULT_CONFIDENCE = float(os.getenv("ALERT_DEFAULT_CONFIDENCE", "0.7"))

# Equity confidence, broken into the same kind of auditable, field-based
# components extract_signal.py already uses for options - a flat default
# regardless of content was itself a blunt-instrument regression from
# that per-field scoring (just miscalibrated for equity's structurally
# different field set, which used to put every plain equity alert below
# CONF_FLOOR - see execute_trade_from_post's history). Weights are chosen
# so the common case (an exact ticker match, a clear single-direction
# word, no stated target/stop, no hedging) lands at exactly the old flat
# default (0.35 + 0.35 = 0.70) - this changes the GRADIENT around that
# point, not the baseline.
EQUITY_TICKER_MATCH_WEIGHT = 0.35
EQUITY_DIRECTION_WEIGHT = 0.35
EQUITY_TARGET_OR_STOP_BONUS = 0.15

BUY_WORDS = ("BUY", "CALL", "LONG", "BTO", "ENTRY")
SELL_WORDS = ("SELL", "PUT", "SHORT", "STC", "EXIT", "CLOSE")

# Real gap found this round: "buying AAPL here" / "selling my position" /
# "shorting this" / "exiting now" / "closing out" are completely normal,
# common phrasing for the exact same intent as BUY/SELL/SHORT/EXIT/CLOSE -
# but the \bBUYS?\b-style word-boundary match (optional plural "S" only)
# never matched the "-ing" gerund form, so any alert phrased this way was
# silently treated as "no clear direction" and skipped entirely, never
# even reaching the confidence question. Deliberately NOT every word:
# CALL/PUT's gerunds (CALLING/PUTTING) are common, everyday English words
# with nothing to do with trading ("calling my broker", "putting my stop
# at X") - adding them would be a real false-positive risk. LONG/BTO/STC/
# ENTRY don't have a natural gerund that means anything here either
# ("longing AAPL" isn't real trading phrasing - "going long" is, a
# different pattern entirely). An explicit map, not naive "+ING"
# concatenation, because "close" -> "closing" drops the silent E.
_GERUND_FORMS = {"BUY": "BUYING", "SELL": "SELLING", "SHORT": "SHORTING",
                  "EXIT": "EXITING", "CLOSE": "CLOSING"}

# Real screenshots seen this session showed her also trading perpetual
# futures/leveraged tokens on Hyperliquid ("MU/USDC-P", "QQQ tokenized
# perp") - a completely different instrument and risk/leverage profile
# than plain equity. Alpaca can't execute perps/leveraged tokens at all,
# and nothing here could safely translate "10x leveraged MU perp" into
# "buy $500 of plain MU shares" - that's not copying her trade, it's
# making an unrelated bet on the same ticker. Detect and skip loudly
# instead of silently downgrading it into an equity buy.
_PERP_RE = re.compile(
    r"\b[A-Z]{1,6}[-/]USDC?-?P\b"      # MU/USDC-P, SNDK-USDC-P style pair notation
    r"|\bperp(?:etual)?s?\b"           # "perp", "perps", "perpetual futures"
    r"|\btokeniz(?:ed)?\s+perp\b"      # "tokenized perp"
    r"|\b\d{1,3}x\s*lev(?:erage)?\b",  # "10x leverage" / "5x lev" - not bare "10x" (too hype-language-prone)
    re.IGNORECASE,
)


def _is_perp_or_leveraged(text: str) -> bool:
    return bool(_PERP_RE.search(text or ""))


def _nonactionable(text):
    normalized = " ".join((text or "").lower().split())
    # There is no scheduler or conditional-order contract in this router.
    # A source timestamp proves freshness, not that "tomorrow" means now.
    return "?" in normalized or bool(re.search(
        r"\b(do not|don't|don’t|never|no entry|no trade|not in yet|watch only|if|only if|might|maybe|should i|yesterday|cancel(?:ed|led)?|ignore|disregard|stay out|was wrong|was a mistake|failed entry|close to|into the close|long term|exit strategy|tomorrow|later|next (?:week|month|session)|at the open|when|once|until|after)\b"
        r"|\b(?:not|no)\s+(?:going\s+to\s+)?(?:buy(?:ing)?|sell(?:ing)?|enter(?:ing)?|clos(?:e|ing)|exit(?:ing)?|long|short(?:ing)?)\b",
        normalized))


def _option_intent(text, parsed):
    return bool(parsed.get("side") or re.search(r"\b(calls?|puts?|options?|bto|stc)\b|\d[cp]\b", text or "", re.I))


def _equity_action_from_text(text: str) -> Optional[str]:
    # Word-boundary match, not substring - "w in up" would match "CALL"
    # inside "RECALL"/"CALLBACK" and "PUT" inside "DISPUTE"/"COMPUTE"/
    # "REPUTATION", turning ordinary commentary next to a real ticker into
    # a false BUY/SELL classification. Optional trailing "S" so plural
    # casual phrasing ("grabbing calls", "loading puts", "adding to my
    # longs", "exits the position") still matches - a bare \bCALL\b would
    # otherwise miss "CALLS" entirely (the "L"->"S" boundary doesn't
    # exist, both are word characters).
    if _nonactionable(text):
        return None
    # This path opens longs or fully closes a verified long. It has no
    # contract for shorts, covers, or source-directed partial quantities.
    # Never turn "trim half" into liquidation of the entire holding.
    if re.search(r"\b(short(?:s|ing)?|cover(?:s|ing)?|btc|trim(?:s|ming)?|half|quarter|partial(?:ly)?|some|portion)\b"
                 r"|\d+(?:\.\d+)?\s*%"
                 r"|\b(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten)\s+shares?\b", text or "", re.I):
        return None
    up = (text or "").upper()

    def _any_match(words: tuple) -> bool:
        for w in words:
            if re.search(rf"\b{re.escape(w)}S?\b", up):
                return True
            gerund = _GERUND_FORMS.get(w)
            if gerund and re.search(rf"\b{re.escape(gerund)}\b", up):
                return True
        return False

    has_buy = _any_match(BUY_WORDS) or bool(re.search(r"\b(adding|grabbing shares)\b", up, re.I))
    has_sell = _any_match(SELL_WORDS) or bool(re.search(r"\btrimming\b", up, re.I))
    if has_buy and not has_sell:
        return "BUY"
    if has_sell and not has_buy:
        return "SELL"
    return None


def _equity_confidence(parsed: dict, text: str) -> float:
    """
    Field-based, same philosophy as extract_signal.py's options score,
    calibrated for equity's structurally different field set (no side/
    strike/expiry to lean on). Reaching this function already means
    _equity_action_from_text() found an unambiguous single direction -
    that binary classifier IS the real direction-confidence check (it
    returns None on any ambiguity), so it earns full credit here rather
    than a second, redundant numeric score.
    """
    ticker_score = parsed.get("ticker_score") or 100
    conf = EQUITY_TICKER_MATCH_WEIGHT * (ticker_score / 100.0)
    conf += EQUITY_DIRECTION_WEIGHT
    if parsed.get("target_hint") or parsed.get("stop_hint"):
        conf += EQUITY_TARGET_OR_STOP_BONUS
    if has_hedge_language(text):
        conf -= HEDGE_CONFIDENCE_PENALTY
    return round(min(0.99, max(0.0, conf)), 4)


def handle_alert_text(
    text: str,
    image_path: Optional[str] = None,
    note: str = "",
    source: str = "alert",
    amount_usd: Optional[float] = None,
) -> None:
    """
    Core logic, independent of which source called it: parse an alert
    (text and/or an attached trade-card image) and hand it to the
    execution pipeline. Used by both the Patreon poller and the Discord
    listener so "whichever source posts first" behaves identically either
    way - trade_handler.execute_trade_from_post() dedupes the second
    source's copy of the same alert.
    """
    if _nonactionable(text):
        return
    if amount_usd is not None:
        try:
            finite_number(amount_usd, positive=True)
        except ValueError:
            return
    if _is_perp_or_leveraged(text):
        log.warning("[%s] Alert looks like a perp/leveraged-token trade (not equity/options) - "
                    "skipping, can't be safely copied as a plain stock buy: %r", source, (text or "")[:200])
        post_discord(
            f"⚠️ Skipped a {source} alert that looks like a perpetual futures/leveraged-token trade, "
            f"not equity or options - this bot can't execute those and won't guess-translate it into "
            f"a plain stock buy (different leverage/risk profile entirely). Review manually if you want "
            f"to act on it: {(text or '')[:200]}"
        )
        return

    parsed = parse_alert(text=text, img_path=Path(image_path) if image_path else None, source=source)

    instructions = text + "\n" + (parsed.get("instruction_text") or "")
    if _nonactionable(instructions) or _is_perp_or_leveraged(instructions):
        return

    if parsed.get("field_conflicts"):
        log.warning("[%s] Conflicting or invalid alert fields require review: %s", source, parsed["field_conflicts"])
        return
    ticker = parsed.get("ticker")
    if not ticker:
        log.info("[%s] No ticker found in alert, skipping: %r", source, (text or "")[:200])
        return

    # extract_signal.py's confidence score is weighted for OPTIONS fields
    # specifically (side/strike/expiry are worth 0.50 of it combined) - an
    # equity-only alert structurally cannot earn those points, capping it
    # at ticker+entry+target/stop (0.45 at best), always under CONF_FLOOR
    # (0.55 default) no matter how clearly worded the buy/sell intent was.
    # Confirmed directly: "BUY NVDA now, breaking out" - completely
    # unambiguous - scored 0.25 and was silently skipped by
    # trade_handler's confidence-floor check before this fix, meaning NO
    # plain equity alert from either Patreon or Discord could ever
    # actually execute. _equity_action_from_text() succeeding below is
    # itself the real confidence signal for an equity call - it's a
    # separate, dedicated classifier, not a numeric score meant to be
    # compared against the options heuristic at all.
    options_confidence = parsed.get("confidence") or DEFAULT_CONFIDENCE

    if parsed.get("side") in ("CALL", "PUT") and parsed.get("strike") and parsed.get("expiry"):
        # This route only opens long contracts. Exit/short instructions must
        # never be translated into another buy; bracket exits use their own
        # verified-position lifecycle.
        if re.search(r"\b(stc|sto|btc|sell(?:ing)?|clos(?:e|ing)|exit(?:ing)?|trim(?:ming)?|short(?:ing)?)\b", instructions, re.I):
            log.warning("[%s] Options close/short instruction requires position review", source)
            return
        signal = {
            "source": source,
            "action": "BUY",
            "ticker": ticker,
            "side": parsed["side"],
            "strike": parsed["strike"],
            "expiry": parsed["expiry"],
            "confidence": options_confidence,
            "target_hint": parsed.get("target_hint"),
            "stop_hint": parsed.get("stop_hint"),
            "note": note or parsed.get("notes_excerpt", ""),
            "amount_usd": amount_usd,
        }
        log.info("[%s] Parsed options alert: %s", source, signal)
        execute_trade_from_post(signal)
        return

    if _option_intent(text, parsed):
        return
    action = _equity_action_from_text(instructions)
    if not action:
        log.info("[%s] Ticker %s found but no clear BUY/SELL direction, skipping: %r",
                  source, ticker, (text or "")[:200])
        return

    equity_confidence = _equity_confidence(parsed, text)

    signal = {
        "source": source,
        "action": action,
        "symbol": ticker,
        "confidence": equity_confidence,
        "note": note or parsed.get("notes_excerpt", ""),
        "amount_usd": amount_usd,
        # She doesn't always give these on equity calls either, but when she
        # does, trade_handler prefers them over the fixed pct bracket.
        "target_hint": parsed.get("target_hint"),
        "stop_hint": parsed.get("stop_hint"),
    }
    log.info("[%s] Parsed equity alert: %s", source, signal)
    execute_trade_from_post(signal)
