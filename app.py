import os
from datetime import datetime
from flask import Flask, request, abort
from FinMind.data import DataLoader

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# 1. 建立 Flask 實體（Render 必須靠這個 app 變數啟動）
app = Flask(__name__)

# 2. 設定 LINE Bot 金鑰（建議在 Render 環境變數設定，也可直接填入字串）
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', 'YOUR_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', 'YOUR_CHANNEL_SECRET')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 3. 核心功能：查詢股票並組裝文字報告
def get_stock_report(stock_id):
    dl = DataLoader()
    
    # (A) 抓取股票基本資訊 (名稱與產業類別)
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

    # (B) 抓取最新日K線行情 (取得最新資料日期與價格)
    try:
        df_price = dl.taiwan_stock_daily(stock_id=stock_id, start_date="2026-01-01")
        if not df_price.empty:
            data_date = df_price.iloc[-1]['date']
            close_price = df_price.iloc[-1]['close']
            volume = df_price.iloc[-1]['Trading_Volume'] // 1000
        else:
            data_date = datetime.now().strftime("%Y-%m-%d")
            close_price = "N/A"
            volume = "N/A"
    except Exception:
        data_date = datetime.now().strftime("%Y-%m-%d")
        close_price = "N/A"
        volume = "N/A"

    # (C) 組合要發送給 LINE 的文字訊息
    report_text = f"""📈 【{stock_id} {stock_name}】綜合分析
🗓️ 資料日期：{data_date}
🏷️ 產業族群：{category}
----------------------------------------
💰 行情與估值
‧ 收盤價：{close_price} 元
‧ 成交量：{volume} 張
‧ 本益比：N/A 倍

📊 技術面 (均線位置)
‧ 狀態：資料更新中 ⚖️

📑 基本面 (月營收)
‧ 單月營收：N/A

🏛️ 三大法人買賣超 (最新日)
‧ 法人合計：N/A 張

👑 千張大戶持股比 (最新週)
‧ 持股比例：N/A"""

    return report_text

# 4. LINE Webhook 接收點
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'

# 5. 處理文字訊息（當使用者傳送股票代號時）
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    
    # 判斷使用者是否輸入 4 位數股票代號
    if user_msg.isdigit() and len(user_msg) == 4:
        reply_content = get_stock_report(user_msg)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_content)
        )

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
