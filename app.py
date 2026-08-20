import os
from datetime import datetime, timedelta
from flask import Flask, request, abort
from FinMind.data import DataLoader

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', 'YOUR_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', 'YOUR_CHANNEL_SECRET')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

def safe_float(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default

def get_consecutive_days(daily_data):
    if not daily_data:
        return 0, "無資料"
    last_val = daily_data[-1]
    if last_val == 0:
        return 0, "無買賣"
    
    is_buy = last_val > 0
    count = 0
    for val in reversed(daily_data):
        if (is_buy and val > 0) or (not is_buy and val < 0):
            count += 1
        else:
            break
    return count, "連買" if is_buy else "連賣"

def calculate_kd(prices_dict):
    if len(prices_dict) < 9:
        return "N/A", "N/A"
    
    closes = [p['close'] for p in prices_dict]
    highs = [p['high'] for p in prices_dict]
    lows = [p['low'] for p in prices_dict]
    
    k = 50.0
    d = 50.0
    for i in range(8, len(closes)):
        sub_high = max(highs[i-8:i+1])
        sub_low = min(lows[i-8:i+1])
        rsv = 50.0 if sub_high == sub_low else ((closes[i] - sub_low) / (sub_high - sub_low)) * 100
        k = (2/3) * k + (1/3) * rsv
        d = (2/3) * d + (1/3) * k
    return round(k, 1), round(d, 1)

def get_stock_report(stock_id):
    dl = DataLoader()
    start_date = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
    
    # 1. 基本資訊
    try:
        df_info = dl.taiwan_stock_info()
        matched = df_info[df_info['stock_id'] == stock_id]
        if not matched.empty:
            stock_name = matched.iloc[0]['stock_name']
            category = matched.iloc[0]['industry_category']
        else:
            stock_name, category = "未知", "其他 / 待確定"
    except Exception:
        stock_name, category = "股票", "其他 / 待確定"

    # 2. 日K線價格與技術指標
    try:
        df_price = dl.taiwan_stock_daily(stock_id=stock_id, start_date=start_date)
        if not df_price.empty:
            price_list = df_price.to_dict('records')
            data_date = price_list[-1]['date']
            close_price = safe_float(price_list[-1]['close'])
            volume = int(safe_float(price_list[-1]['Trading_Volume']) // 1000)
            
            closes = [safe_float(p['close']) for p in price_list]
            
            ma5_val = sum(closes[-5:]) / 5 if len(closes) >= 5 else None
            ma20_val = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
            ma60_val = sum(closes[-60:]) / 60 if len(closes) >= 60 else None
            
            ma5 = round(ma5_val, 1) if ma5_val else "N/A"
            ma20 = round(ma20_val, 1) if ma20_val else "N/A"
            ma60 = round(ma60_val, 1) if ma60_val else "N/A"
            
            bias_5 = round(((close_price - ma5_val) / ma5_val) * 100, 1) if ma5_val else "N/A"
            
            if ma5_val and ma20_val and ma60_val:
                if close_price > ma5_val > ma20_val > ma60_val:
                    status = "多頭排列 🚀"
                elif close_price < ma5_val < ma20_val < ma60_val:
                    status = "空頭排列 📉"
                else:
                    status = "均線震盪中 ⚖️"
            else:
                status = "資料不足 ⚖️"
                
            k_val, d_val = calculate_kd(price_list)
            kd_str = f"K {k_val} / D {d_val}"
            if k_val != "N/A":
                if k_val > 80: kd_str += " (超買區 🔥)"
                elif k_val < 20: kd_str += " (超賣區 ❄️)"
        else:
            data_date, close_price, volume, ma5, ma20, ma60, status, bias_5, kd_str = datetime.now().strftime("%Y-%m-%d"), "N/A", "N/A", "N/A", "N/A", "N/A", "資料不足", "N/A", "N/A"
    except Exception:
        data_date, close_price, volume, ma5, ma20, ma60, status, bias_5, kd_str = datetime.now().strftime("%Y-%m-%d"), "N/A", "N/A", "N/A", "N/A", "N/A", "資料不足", "N/A", "N/A"

    # 3. 估值資訊
    try:
        df_per = dl.taiwan_stock_per_pbr(stock_id=stock_id, start_date=start_date)
        if not df_per.empty:
            per_list = df_per.to_dict('records')
            pe = safe_float(per_list[-1].get('PER'))
            pb = safe_float(per_list[-1].get('PBR'))
            dy = safe_float(per_list[-1].get('dividend_yield'))
            
            pe_str = f"{round(pe, 1)} 倍" if pe > 0 else "N/A"
            pb_str = f"{round(pb, 1)} 倍" if pb > 0 else "N/A"
            dy_str = f"{round(dy, 2)} %" if dy > 0 else "N/A"
        else:
            pe_str, pb_str, dy_str = "N/A", "N/A", "N/A"
    except Exception:
        pe_str, pb_str, dy_str = "N/A", "N/A", "N/A"

    # 4. 三大法人買賣超
    sync_status = ""
    sync_days = 0
    try:
        df_chip = dl.taiwan_stock_institutional_investors(stock_id=stock_id, start_date=start_date)
        if not df_chip.empty:
            chip_list = df_chip.to_dict('records')
            daily_chips = {}
            for item in chip_list:
                d = item['date']
                name = item['name']
                net = (safe_float(item['buy']) - safe_float(item['sell'])) // 1000
                if d not in daily_chips:
                    daily_chips[d] = {'Foreign': 0, 'Trust': 0, 'Dealer': 0}
                if 'Foreign' in name: daily_chips[d]['Foreign'] += net
                elif 'Investment_Trust' in name: daily_chips[d]['Trust'] += net
                elif 'Dealer' in name: daily_chips[d]['Dealer'] += net
            
            dates = sorted(daily_chips.keys())
            f_series = [daily_chips[d]['Foreign'] for d in dates]
            t_series = [daily_chips[d]['Trust'] for d in dates]
            d_series = [daily_chips[d]['Dealer'] for d in dates]
            
            f_today, t_today, d_today = int(f_series[-1]), int(t_series[-1]), int(d_series[-1])
            total_today = f_today + t_today + d_today
            
            f_cnt, f_act = get_consecutive_days(f_series)
            t_cnt, t_act = get_consecutive_days(t_series)
            d_cnt, d_act = get_consecutive_days(d_series)
            
            foreign_str = f"{f_today:+d} 張 ({f_act} {f_cnt} 天)"
            trust_str = f"{t_today:+d} 張 ({t_act} {t_cnt} 天)"
            dealer_str = f"{d_today:+d} 張 ({d_act} {d_cnt} 天)"
            total_str = f"{total_today:+d} 張"
            
            if f_today > 0 and t_today > 0 and d_today > 0:
                for f, t, d in zip(reversed(f_series), reversed(t_series), reversed(d_series)):
                    if f > 0 and t > 0 and d > 0: sync_days += 1
                    else: break
                sync_status = f"\n🔥 法人籌碼：三大法人同步買超 {sync_days} 天 🚀"
            elif f_today < 0 and t_today < 0 and d_today < 0:
                for f, t, d in zip(reversed(f_series), reversed(t_series), reversed(d_series)):
                    if f < 0 and t < 0 and d < 0: sync_days += 1
                    else: break
                sync_status = f"\n⚠️ 法人籌碼：三大法人同步賣超 {sync_days} 天 📉"
        else:
            foreign_str, trust_str, dealer_str, total_str = "N/A", "N/A", "N/A", "N/A"
    except Exception:
        foreign_str, trust_str, dealer_str, total_str = "N/A", "N/A", "N/A", "N/A"

    # 5. 千張大戶
    try:
        df_large = dl.taiwan_stock_holding_shares_per(stock_id=stock_id, start_date=start_date)
        if not df_large.empty:
            df_1000 = df_large[df_large['HoldingSharesLevel'] == '15'].to_dict('records')
            if len(df_1000) >= 2:
                latest_ratio = safe_float(df_1000[-1]['percent'])
                prev_ratio = safe_float(df_1000[-2]['percent'])
                diff = round(latest_ratio - prev_ratio, 2)
                large_holder_str = f"{latest_ratio:.2f} % (較上週 {diff:+.2f}%)"
            elif len(df_1000) == 1:
                latest_ratio = safe_float(df_1000[-1]['percent'])
                large_holder_str = f"{latest_ratio:.2f} %"
            else:
                large_holder_str = "N/A"
        else:
            large_holder_str = "N/A"
    except Exception:
        large_holder_str = "N/A"

    # 6. 月營收
    try:
        df_rev = dl.taiwan_stock_month_revenue(stock_id=stock_id, start_date="2025-01-01")
        if not df_rev.empty:
            rev_val = int(safe_float(df_rev.iloc[-1]['revenue']) // 1000)
            rev_str = f"{rev_val:,} 千元"
        else:
            rev_str = "N/A"
    except Exception:
        rev_str = "N/A"

    # 7. 重點總結
    summary_list = []
    if status == "多頭排列 🚀": summary_list.append("‧ 趨勢多頭，均線呈多頭排列 🚀")
    elif status == "空頭排列 📉": summary_list.append("‧ 趨勢偏弱，均線呈空頭排列 📉")
        
    if sync_days > 0 and f_today > 0: summary_list.append(f"‧ 三大法人已連續同步買超 {sync_days} 天！")
    elif sync_days > 0 and f_today < 0: summary_list.append(f"‧ 三大法人已連續同步賣超 {sync_days} 天！")
        
    if isinstance(bias_5, (int, float)) and bias_5 > 6: summary_list.append(f"‧ 5MA 乖離率達 +{bias_5}%，短線注意追高風險 ⚠️")
        
    summary_text = "\n".join(summary_list) if summary_list else "‧ 短線區間震盪整理中 ⚖️"

    # 8. 組合報告
    report_text = f"""📈 【{stock_id} {stock_name}】綜合健檢報告
🗓️ 資料日期：{data_date}
🏷️ 產業族群：{category}
----------------------------------------
💡 一秒重點總結
{summary_text}

💰 行情與估值
‧ 收盤價：{close_price} 元
‧ 成交量：{volume:,} 張
‧ 本益比 (PE)：{pe_str}
‧ 股價淨值比 (PB)：{pb_str}
‧ 殖利率：{dy_str}

📊 技術面 (均線與指標)
‧ 5MA：{ma5} 元
‧ 20MA：{ma20} 元
‧ 60MA：{ma60} 元
‧ 均線型態：{status}
‧ 5MA 乖離率：{f'{bias_5:+.1f}%' if isinstance(bias_5, (int, float)) else 'N/A'}
‧ KD 指標：{kd_str}

📑 基本面 (月營收)
‧ 單月營收：{rev_str}

🏛️ 三大法人買賣超 (最新日)
‧ 外資：{foreign_str}
‧ 投信：{trust_str}
‧ 自營商：{dealer_str}
‧ 法人合計：{total_str}{sync_status}

👑 千張大戶持股 (最新週)
‧ 持股比例：{large_holder_str}"""

    return report_text

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    if user_msg.isdigit() and len(user_msg) == 4:
        reply_content = get_stock_report(user_msg)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_content))

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
