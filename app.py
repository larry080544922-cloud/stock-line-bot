import os
import pandas as pd
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
            stock_name = "未知"
            category = "其他 / 待確定"
    except Exception:
        stock_name = "股票"
        category = "其他 / 待確定"

    # 2. 日K線價格與均線計算 (5MA, 20MA, 60MA)
    try:
        df_price = dl.taiwan_stock_daily(stock_id=stock_id, start_date=start_date)
        if not df_price.empty:
            data_date = df_price.iloc[-1]['date']
            close_price = df_price.iloc[-1]['close']
            volume = df_price.iloc[-1]['Trading_Volume'] // 1000
            
            # 計算均線
            df_price['5MA'] = df_price['close'].rolling(5).mean()
            df_price['20MA'] = df_price['close'].rolling(20).mean()
            df_price['60MA'] = df_price['close'].rolling(60).mean()
            
            ma5 = round(df_price.iloc[-1]['5MA'], 1) if pd.notnull(df_price.iloc[-1]['5MA']) else "N/A"
            ma20 = round(df_price.iloc[-1]['20MA'], 1) if pd.notnull(df_price.iloc[-1]['20MA']) else "N/A"
            ma60 = round(df_price.iloc[-1]['60MA'], 1) if pd.notnull(df_price.iloc[-1]['60MA']) else "N/A"
            
            # 判斷均線狀態
            if ma5 != "N/A" and ma20 != "N/A" and ma60 != "N/A":
                if close_price > ma5 > ma20 > ma60:
                    status = "多頭排列 🚀"
                elif close_price < ma5 < ma20 < ma60:
                    status = "空頭排列 📉"
                else:
                    status = "均線震盪中 ⚖️"
            else:
                status = "資料不足 ⚖️"
        else:
            data_date, close_price, volume = datetime.now().strftime("%Y-%m-%d"), "N/A", "N/A"
            ma5, ma20, ma60, status = "N/A", "N/A", "N/A", "資料不足"
    except Exception:
        data_date, close_price, volume = datetime.now().strftime("%Y-%m-%d"), "N/A", "N/A"
        ma5, ma20, ma60, status = "N/A", "N/A", "N/A", "資料不足"

    # 3. 三大法人買賣超 (最新日)
    try:
        df_chip = dl.taiwan_stock_institutional_investors(stock_id=stock_id, start_date=start_date)
        if not df_chip.empty:
            latest_chip_date = df_chip.iloc[-1]['date']
            df_latest_chip = df_chip[df_chip['date'] == latest_chip_date]
            
            foreign = df_latest_chip[df_latest_chip['name'].str.contains('Foreign')]['buy'].sum() - df_latest_chip[df_latest_chip['name'].str.contains('Foreign')]['sell'].sum()
            trust = df_latest_chip[df_latest_chip['name'].str.contains('Investment_Trust')]['buy'].sum() - df_latest_chip[df_latest_chip['name'].str.contains('Investment_Trust')]['sell'].sum()
            dealer = df_latest_chip[df_latest_chip['name'].str.contains('Dealer')]['buy'].sum() - df_latest_chip[df_latest_chip['name'].str.contains('Dealer')]['sell'].sum()
            
            foreign_str = f"{foreign // 1000:+d}"
            trust_str = f"{trust // 1000:+d}"
            dealer_str = f"{dealer // 1000:+d}"
            total_str = f"{(foreign + trust + dealer) // 1000:+d}"
        else:
            foreign_str, trust_str, dealer_str, total_str = "N/A", "N/A", "N/A", "N/A"
    except Exception:
        foreign_str, trust_str, dealer_str, total_str = "N/A", "N/A", "N/A", "N/A"

    # 4. 月營收
    try:
        df_rev = dl.taiwan_stock_month_revenue(stock_id=stock_id, start_date="2025-01-01")
        if not df_rev.empty:
            rev_val = df_rev.iloc[-1]['revenue'] // 1000  # 轉千元或百萬
            rev_str = f"{rev_val:,} 千元"
        else:
            rev_str = "N/A"
    except Exception:
        rev_str = "N/A"

    # 5. 組合發送報告
    report_text = f"""📈 【{stock_id} {stock_name}】綜合分析
🗓️ 資料日期：{data_date}
🏷️ 產業族群：{category}
----------------------------------------
💰 行情與估值
‧ 收盤價：{close_price} 元
‧ 成交量：{volume:,} 張

📊 技術面 (均線位置)
‧ 5MA：{ma5} 元
‧ 20MA：{ma20} 元
‧ 60MA：{ma60} 元
‧ 狀態：{status}

📑 基本面 (月營收)
‧ 單月營收：{rev_str}

🏛️ 三大法人買賣超 (最新日)
‧ 外資：{foreign_str} 張
‧ 投信：{trust_str} 張
‧ 自營商：{dealer_str} 張
‧ 法人合計：{total_str} 張"""

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
