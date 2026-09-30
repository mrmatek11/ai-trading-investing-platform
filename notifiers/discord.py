"""
Discord Notifier Module
Wysyła sygnały tradingowe na Discord przez Webhook.
Piękne embedy z kolorami, ikonami i formatowaniem.
"""

import requests
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional, List
from collections import deque
from strategy.signal_detector import Signal

logger = logging.getLogger(__name__)


class DiscordNotifier:
    """
    Wysyła powiadomienia o sygnałach na Discord przez Webhook URL.
    
    Setup:
        1. Otwórz Discord → Ustawienia kanału → Integracje → Webhooks
        2. Stwórz nowy webhook, skopiuj URL
        3. Podaj URL w konfiguracji bota
    """

    def __init__(
        self,
        webhook_url: str,
        bot_name: str = "Multi-Asset Signal Bot",
        avatar_url: str = "",
        mention_role_id: Optional[str] = None,   # ID roli do @mention
        mention_on_long: bool = True,
        mention_on_short: bool = True,
        quiet_hours: Optional[dict] = None,       # {"start": 23, "end": 7} — cisza nocna
    ):
        self.webhook_url = webhook_url
        self.bot_name = bot_name
        self.avatar_url = avatar_url
        self.mention_role_id = mention_role_id
        self.mention_on_long = mention_on_long
        self.mention_on_short = mention_on_short
        self.quiet_hours = quiet_hours
        self._last_signal_keys = deque(maxlen=500)  # Anti-spam: nie wysyłaj dwa razy tego samego
        self._last_signal_keys_set = set()  # For O(1) lookup
        self._last_send_time = 0  # Rate limiting

    def send_signal(self, signal: Signal, force: bool = False) -> bool:
        """
        Wyślij pojedynczy sygnał na Discord.
        Zwraca True jeśli wysłano, False jeśli zablokowano (spam/quiet hours).
        """
        # Anti-spam: sprawdź czy ten sygnał już był wysłany
        signal_key = f"{signal.symbol}_{signal.timeframe}_{signal.signal_type}_{signal.k_value}_{signal.timestamp}"
        if signal_key in self._last_signal_keys_set and not force:
            return False
        # Evict oldest if at capacity
        if len(self._last_signal_keys) >= 500:
            try:
                oldest = self._last_signal_keys.popleft()
                self._last_signal_keys_set.discard(oldest)
            except IndexError:
                pass
        self._last_signal_keys.append(signal_key)
        self._last_signal_keys_set.add(signal_key)

        # Rate limit: min 0.5s between sends
        now = time.time()
        if not force and now - self._last_send_time < 0.5:
            time.sleep(0.5 - (now - self._last_send_time))

        # Quiet hours check
        if self.quiet_hours and not force:
            hour = datetime.now(timezone.utc).hour
            start = self.quiet_hours.get("start", 0)
            end = self.quiet_hours.get("end", 0)
            if start <= end:
                if start <= hour < end:
                    return False
            else:  # overnight (np. 23-7)
                if hour >= start or hour < end:
                    return False

        embed = self._build_signal_embed(signal)
        
        # @mention dla ról
        content = ""
        if self.mention_role_id:
            should_mention = (
                (signal.signal_type == "LONG" and self.mention_on_long) or
                (signal.signal_type == "SHORT" and self.mention_on_short)
            )
            if should_mention:
                content = f"<@&{self.mention_role_id}>"

        payload = {
            "username": self.bot_name,
            "avatar_url": self.avatar_url,
            "content": content,
            "embeds": [embed],
        }

        if self.avatar_url:
            payload["avatar_url"] = self.avatar_url

        result = self._send_webhook(payload)
        self._last_send_time = time.time()
        return result

    def send_signals_batch(self, signals: List[Signal]) -> int:
        """Wyślij listę sygnałów. Zwraca liczbę wysłanych."""
        sent = 0
        for signal in signals:
            if self.send_signal(signal):
                sent += 1
        return sent

    def send_status(self, symbol: str, timeframe: str, values: dict,
                    detector_config: dict = None) -> bool:
        """Wyślij status monitorowania (rzadziej, jako info)."""
        zone = values.get("zone", "neutral")
        zone_emoji = {"oversold": "🟢", "overbought": "🔴", "neutral": "⚪"}.get(zone, "⚪")

        embed = {
            "title": f"📊 Status: {symbol} ({timeframe})",
            "color": 0x2196F3,
            "fields": [
                {"name": "Price", "value": f"${values['price']:,.2f}", "inline": True},
                {"name": "Stoch %K", "value": f"{values['stoch_k']:.1f}", "inline": True},
                {"name": "Stoch %D", "value": f"{values['stoch_d']:.1f}", "inline": True},
                {"name": "Zone", "value": f"{zone_emoji} {zone.upper()}", "inline": True},
                {"name": "RSI", "value": f"{values.get('rsi', 'N/A')}", "inline": True},
                {"name": "ATR", "value": f"{values.get('atr', 'N/A')}", "inline": True},
            ],
            "footer": {"text": f"Stoch({detector_config.get('k_length', 7)},{detector_config.get('k_smooth', 3)},{detector_config.get('d_smooth', 2)})"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        payload = {
            "username": self.bot_name,
            "avatar_url": self.avatar_url,
            "embeds": [embed],
        }

        return self._send_webhook(payload)

    def send_startup_message(self, config: dict) -> bool:
        """Wyślij wiadomość o starcie bota."""
        symbols = ", ".join(config.get("symbols", []))
        timeframes = ", ".join(config.get("timeframes", []))

        embed = {
            "title": "🚀 Bot uruchomiony!",
            "color": 0x00BCD4,
            "fields": [
                {"name": "Monitoring", "value": symbols, "inline": False},
                {"name": "Timeframes", "value": timeframes, "inline": False},
                {"name": "Strategy", "value": config.get("strategy_name", "NWO + Stoch + CVD"), "inline": True},
                {"name": "Stochastic", "value": f"({config.get('stoch_k_length', 7)}, {config.get('stoch_k_smooth', 3)}, {config.get('stoch_d_smooth', 2)})", "inline": True},
                {"name": "NWO BWM", "value": f"Best: {config.get('best_criterion', 'Trend')} | Worst: {config.get('worst_criterion', 'Momentum')}", "inline": True},
                {"name": "Oversold", "value": f"< {config.get('oversold_threshold', 20)}", "inline": True},
                {"name": "Overbought", "value": f"> {config.get('overbought_threshold', 80)}", "inline": True},
                {"name": "Interval", "value": f"{config.get('scan_interval', 60)}s", "inline": True},
                {"name": "Adaptive Training", "value": "✅" if config.get('use_training', True) else "❌", "inline": True},
            ],
            "footer": {"text": "Crypto Signal Bot — NWO + Stoch + CVD"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        payload = {
            "username": self.bot_name,
            "avatar_url": self.avatar_url,
            "embeds": [embed],
        }

        return self._send_webhook(payload)

    def send_custom_embed(self, embed: dict) -> bool:
        """Wyślij custom embed na Discord (public API)."""
        payload = {
            "username": self.bot_name,
            "embeds": [embed],
        }
        if self.avatar_url:
            payload["avatar_url"] = self.avatar_url
        return self._send_webhook(payload)

    def send_error(self, error_msg: str) -> bool:
        """Wyślij powiadomienie o błędzie."""
        embed = {
            "title": "⚠️ Błąd bota",
            "description": f"```{error_msg[:500]}```",
            "color": 0xFF9800,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        payload = {
            "username": self.bot_name,
            "embeds": [embed],
        }

        return self._send_webhook(payload)

    def send_position_stats(self, stats: dict) -> bool:
        """Wyślij statystyki pozycji na Discord."""
        if stats["total_trades"] == 0:
            return True

        by_dir_lines = []
        for direction, data in stats.get("by_direction", {}).items():
            by_dir_lines.append(f"{direction}: {data['count']} trades | WR: {data['win_rate']:.1f}% | PnL: ${data['pnl']:+,.2f}")

        by_dir_text = "\n".join(by_dir_lines) if by_dir_lines else "N/A"

        embed = {
            "title": "📊 Position Stats",
            "color": 0x00E676 if stats["total_pnl"] > 0 else 0xFF1744,
            "fields": [
                {"name": "Total Trades", "value": str(stats["total_trades"]), "inline": True},
                {"name": "Win Rate", "value": f"{stats['win_rate']}%", "inline": True},
                {"name": "Profit Factor", "value": str(stats["profit_factor"]), "inline": True},
                {"name": "Total PnL", "value": f"${stats['total_pnl']:+,.2f}", "inline": True},
                {"name": "Best Trade", "value": f"${stats['best_trade']:+,.2f}", "inline": True},
                {"name": "Worst Trade", "value": f"${stats['worst_trade']:+,.2f}", "inline": True},
                {"name": "Avg Hold Time", "value": f"{stats['avg_holding_hours']:.1f}h", "inline": True},
                {"name": "Max Drawdown", "value": f"${stats['max_drawdown']:.2f}", "inline": True},
                {"name": "By Direction", "value": by_dir_text, "inline": False},
            ],
            "footer": {"text": "Position Tracker | Crypto Signal Bot"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        payload = {
            "username": self.bot_name,
            "embeds": [embed],
        }

        return self._send_webhook(payload)

    # ─── Internal ──────────────────────────────────────────────────────────

    def _build_signal_embed(self, signal: Signal) -> dict:
        """Buduje Discord embed z sygnału."""

        # Check if against trend (high risk)
        against_trend = signal.extra_data.get("against_trend", False)
        risk_level = signal.extra_data.get("risk_level", "NORMAL")

        # Nagłówek z kierunkiem
        if signal.signal_type == "LONG":
            if against_trend:
                title = f"⚠️ LONG (RISKY) — {signal.symbol}"
                direction_text = "🟡 **LONG SIGNAL — AGAINST DOWNTREND**"
            else:
                title = f"{signal.emoji} LONG — {signal.symbol}"
                direction_text = "🟢 **LONG SIGNAL**"
        else:
            if against_trend:
                title = f"⚠️ SHORT (RISKY) — {signal.symbol}"
                direction_text = "🟡 **SHORT SIGNAL — AGAINST UPTREND**"
            else:
                title = f"{signal.emoji} SHORT — {signal.symbol}"
                direction_text = "🔴 **SHORT SIGNAL**"

        # Strefa stocha
        if signal.k_value < 20:
            zone_text = "🟢 OVERSOLD"
        elif signal.k_value > 80:
            zone_text = "🔴 OVERBOUGHT"
        else:
            zone_text = "⚪ NEUTRAL"

        # Oblicz suggested SL/TP na bazie ATR (v2: 3x ATR SL, 4.5x ATR TP)
        atr = signal.extra_data.get("atr", 0)
        sl_mult = signal.extra_data.get("sl_atr_mult", 3.0)
        tp_mult = signal.extra_data.get("tp_atr_mult", 4.5)
        
        # Pre-computed SL/TP from strategy (if available)
        pre_sl = signal.extra_data.get("sl")
        pre_tp = signal.extra_data.get("tp")
        
        if pre_sl and pre_tp and atr > 0:
            # Użyj pre-computed values z custom_strategy.py
            sl_tp_text = (f"SL: ${pre_sl:,.2f} ({sl_mult:.1f}x ATR)\n"
                         f"TP: ${pre_tp:,.2f} ({tp_mult:.1f}x ATR)\n"
                         f"R:R = 1:{tp_mult/sl_mult:.1f}")
        elif atr > 0 and signal.signal_type == "LONG":
            sl = signal.price - atr * sl_mult
            tp = signal.price + atr * tp_mult
            sl_tp_text = (f"SL: ${sl:,.2f} ({sl_mult:.1f}x ATR)\n"
                         f"TP: ${tp:,.2f} ({tp_mult:.1f}x ATR)\n"
                         f"R:R = 1:{tp_mult/sl_mult:.1f}")
        elif atr > 0 and signal.signal_type == "SHORT":
            sl = signal.price + atr * sl_mult
            tp = signal.price - atr * tp_mult
            sl_tp_text = (f"SL: ${sl:,.2f} ({sl_mult:.1f}x ATR)\n"
                         f"TP: ${tp:,.2f} ({tp_mult:.1f}x ATR)\n"
                         f"R:R = 1:{tp_mult/sl_mult:.1f}")
        else:
            sl_tp_text = "N/A"

        rsi_val = signal.extra_data.get("rsi")
        osc_val = signal.extra_data.get("osc")
        cvd_val = signal.extra_data.get("cvd")
        source = signal.extra_data.get("source", "")

        # Build fields
        fields = [
            {
                "name": "Kierunek",
                "value": direction_text,
                "inline": True
            },
            {
                "name": "Cena",
                "value": f"💰 **${signal.price:,.2f}**",
                "inline": True
            },
            {
                "name": "Timeframe",
                "value": f"⏱️ {signal.timeframe}",
                "inline": True
            },
        ]

        # Stochastic
        fields.append({
            "name": "Stoch (7,3,2)",
            "value": f"K: **{signal.k_value:.1f}** | D: **{signal.d_value:.1f}** | {zone_text}",
            "inline": True
        })

        # NWO Oscillator
        if osc_val is not None:
            osc_emoji = "🟢" if osc_val < 30 else ("🔴" if osc_val > 70 else "⚪")
            fields.append({
                "name": "NWO Oscillator",
                "value": f"{osc_emoji} **{osc_val:.1f}**",
                "inline": True
            })

        # CVD
        if cvd_val is not None:
            cvd_emoji = "🟢" if cvd_val > 0 else ("🔴" if cvd_val < 0 else "⚪")
            fields.append({
                "name": "CVD",
                "value": f"{cvd_emoji} {cvd_val:+.2f}",
                "inline": True
            })

        # RSI
        if rsi_val is not None:
            fields.append({
                "name": "RSI",
                "value": f"{rsi_val:.1f}",
                "inline": True
            })

        # Source tag
        if source:
            source_names = {
                "NWO": "🧠 Neural Weight Osc",
                "STOCH": "📊 Stochastic",
                "STOCH+NWO": "📊 Stoch + 🧠 NWO",
                "CONFLUENCE": "⚡ Confluence (Stoch+NWO+CVD)",
            }
            fields.append({
                "name": "Źródło",
                "value": source_names.get(source, source),
                "inline": True
            })

        # NWO Histogram direction
        histogram_val = signal.extra_data.get("histogram")
        if histogram_val is not None:
            dir_emoji = "🟢" if histogram_val > 0 else "🔴"
            dir_text = "BULLISH" if histogram_val > 0 else "BEARISH"
            fields.append({
                "name": "NWO Kierunek",
                "value": f"{dir_emoji} {dir_text} (hist={histogram_val:.3f})",
                "inline": True
            })
        
        # Trend
        trend = signal.extra_data.get("trend")
        if trend:
            trend_emoji = "📈" if trend == "UP" else "📉"
            fields.append({
                "name": "Trend",
                "value": f"{trend_emoji} {trend}",
                "inline": True
            })
        
        # Sentiment (AI news analysis)
        sentiment_score = signal.extra_data.get("sentiment_score")
        sentiment_label = signal.extra_data.get("sentiment_label")
        sentiment_summary = signal.extra_data.get("sentiment_summary")
        if sentiment_score is not None:
            sent_emoji = {"VERY_BULLISH": "🔥🟢", "BULLISH": "🟢", "NEUTRAL": "⚪", "BEARISH": "🔴", "VERY_BEARISH": "🔥🔴"}.get(sentiment_label, "⚪")
            sent_text = f"{sent_emoji} {sentiment_label} ({sentiment_score:+.2f})"
            if sentiment_summary:
                sent_text += f"\n📰 {sentiment_summary[:80]}"
            fields.append({
                "name": "📰 AI Sentiment",
                "value": sent_text,
                "inline": False
            })

        # GLM AI Signal Score
        glm_score = signal.extra_data.get("glm_score")
        glm_rec = signal.extra_data.get("glm_recommendation")
        glm_analysis = signal.extra_data.get("glm_analysis")
        glm_factors = signal.extra_data.get("glm_key_factors", [])
        glm_risks = signal.extra_data.get("glm_risks", [])
        if glm_score is not None:
            score_emoji = {"TAKE": "\u2705", "WATCH": "\ud83d\udc40", "SKIP": "\u274c"}.get(glm_rec, "\u26aa")
            score_bar = "\ud83d\udd25" if glm_score >= 8 else ("\ud83d\udfe2" if glm_score >= 6 else ("\ud83d\udfe1" if glm_score >= 4 else "\ud83d\udd34"))
            glm_text = f"{score_bar} {glm_score}/10 {score_emoji} {glm_rec}"
            if glm_analysis:
                glm_text += f"\n\ud83d\udca1 {glm_analysis}"
            if glm_factors:
                glm_text += f"\n\u2705 Factors: {', '.join(glm_factors[:3])}"
            if glm_risks:
                glm_text += f"\n\u26a0\ufe0f Risks: {', '.join(glm_risks[:3])}"
            fields.append({
                "name": "🧠 GLM AI Score",
                "value": glm_text,
                "inline": False
            })

        # GLM Regime
        glm_regime = signal.extra_data.get("glm_regime")
        glm_regime_bias = signal.extra_data.get("glm_regime_bias")
        if glm_regime:
            regime_emoji = {"trending_up": "📈", "trending_down": "📉", "volatile": "⚡", "ranging": "↔️", "quiet": "😴"}.get(glm_regime, "❓")
            bias_emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(glm_regime_bias, "")
            regime_text = f"{regime_emoji} {glm_regime.upper()}"
            if glm_regime_bias:
                regime_text += f" | Bias: {bias_emoji} {glm_regime_bias.upper()}"
            fields.append({
                "name": "📊 GLM Regime",
                "value": regime_text,
                "inline": True
            })

        # GLM Multi-TF Confluence
        glm_conf_score = signal.extra_data.get("glm_confluence_score")
        glm_conf_dir = signal.extra_data.get("glm_confluence_direction")
        glm_conf_tf = signal.extra_data.get("glm_confluence_strongest_tf")
        if glm_conf_score is not None:
            conf_emoji = {"bullish": "🟢", "bearish": "🔴", "mixed": "🟡", "neutral": "⚪"}.get(glm_conf_dir, "⚪")
            conf_text = f"{conf_emoji} {glm_conf_score}/10 {glm_conf_dir.upper()}"
            if glm_conf_tf:
                conf_text += f" | Strongest: {glm_conf_tf}"
            fields.append({
                "name": "🔀 GLM Confluence",
                "value": conf_text,
                "inline": True
            })

        # Risk warning (against trend)
        if against_trend:
            fields.append({
                "name": "⚠️ RISK WARNING",
                "value": "**Signal AGAINST the trend!** Consider avoiding this trade. Counter-trend signals have lower win rate (~40% vs 60%+ with trend).",
                "inline": False
            })

        # SL/TP
        fields.append({
            "name": "Sugerowane SL/TP (ATR)",
            "value": sl_tp_text,
            "inline": False
        })

        # Reason
        fields.append({
            "name": "Powód",
            "value": signal.reason,
            "inline": False
        })

        embed = {
            "title": title,
            "color": 0xFF9800 if against_trend else signal.color_hex,  # Orange for risky, normal color otherwise
            "fields": fields,
            "footer": {
                "text": f"{signal.strategy_name} | Crypto Signal Bot",
                "icon_url": "https://cdn-icons-png.flaticon.com/512/6001/6001527.png",
            },
            "timestamp": signal.timestamp.isoformat() if signal.timestamp else datetime.now(timezone.utc).isoformat(),
        }

        return embed

    def _send_webhook(self, payload: dict) -> bool:
        """Wyślij payload na Discord webhook."""
        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10,
                headers={"Content-Type": "application/json"},
            )
            if response.status_code in (200, 204):
                return True
            elif response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", "1"))
                logger.warning(f"[Discord] Rate limited, retrying after {retry_after}s")
                time.sleep(retry_after)
                return self._send_webhook(payload)
            else:
                logger.warning(f"[Discord] Error {response.status_code}: {response.text[:200]}")
                return False
        except requests.exceptions.Timeout:
            logger.warning("[Discord] Timeout — webhook nie odpowiada")
            return False
        except Exception as e:
            logger.warning(f"[Discord] Błąd wysyłki: {e}")
            return False
