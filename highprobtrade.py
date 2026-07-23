# 7. Main Bot Loop (WITH PREVIOUS CANDLE DETECTION & RE-ALERT)
def run_bot():
    global last_check_time, cycle_count, api_calls_saved

    condition_names = {
        1: "Bullish Continuation (Trend+Volume)",
        3: "Bearish Continuation (Trend+Volume)",
        6: "Failed Rally in Bear Trend",
        8: "Bullish Dip Buy"
    }

    # Track when we last alerted for each condition to allow re-alerts
    last_alert_time = {}

    print("\n" + "="*70)
    print("🚀 RSI/VWMA/VOLUME SIGNAL GENERATOR - ETH/USDT")
    print("="*70)
    print(f"📊 Exchange: BINANCE ONLY")
    print(f"\n📈 CONFIGURATION:")
    print(f"  • ⚡ INSTANT ALERTS + PREVIOUS CANDLE CHECK")
    print(f"  • Symbol: ETH/USDT")
    print(f"  • Timeframe: 1 MINUTE")
    print(f"  • Scan Interval: 20 SECONDS ⚡")
    print(f"  • Checking: CURRENT + PREVIOUS candle")
    print(f"  • Indicators: RSI(7), StochRSI(14), VWMA(26/52), VolMA(20)")
    print(f"\n📊 ACTIVE CONDITIONS (1, 3, 6, 8):")
    print(f"  • Cond 1 (BUY): RSI(7)>70, StochRSI>0.8, VWMA26>VWMA52, GreenVol>VolMA20")
    print(f"  • Cond 3 (SELL): RSI(7)<30, StochRSI<0.2, VWMA52>VWMA26, RedVol>VolMA20")
    print(f"  • Cond 6 (SELL): RSI(7)>70, StochRSI>0.8, VWMA52>VWMA26, RedVol>VolMA20")
    print(f"  • Cond 8 (BUY): RSI(7)<30, StochRSI<0.2, VWMA26>VWMA52, GreenVol>VolMA20")
    print("="*70 + "\n")

    available_symbols = [s for s in SYMBOLS if s in EXCHANGE.markets]
    print(f"✅ Monitoring {len(available_symbols)}/{len(SYMBOLS)} symbols on Binance")

    if TOKEN and CHAT_ID:
        send_alert(
            f"✅ <b>RSI/VWMA/Vol Bot Started - ETH/USDT</b>\n\n"
            f"📊 <b>Exchange:</b> BINANCE ONLY\n"
            f"⏱️ <b>Timeframe:</b> 1 Minute\n"
            f"🔄 <b>Scan Interval:</b> 20 Seconds ⚡\n"
            f"🔍 <b>Checking:</b> Current + Previous candle\n"
            f"⚡ <b>Alert Mode:</b> INSTANT\n"
            f"🔍 <b>Monitoring:</b> ETH/USDT only\n"
            f"📊 <b>Conditions Active:</b> 1, 3, 6, 8\n"
            f"🕒 <b>Start:</b> {datetime.now().strftime('%H:%M:%S')}"
        )

    while True:
        try:
            cycle_count += 1
            new_signals = 0
            processed = 0

            print(f"\n{'='*70}")
            print(f"🔄 Cycle #{cycle_count} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Every 20s ⚡")
            print(f"{'='*70}")

            if cycle_count % 10 == 0:
                cleanup_cache()

            for i, symbol in enumerate(available_symbols):
                try:
                    if i > 0:
                        time.sleep(API_CALL_INTERVAL)

                    df = get_cached_ohlcv(
                        EXCHANGE, 
                        symbol, 
                        timeframe='1m',
                        limit=CANDLES_TO_FETCH
                    )

                    if df is None or len(df) < 60:
                        print(f"  ⚠️ {symbol}: Insufficient data ({len(df) if df is not None else 0} candles)")
                        continue

                    # Calculate indicators
                    indicators = calculate_indicators(df)

                    if indicators is None:
                        print(f"  ⚠️ {symbol}: Indicator calculation failed")
                        continue

                    # Get current values for display
                    current_price = df['close'].iloc[-1]
                    prev_price = df['close'].iloc[-2]
                    price_str = format_price(current_price)
                    
                    # Current candle values
                    rsi_7_curr = indicators['current_rsi_7']
                    stoch_rsi_curr = indicators['current_stoch_rsi']
                    vwma_26_curr = indicators['current_vwma_26']
                    vwma_52_curr = indicators['current_vwma_52']
                    
                    # Previous candle values
                    rsi_7_prev = indicators['rsi_7'].iloc[-2] if len(indicators['rsi_7']) >= 2 else 0
                    stoch_rsi_prev = indicators['stoch_rsi'].iloc[-2] if len(indicators['stoch_rsi']) >= 2 else 0
                    
                    trend = "BULL" if vwma_26_curr > vwma_52_curr else "BEAR"
                    candle_type = "GREEN" if current_price > df['open'].iloc[-1] else "RED"
                    prev_candle_type = "GREEN" if prev_price > df['open'].iloc[-2] else "RED"
                    
                    # Display BOTH current and previous candle
                    print(f"  {symbol:12} | {price_str:12} | {candle_type:5}")
                    print(f"    CURRENT  → RSI7:{rsi_7_curr:6.2f} | StochRSI:{stoch_rsi_curr:6.3f} | VWMA26:{vwma_26_curr:10.4f} | VWMA52:{vwma_52_curr:10.4f} | {trend}")
                    print(f"    PREVIOUS → RSI7:{rsi_7_prev:6.2f} | StochRSI:{stoch_rsi_prev:6.3f} | Candle: {prev_candle_type}")

                    # Check signals (BOTH current and previous candles)
                    signal, strength, condition_num, candle_position = check_signals(symbol, df, indicators)

                    if signal:
                        cond_name = condition_names.get(condition_num, f"Condition {condition_num}")
                        
                        # Get the actual price at the signal candle
                        signal_price = current_price if candle_position == "CURRENT" else prev_price
                        signal_price_str = format_price(signal_price)
                        
                        print(f"  🎯 {symbol}: {signal} (Cond #{condition_num} - {cond_name}) on {candle_position} candle @ {signal_price_str}")

                        # Create unique key for this signal
                        signal_key = f"{symbol}_{signal}_{condition_num}"
                        
                        # Check if we should alert (new signal OR re-alert after 3 minutes)
                        should_alert = False
                        now = datetime.now()
                        
                        if signal_key not in last_alert_time:
                            should_alert = True
                        else:
                            # Re-alert if signal persists for more than 3 minutes
                            time_since_last = (now - last_alert_time[signal_key]).total_seconds()
                            if time_since_last > 180:  # 3 minutes
                                should_alert = True
                        
                        if should_alert:
                            new_signals += 1
                            last_alert_time[signal_key] = now

                            emoji = "🟢" if signal == 'BUY' else "🔴"
                            strength_emoji = "💪" if strength == 'STRONG' else "✅"
                            candle_emoji = "🕯️" if candle_position == "CURRENT" else "⏮️"

                            message = (
                                f"🚨 <b>{signal} SIGNAL DETECTED!</b> {strength_emoji}\n\n"
                                f"<b>Symbol:</b> {symbol}\n"
                                f"<b>Exchange:</b> BINANCE\n"
                                f"<b>Signal Price:</b> {signal_price_str}\n"
                                f"<b>Current Price:</b> {price_str}\n"
                                f"<b>Condition:</b> #{condition_num} - {cond_name}\n"
                                f"<b>Strength:</b> {strength}\n"
                                f"{candle_emoji} <b>Detected on:</b> {candle_position} CANDLE\n\n"
                                f"<b>Indicators at Signal:</b>\n"
                                f"• Trend: {trend}\n"
                                f"• Current Candle: {candle_type}\n"
                                f"• Previous Candle: {prev_candle_type}\n\n"
                                f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                                f"⚡ <b>20 SECOND SCAN - CHECKING BOTH CANDLES!</b>"
                            )

                            if send_alert(message):
                                print(f"  🚨 ALERT SENT: {signal} on {candle_position} candle (Cond #{condition_num})")
                            else:
                                print(f"  ❌ Alert FAILED")
                        else:
                            # Signal exists but already alerted recently
                            time_ago = (now - last_alert_time[signal_key]).seconds
                            print(f"  ℹ️ Signal already active (alerted {time_ago}s ago, re-alert in {180-time_ago}s)")

                    processed += 1

                except Exception as e:
                    print(f"  ❌ Error processing {symbol}: {e}")
                    traceback.print_exc()
                    continue

            last_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

            print(f"\n📊 Cycle #{cycle_count} Summary (20s scan):")
            print(f"  • Exchange: BINANCE")
            print(f"  • Timeframe: 1 Minute")
            print(f"  • Checking: Current + Previous candles")
            print(f"  • Processed: {processed}/{len(available_symbols)}")
            print(f"  • New Signals: {new_signals}")
            print(f"  • API Calls Saved: {api_calls_saved}")

            if last_alert_time:
                print(f"  • Recent Alerts:")
                for key, alert_time in list(last_alert_time.items())[-5:]:
                    print(f"    - {key} @ {alert_time.strftime('%H:%M:%S')}")

            print(f"  • Next Scan: {(datetime.now() + timedelta(seconds=CHECK_INTERVAL)).strftime('%H:%M:%S')}")
            print(f"{'='*70}\n")

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n👋 Bot stopped by user")
            if TOKEN and CHAT_ID:
                send_alert("🛑 Bot stopped by user")
            break
        except Exception as e:
            print(f"❌ Critical error: {e}")
            traceback.print_exc()
            time.sleep(20)
