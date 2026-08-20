import os
import requests
import datetime
import pandas as pd
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from FinMind.data import DataLoader

app = Flask(__name__)

# 從環境變數讀取安全金鑰
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

def get_stock_analysis(stock_id):
    dl = DataLoader()
    today = datetime.datetime.now()
    start_date = (today - datetime.timedelta(days=120)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    # 1. 股票名稱
    try:
        df_info = dl.taiwan_stock_info()
        stock_match = df_info[df_info['stock_id'] == stock_id]
        if stock_match.empty:
            return f"❌ 找不到股票代號 [{stock_id}]，請確認輸入是否正確。"
        stock_name = stock_match.iloc[0]['stock_name']
    except Exception:
        return "❌ 系統讀取股票清單失敗，請稍後再試。"

    # 2. 日 K 線與均線
    df_k = dl.taiwan_stock_daily(stock_id=stock_id, start_date=start_date, end_date=end_date)
    if df_k.empty or len(df_k) < 60:
        return f"⚠️ 股票 {stock_id} {stock_name} 的歷史資料不足，無法計算均線。"

    df_k['MA5'] = df_k['close'].rolling(5).mean()
    df_k['MA20'] = df_k['close'].rolling(20).mean()
    df_k['MA60'] = df_k['close'].rolling(60).mean()
    
    latest_k = df_k.iloc[-1]
    close_p = latest_k['close']
    ma5_p = round(latest_k['MA5'], 1)
    ma20_p = round(latest_k['MA20'], 1)
    ma60_p = round(latest_k['MA60'], 1)
    vol_k = int(latest_k['Trading_Volume'] // 1000)

    ma_status = "全均線站上 🚀" if (close_p > ma5_p and close_p > ma20_p and close_p > ma60_p) else "均線震盪中 ⚖️"

    # 3. 本益比
    try:
        df_per = dl.taiwan_stock_per_pbr(stock_id=stock_id, start_date=(today - datetime.timedelta(days=10)).strftime("%Y-%m-%d"))
        per_val = round(df_per.iloc[-1]['PER'], 1) if not df_per.empty else "N/A"
    except Exception:
        per_val = "N/A"

    # 4. 月營收
    try:
        df_rev = dl.taiwan_stock_month_revenue(stock_id=stock_id, start_date=(today - datetime.timedelta(days=120)).strftime("%Y-%m-%d"))
        if not df_rev.empty:
            rev_latest = df_rev.iloc[-1]
            rev_val = int(rev_latest['revenue'] // 1000)
            rev_yoy = round(rev_latest.get('revenue_year_growth_proportion', 0), 1)
            rev_str = f"{rev_val:,} 百萬元 (YoY: {rev_yoy}%)"
        else:
            rev_str = "資料更新中"
    except Exception:
        rev_str = "N/A"

    # 5. 三大法人買賣超
    try:
        df_inst = dl.taiwan_stock_institutional_investors(stock_id=stock_id, start_date=(today - datetime.timedelta(days=10)).strftime("%Y-%m-%d"))
        if not df_inst.empty:
            latest_date = df_inst['date'].max()
            df_latest_inst = df_inst[df_inst['date'] == latest_date]
            
            foreign = int(df_latest_inst[df_latest_inst['name']=='Foreign_Investor']['buy'].sum() - df_latest_inst[df_latest_inst['name']=='Foreign_Investor']['sell'].sum()) // 1000
            sitc = int(df_latest_inst[df_latest_inst['name']=='Investment_Trust']['buy'].sum() - df_latest_inst[df_latest_inst['name']=='Investment_Trust']['sell'].sum()) // 1000
            dealer = int(df_latest_inst[df_latest_inst['name']=='Dealer_Self']['buy'].sum() - df_latest_inst[df_latest_inst['name']=='Dealer_Self']['sell'].sum()) // 1000
            total_inst = foreign + sitc + dealer
            
            inst_str = (
                f"‧ 外資：{foreign:+d} 張\n"
                f"‧ 投信：{sitc:+d} 張\n"
                f"‧ 自營商：{dealer:+d} 張\n"
                f"‧ 法人合計：{total_inst:+d} 張"
            )
        else:
            inst_str = "無最新法人數據"
    except Exception:
        inst_str = "N/A"

    # 6. 千張大戶持股比
    try:
        df_share = dl.taiwan_stock_shareholding(stock_id=stock_id, start_date=(today - datetime.timedelta(days=30)).strftime("%Y-%m-%d"))
        df_1000 = df_share[df_share['HoldingSharesLevel'] == 15].sort_values('date', ascending=False)
        if len(df_1000) >= 2:
            w0_ratio = df_1000.iloc[0]['percent']
            w1_ratio = df_1000.iloc[1]['percent']
            diff = round(w0_ratio - w1_ratio, 2)
            large_str = f"{round(w0_ratio, 2)}% (較上週 {diff:+.2f}%)"
        elif not df_1000.empty:
            large_str = f"{round(df_1000.iloc[0]['percent'], 2)}%"
        else:
            large_str = "N/A"
    except Exception:
        large_str = "N/A"

    # 7. 組裝訊息
    report = (
        f"📈 【{stock_id} {stock_name}】綜合分析\n"
        f"----------------------------------------\n"
        f"💰 行情與估值\n"
        f"‧ 收盤價：{close_p} 元\n"
        f"‧ 成交量：{vol_k:,} 張\n"
        f"‧ 本益比：{per_val} 倍\n\n"
        f"📊 技術面 (均線位置)\n"
        f"‧ 5MA：{ma5_p} 元\n"
        f"‧ 20MA：{ma20_p} 元\n"
        f"‧ 60MA：{ma60_p} 元\n"
        f"‧ 狀態：{ma_status}\n\n"
        f"📑 基本面 (月營收)\n"
        f"‧ 單月營收：{rev_str}\n\n"
        f"🏛️ 三大法人買賣超 (最新日)\n"
        f"{inst_str}\n\n"
        f"👑 千張大戶持股比 (最新週)\n"
        f"‧ 持股比例：{large_str}"
    )
    return report

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    if user_msg.isdigit() and len(user_msg) == 4:
        reply_text = get_stock_analysis(user_msg)
    else:
        reply_text = "請輸入 4 位數台股股票代號 (例如：2330、2454)，小幫手將為您提供完整的個股分析報告！"
        
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
