import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from flask import Flask, request, abort
from FinMind.data import DataLoader

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 設定 LINE Bot 金鑰
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', 'YOUR_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', 'YOUR_CHANNEL_SECRET')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 輔助函式：計算連續買賣超天數
def get_consecutive_days(series):
    if series.empty:
        return 0, "無資料"
    last_val = series.iloc[-1]
    if last_val == 0:
        return 0, "無買賣"
    
    is_buy = last_val > 0
    count = 0
    for val in reversed(series):
        if (is_buy and val > 0) or (not is_buy and val < 0):
            count += 1
        else:
            break
    action = "連買" if is_buy else "連賣"
    return count, action

# 輔助函式：計算 KD 指標
def calculate_kd(df, n=9):
    try:
        low_list = df['low'].rolling(window=n, min_periods=n).min()
        high_list = df['high'].rolling(window=n, min_periods=n).max()
        rsv = (df['close'] - low_list) / (high_list - low_list) * 100
        
        k = [50.0]
        d = [50.0]
        for r in rsv[n-1:]:
            if pd.isna(r):
                r = 50.0
            k_val = (2/3) * k[-1] + (1/3) * r
            d_val = (2/3) * d[-1] + (1/3) * k_val
            k.append(k_val)
            d.append(d_val)
        
        return round(k[-1], 1), round(d[-1], 1)
    except Exception:
        return "N/A", "N/A"

def get_stock_report(stock_id):
    dl = DataLoader()
    start_date = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
    
    # 1. 基本資訊（名稱、產業）
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

    # 2. 日K線價格、均線、乖離率、KD計算
    try:
        df_price = dl.taiwan_stock_daily(stock_id=stock_id, start_date=start_date)
        if not df_price.empty:
            data_date = df_price.iloc[-1]['date']
            close_price = df_price.iloc[-1]['close']
            volume = df_price.iloc[-1]['Trading_Volume'] // 1000
            
            # 均線
            df_price['5MA'] = df_price['close'].rolling(5).mean()
            df_price['20MA'] = df_price['close'].rolling(20).mean()
            df_price['60MA'] = df_price['close'].rolling(60).mean()
            
            ma5_val = df_price.iloc[-1]['5MA']
            ma20_val = df_price.iloc[-1]['20MA']
            ma60_val = df_price.iloc[-1]['60MA']
            
            ma5 = round(ma5_val, 1) if pd.notnull(ma5_val) else "N/A"
            ma20 = round(ma20_val, 1) if pd.notnull(ma20_val) else "N/A"
            ma60 = round(ma60_val, 1) if pd.notnull(ma60_val) else "N/A"
            
            # 5MA 乖離率
            bias_5 = round(((close_price - ma5_val) / ma5_val) * 100, 1) if pd.notnull(ma5_val) else "N/A"
            
            # 均線型態
            if ma5 != "N/A" and ma20 != "N/A" and ma60 != "N/A":
                if close_price > ma5_val > ma20_val > ma60_val:
                    status = "多頭排列 🚀"
                elif close_price < ma5_val < ma20_val < ma60_val:
                    status = "空頭排列 📉"
                else:
                    status = "均線震盪中 ⚖️"
            else:
                status = "資料不足 ⚖️"
                
            # KD 計算
            k_val, d_val = calculate_kd(df_price)
            kd_str = f"K {k_val} / D {d_val}"
            if k_val != "N/A":
                if k_val > 80:
                    kd_str += " (超買區 🔥)"
                elif k_val < 20:
                    kd_str += " (超賣區 ❄️)"
        else:
            data_date, close_price, volume = datetime.now().strftime("%Y-%m-%d"), "N/A", "N/A"
            ma5, ma20, ma60, status, bias_5, kd_str = "N/A", "N/A", "N/A", "資料不足", "N/A", "N/A"
    except Exception:
        data_date, close_price, volume = datetime.now().strftime("%Y-%m-%d"), "N/A", "N/A"
        ma5, ma20, ma60, status, bias_5, kd_str = "N/A", "N/A", "N/A", "資料不足", "N/A", "N/A"

    # 3. 本益比 / 殖利率 / 股價淨值比
    try:
        df_per = dl.taiwan_stock_per_pbr(stock_id=stock_id, start_date=start_date)
        if not df_per.empty:
            pe = df_per.iloc[-1].get('PER', 'N/A')
            pb = df_per.iloc[-1].get('PBR', 'N/A')
            dy = df_per.iloc[-1].get('dividend_yield', 'N/A')
            
            pe_str = f"{round(pe, 1)} 倍" if pd.notnull(pe) and pe != 0 else "N/A"
            pb_str = f"{round(pb, 1)} 倍" if pd.notnull(pb) and pb != 0 else "N/A"
            dy_str = f"{round(dy, 2)} %" if pd.notnull(dy) and dy != 0 else "N/A"
        else:
            pe_str, pb_str, dy_str = "N/A", "N/A", "N/A"
    except Exception:
        pe_str, pb_str, dy_str = "N/A", "N/A", "N/A"

    # 4. 三大法人與籌碼狀態
    sync_status = ""
    sync_days = 0
    try:
        df_chip = dl.taiwan_stock_institutional_investors(stock_id=stock_id, start_date=start_date)
        if not df_chip.empty:
            df_chip['net_buy'] = (df_chip['buy'] - df_chip['sell']) // 1000
            
            foreign_df = df_chip[df_chip['name'].str.contains('Foreign')].groupby('date')['net_buy'].sum()
            trust_df = df_chip[df_chip['name'].str.contains('Investment_Trust')].groupby('date')['net_buy'].sum()
            dealer_df = df_chip[df_chip['name'].str.contains('Dealer')].groupby('date')['net_buy'].sum()
            total_df = foreign_df.add(trust_df, fill_value=0).add(dealer_df, fill_value=0)
            
            foreign_today = int(foreign_df.iloc[-1]) if not foreign_df.empty else 0
            trust_today = int(trust_df.iloc[-1]) if not trust_df.empty else 0
            dealer_today = int(dealer_df.iloc[-1]) if not dealer_df.empty else 0
            total_today = int(total_df.iloc[-1]) if not total_df.empty else 0
            
            f_cnt, f_act = get_consecutive_days(foreign_df)
            t_cnt, t_act = get_consecutive_days(trust_df)
            d_cnt, d_act = get_consecutive_days(dealer_df)
            
            foreign_str = f"{foreign_today:+d} 張 ({f_act} {f_cnt} 天)"
            trust_str = f"{trust_today:+d} 張 ({t_act} {t_cnt} 天)"
            dealer_str = f"{dealer_today:+d} 張 ({d_act} {d_cnt} 天)"
            total_str = f"{total_today:+d} 張"
            
            if foreign_today > 0 and trust_today > 0 and dealer_today > 0:
                for f, t, d in zip(reversed(foreign_df), reversed(trust_df), reversed(dealer_df)):
                    if f > 0 and t > 0 and d > 0:
                        sync_days += 1
                    else:
                        break
                sync_status = f"\n🔥 法人籌碼：三大法人同步買超 {sync_days} 天 🚀"
            elif foreign_today < 0 and trust_today < 0 and dealer_today < 0:
                for f, t, d in zip(reversed(foreign_df), reversed(trust_df), reversed(dealer_df)):
                    if f < 0 and t < 0 and d < 0:
                        sync_days += 1
                    else:
                        break
                sync_status = f"\n⚠️ 法人籌碼：三大法人同步賣超 {sync_days} 天 📉"
        else:
            foreign_str, trust_str, dealer_str, total_str = "N/A", "N/A", "N/A", "N/A"
    except Exception:
        foreign_str, trust_str, dealer_str, total_str = "N/A", "N/A", "N/A", "N/A"

    # 5. 千張大戶持股比
    try:
        df_large = dl.taiwan_stock_holding_shares_per(stock_id=stock_id, start_date=start_date)
        if not df_large.empty:
            df_1000 = df_large[df_large['HoldingSharesLevel'] == '15']
            if len(df_1000) >= 2:
                latest_ratio = df_1000.iloc[-1]['percent']
                prev_ratio = df_1000.iloc[-2]['percent']
                diff = round(latest_ratio - prev_ratio, 2)
                diff_str = f" (較上週 {diff:+.2f}%)"
            elif len(df_1000) == 1:
                latest_ratio = df_1000.iloc[-1]['percent']
                diff_str = ""
            else:
                latest_ratio, diff_str = "N/A", ""
            large_holder_str = f"{latest_ratio:.2f} %{diff_str}" if isinstance(latest_ratio, (int, float)) else "N/A"
        else:
            large_holder_str = "N/A"
    except Exception:
        large_holder_str = "N/A"

    # 6. 月營收
    try:
        df_rev = dl.taiwan_stock_month_revenue(stock_id=stock_id, start_date="2025-01-01")
        if not df_rev.empty:
            rev_val = df_rev.iloc[-1]['revenue'] // 1000
            rev_str = f"{rev_val:,} 千元"
        else:
            rev_str = "N/A"
    except Exception:
        rev_str = "N/A"

    # 7. 智慧重點總結
    summary_list = []
    if status == "多頭排列 🚀":
        summary_list.append("‧ 趨勢多頭，均線呈多頭排列 🚀")
    elif status == "空頭排列 📉":
        summary_list.append("‧ 趨勢偏弱，均線呈空頭排列 📉")
        
    if sync_days > 0 and foreign_today > 0:
        summary_list.append(f"‧ 三大法人已連續同步買超 {sync_days} 天，籌碼偏多！")
    elif sync_days > 0 and foreign_today < 0:
        summary_list.append(f"‧ 三大法人已連續同步賣超 {sync_days} 天，注意調節風險！")
        
    if isinstance(bias_5, (int, float)) and bias_5 > 6:
        summary_list.append(f"‧ 5MA 乖離率達 +{bias_5}%，短線留意回檔風險 ⚠️")
        
    summary_text = "\n".join(summary_list) if summary_list else "‧ 短線區間震盪整理中 ⚖️"

    # 8. 組合最終報告
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
123
