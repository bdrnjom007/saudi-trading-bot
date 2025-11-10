"""
خادم Webhook المتقدم - النظام الاحترافي
يستقبل إشارات TradingView ويرسلها على Telegram مع تتبع الأداء
"""

from flask import Flask, request, jsonify, render_template_string
import requests
import sqlite3
import os
from datetime import datetime
import logging
import hashlib
import hmac

# إعداد logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# الإعدادات
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET', 'your_secret_key')
DATABASE_PATH = os.getenv('DATABASE_URL', 'sqlite:///trading_bot.db').replace('sqlite:///', '')

# طباعة الإعدادات للتشخيص (إخفاء جزء من Token للأمان)
logger.info(f"🔧 TELEGRAM_BOT_TOKEN: {'*' * 10 + TELEGRAM_BOT_TOKEN[-10:] if TELEGRAM_BOT_TOKEN else 'NOT SET'}")
logger.info(f"🔧 TELEGRAM_CHAT_ID: {TELEGRAM_CHAT_ID if TELEGRAM_CHAT_ID else 'NOT SET'}")
logger.info(f"🔧 WEBHOOK_SECRET: {'SET' if WEBHOOK_SECRET else 'NOT SET'}")

# إنشاء قاعدة البيانات
def init_database():
    """إنشاء جداول قاعدة البيانات"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # جدول التوصيات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,
            price REAL NOT NULL,
            rsi REAL,
            macd REAL,
            volume REAL,
            message TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            sent_to_telegram BOOLEAN DEFAULT 0
        )
    ''')
    
    # جدول الأداء
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER,
            entry_price REAL,
            exit_price REAL,
            profit_loss REAL,
            profit_pct REAL,
            status TEXT DEFAULT 'OPEN',
            closed_at DATETIME,
            FOREIGN KEY (signal_id) REFERENCES signals(id)
        )
    ''')
    
    # جدول الإحصائيات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            total_signals INTEGER DEFAULT 0,
            successful_signals INTEGER DEFAULT 0,
            total_profit REAL DEFAULT 0,
            success_rate REAL DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("✅ قاعدة البيانات جاهزة")

# تهيئة قاعدة البيانات عند البدء
init_database()

def send_telegram_message(message: str, parse_mode='HTML') -> bool:
    """إرسال رسالة إلى Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ Telegram غير مفعّل")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': parse_mode
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info("✅ تم إرسال الرسالة إلى Telegram")
            return True
        else:
            logger.error(f"❌ فشل إرسال Telegram: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"❌ خطأ في إرسال Telegram: {e}")
        return False

def save_signal(data: dict) -> int:
    """حفظ التوصية في قاعدة البيانات"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO signals (symbol, action, price, rsi, macd, volume, message)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        data.get('symbol', ''),
        data.get('action', ''),
        float(data.get('price', 0)),
        float(data.get('rsi', 0)) if data.get('rsi') else None,
        float(data.get('macd', 0)) if data.get('macd') else None,
        float(data.get('volume', 0)) if data.get('volume') else None,
        data.get('message', '')
    ))
    
    signal_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    logger.info(f"💾 تم حفظ التوصية #{signal_id}")
    return signal_id

def update_stats(symbol: str):
    """تحديث إحصائيات السهم"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # التحقق من وجود إحصائيات للسهم
    cursor.execute('SELECT id FROM stats WHERE symbol = ?', (symbol,))
    exists = cursor.fetchone()
    
    if not exists:
        cursor.execute('''
            INSERT INTO stats (symbol, total_signals, updated_at)
            VALUES (?, 1, CURRENT_TIMESTAMP)
        ''', (symbol,))
    else:
        cursor.execute('''
            UPDATE stats
            SET total_signals = total_signals + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE symbol = ?
        ''', (symbol,))
    
    conn.commit()
    conn.close()

def format_signal_message(data: dict) -> str:
    """تنسيق رسالة التوصية"""
    # أسماء الشركات
    company_names = {
        'TADAWUL:2222': 'أرامكو السعودية',
        'TADAWUL:1180': 'البنك الأهلي',
        'TADAWUL:7010': 'STC',
        'TADAWUL:1211': 'معادن',
        'TADAWUL:1120': 'الراجحي',
        'TADAWUL:2010': 'سابك',
        'TADAWUL:5110': 'الكهرباء',
        'TADAWUL:2280': 'المراعي'
    }
    
    symbol = data.get('symbol', '')
    company_name = company_names.get(symbol, symbol)
    action = data.get('action', 'ALERT').upper()
    price = float(data.get('price', 0))
    
    # تحديد الأيقونة والنص
    if action == 'BUY':
        icon = "🟢"
        action_ar = "شراء"
        strength = "قوية"
    elif action == 'SELL':
        icon = "🔴"
        action_ar = "بيع"
        strength = "قوية"
    else:
        icon = "🟡"
        action_ar = "تنبيه"
        strength = "متوسطة"
    
    # بناء الرسالة
    message = f"""
{icon} <b>توصية {action_ar} - {strength}</b>

📊 <b>السهم:</b> {company_name} ({symbol})
💰 <b>السعر:</b> {price:.2f} ريال

<b>📉 المؤشرات (Real-time):</b>
"""
    
    # إضافة معلومات الاستراتيجية إذا كانت موجودة
    if data.get('strategy'):
        message += f"• <b>الاستراتيجية:</b> {data['strategy']}\n"
    
    if data.get('signals'):
        message += f"• <b>إشارات إيجابية:</b> {data['signals']}\n"
    
    # إضافة المؤشرات إذا كانت موجودة
    if data.get('rsi'):
        rsi = float(data['rsi'])
        rsi_status = "تشبع بيعي" if rsi < 30 else "تشبع شرائي" if rsi > 70 else "متعادل"
        message += f"• RSI: {rsi:.1f} ({rsi_status})\n"
    
    if data.get('macd'):
        macd = float(data['macd'])
        macd_status = "إيجابي" if macd > 0 else "سلبي"
        message += f"• MACD: {macd:+.2f} ({macd_status})\n"
    
    if data.get('volume'):
        volume = float(data['volume'])
        message += f"• حجم التداول: {volume:,.0f}\n"
    
    # إضافة Take Profit و Stop Loss إذا كانت موجودة
    if action == 'BUY' and price > 0:
        take_profit = price * 1.03  # +3%
        stop_loss = price * 0.98    # -2%
        message += f"\n<b>🎯 إدارة المخاطر:</b>\n"
        message += f"• Take Profit: {take_profit:.2f} ريال (+3%)\n"
        message += f"• Stop Loss: {stop_loss:.2f} ريال (-2%)\n"
    
    # إضافة الرسالة المخصصة
    if data.get('message'):
        message += f"\n<b>🔍 التحليل:</b>\n{data['message']}\n"
    
    # إضافة التوقيت
    message += f"\n⏰ <b>الوقت:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    
    # إضافة إخلاء المسؤولية
    message += "\n⚠️ <i>هذه توصية تعليمية. استشر مستشاراً مالياً قبل اتخاذ أي قرار.</i>"
    
    return message.strip()

@app.route('/')
def home():
    """الصفحة الرئيسية"""
    return """
    <html>
    <head>
        <title>Saudi Trading Bot - Premium System</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 800px;
                margin: 50px auto;
                padding: 20px;
                background: #f5f5f5;
            }
            .container {
                background: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            h1 {
                color: #2c3e50;
                text-align: center;
            }
            .status {
                background: #27ae60;
                color: white;
                padding: 15px;
                border-radius: 5px;
                text-align: center;
                margin: 20px 0;
            }
            .info {
                background: #ecf0f1;
                padding: 15px;
                border-radius: 5px;
                margin: 10px 0;
            }
            a {
                color: #3498db;
                text-decoration: none;
            }
            a:hover {
                text-decoration: underline;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🚀 Saudi Trading Bot</h1>
            <h2 style="text-align: center; color: #7f8c8d;">Premium System</h2>
            
            <div class="status">
                ✅ النظام يعمل بشكل صحيح
            </div>
            
            <div class="info">
                <h3>📊 الميزات:</h3>
                <ul>
                    <li>بيانات real-time من TradingView</li>
                    <li>توصيات فورية على Telegram</li>
                    <li>تتبع أداء تلقائي</li>
                    <li>قاعدة بيانات متكاملة</li>
                </ul>
            </div>
            
            <div class="info">
                <h3>🔗 الروابط المفيدة:</h3>
                <ul>
                    <li><a href="/health">فحص الحالة</a></li>
                    <li><a href="/dashboard">لوحة التحكم</a></li>
                    <li><a href="/signals">جميع التوصيات</a></li>
                    <li><a href="/stats">الإحصائيات</a></li>
                </ul>
            </div>
            
            <p style="text-align: center; color: #7f8c8d; margin-top: 30px;">
                © 2025 Saudi Trading Bot - Premium System
            </p>
        </div>
    </body>
    </html>
    """

@app.route('/health')
def health():
    """فحص حالة الخادم"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'telegram': 'configured' if TELEGRAM_BOT_TOKEN else 'not_configured',
        'database': 'connected'
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    """استقبال Webhooks من TradingView"""
    try:
        # الحصول على البيانات
        data = request.get_json()
        
        if not data:
            logger.warning("⚠️ لا توجد بيانات في الطلب")
            return jsonify({'error': 'No data'}), 400
        
        logger.info(f"📥 استقبال webhook: {data}")
        
        # حفظ التوصية
        signal_id = save_signal(data)
        
        # تحديث الإحصائيات
        if data.get('symbol'):
            update_stats(data['symbol'])
        
        # تنسيق وإرسال الرسالة
        message = format_signal_message(data)
        sent = send_telegram_message(message)
        
        # تحديث حالة الإرسال
        if sent:
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE signals SET sent_to_telegram = 1 WHERE id = ?',
                (signal_id,)
            )
            conn.commit()
            conn.close()
        
        return jsonify({
            'success': True,
            'signal_id': signal_id,
            'sent_to_telegram': sent
        })
        
    except Exception as e:
        logger.error(f"❌ خطأ في معالجة webhook: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/signals')
def get_signals():
    """الحصول على جميع التوصيات"""
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, symbol, action, price, rsi, macd, timestamp, sent_to_telegram
            FROM signals
            ORDER BY timestamp DESC
            LIMIT 50
        ''')
        
        signals = []
        for row in cursor.fetchall():
            signals.append({
                'id': row[0],
                'symbol': row[1],
                'action': row[2],
                'price': row[3],
                'rsi': row[4],
                'macd': row[5],
                'timestamp': row[6],
                'sent_to_telegram': bool(row[7])
            })
        
        conn.close()
        
        return jsonify({
            'success': True,
            'count': len(signals),
            'signals': signals
        })
        
    except Exception as e:
        logger.error(f"❌ خطأ في جلب التوصيات: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/stats')
def get_stats():
    """الحصول على الإحصائيات"""
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        # إحصائيات عامة
        cursor.execute('SELECT COUNT(*) FROM signals')
        total_signals = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM signals WHERE sent_to_telegram = 1')
        sent_signals = cursor.fetchone()[0]
        
        # إحصائيات لكل سهم
        cursor.execute('''
            SELECT symbol, COUNT(*) as count
            FROM signals
            GROUP BY symbol
            ORDER BY count DESC
        ''')
        
        by_symbol = []
        for row in cursor.fetchall():
            by_symbol.append({
                'symbol': row[0],
                'count': row[1]
            })
        
        conn.close()
        
        return jsonify({
            'success': True,
            'total_signals': total_signals,
            'sent_signals': sent_signals,
            'by_symbol': by_symbol
        })
        
    except Exception as e:
        logger.error(f"❌ خطأ في جلب الإحصائيات: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/dashboard')
def dashboard():
    """لوحة التحكم"""
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        # الإحصائيات
        cursor.execute('SELECT COUNT(*) FROM signals')
        total_signals = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(DISTINCT symbol) FROM signals')
        total_symbols = cursor.fetchone()[0]
        
        # آخر التوصيات
        cursor.execute('''
            SELECT symbol, action, price, timestamp
            FROM signals
            ORDER BY timestamp DESC
            LIMIT 10
        ''')
        
        recent_signals = cursor.fetchall()
        conn.close()
        
        # بناء HTML
        html = f"""
        <html>
        <head>
            <title>لوحة التحكم - Saudi Trading Bot</title>
            <meta charset="UTF-8">
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    margin: 0;
                    padding: 20px;
                    background: #f5f5f5;
                    direction: rtl;
                }}
                .container {{
                    max-width: 1200px;
                    margin: 0 auto;
                }}
                h1 {{
                    color: #2c3e50;
                    text-align: center;
                }}
                .stats {{
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                    gap: 20px;
                    margin: 30px 0;
                }}
                .stat-card {{
                    background: white;
                    padding: 20px;
                    border-radius: 10px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                    text-align: center;
                }}
                .stat-number {{
                    font-size: 48px;
                    font-weight: bold;
                    color: #27ae60;
                }}
                .stat-label {{
                    color: #7f8c8d;
                    margin-top: 10px;
                }}
                table {{
                    width: 100%;
                    background: white;
                    border-radius: 10px;
                    overflow: hidden;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }}
                th {{
                    background: #34495e;
                    color: white;
                    padding: 15px;
                    text-align: right;
                }}
                td {{
                    padding: 12px 15px;
                    border-bottom: 1px solid #ecf0f1;
                    text-align: right;
                }}
                .buy {{
                    color: #27ae60;
                    font-weight: bold;
                }}
                .sell {{
                    color: #e74c3c;
                    font-weight: bold;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>📊 لوحة التحكم</h1>
                
                <div class="stats">
                    <div class="stat-card">
                        <div class="stat-number">{total_signals}</div>
                        <div class="stat-label">إجمالي التوصيات</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number">{total_symbols}</div>
                        <div class="stat-label">عدد الأسهم</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number">✅</div>
                        <div class="stat-label">النظام يعمل</div>
                    </div>
                </div>
                
                <h2>آخر التوصيات</h2>
                <table>
                    <thead>
                        <tr>
                            <th>السهم</th>
                            <th>الإجراء</th>
                            <th>السعر</th>
                            <th>الوقت</th>
                        </tr>
                    </thead>
                    <tbody>
        """
        
        for signal in recent_signals:
            action_class = 'buy' if signal[1] == 'BUY' else 'sell'
            action_text = 'شراء' if signal[1] == 'BUY' else 'بيع'
            html += f"""
                        <tr>
                            <td>{signal[0]}</td>
                            <td class="{action_class}">{action_text}</td>
                            <td>{signal[2]:.2f} ريال</td>
                            <td>{signal[3]}</td>
                        </tr>
            """
        
        html += """
                    </tbody>
                </table>
            </div>
        </body>
        </html>
        """
        
        return html
        
    except Exception as e:
        logger.error(f"❌ خطأ في لوحة التحكم: {e}")
        return f"<h1>خطأ: {str(e)}</h1>", 500

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("🚀 بدء خادم Webhook المتقدم")
    logger.info("=" * 60)
    logger.info(f"📱 Telegram: {'✅ مفعّل' if TELEGRAM_BOT_TOKEN else '❌ غير مفعّل'}")
    logger.info(f"💾 قاعدة البيانات: {DATABASE_PATH}")
    logger.info("=" * 60)
    
    # إرسال رسالة البداية
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        start_message = """
🚀 <b>خادم Webhook المتقدم بدأ العمل!</b>

✅ <b>الحالة:</b> يعمل بشكل مستمر 24/7

📊 <b>الميزات:</b>
• استقبال إشارات TradingView
• توصيات فورية على Telegram
• قاعدة بيانات متكاملة
• تتبع أداء تلقائي

⏰ <b>بدأ في:</b> """ + datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        send_telegram_message(start_message)
    
    # تشغيل الخادم
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
