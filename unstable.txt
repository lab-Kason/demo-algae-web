import os
import streamlit as st
import boto3
from boto3.dynamodb.conditions import Key
import pandas as pd
from decimal import Decimal
import json
import time
import requests
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

# ---------- 从 Secrets 读取凭证 ----------
AWS_ACCESS_KEY_ID = st.secrets["AWS_ACCESS_KEY_ID"]
AWS_SECRET_ACCESS_KEY = st.secrets["AWS_SECRET_ACCESS_KEY"]
AWS_DEFAULT_REGION = st.secrets["AWS_DEFAULT_REGION"]

os.environ['AWS_ACCESS_KEY_ID'] = AWS_ACCESS_KEY_ID
os.environ['AWS_SECRET_ACCESS_KEY'] = AWS_SECRET_ACCESS_KEY
os.environ['AWS_DEFAULT_REGION'] = AWS_DEFAULT_REGION

# ---------- AWS 资源 ----------
REGION = AWS_DEFAULT_REGION
SENSOR_TABLE = "TankSensorData"
CHAT_TABLE = "ChatHistory"

dynamodb = boto3.resource('dynamodb', region_name=REGION)
sensor_table = dynamodb.Table(SENSOR_TABLE)
chat_table = dynamodb.Table(CHAT_TABLE)

# ---------- 页面配置 ----------
st.set_page_config(
    page_title="Algae Box Monitor",
    page_icon="🌿",
    layout="wide"
)

# ---------- 页面控制（封面/监控） ----------
page = st.query_params.get("page", "cover")  # 默认为 cover

# ---------- 语言管理 ----------
# 从 URL 参数获取语言，默认为繁体中文
lang = st.query_params.get("lang", "zh")
if lang not in ["zh", "en"]:
    lang = "zh"

# 定义翻译字典（监控页用）
TRANSLATIONS = {
    "zh": {
        "app_title": "🌿 藻類監測系統",
        "tank_label": "當前監控 Tank:",
        "temp": "溫度",
        "ph": "pH",
        "turbidity": "濁度",
        "salinity": "鹽度",
        "temp_unit": "°C",
        "turbidity_unit": "NTU",
        "salinity_unit": "ppt",
        "timestamp_label": "設備運行毫秒數：",
        "no_data_warning": "⚠️ 暫無數據，請檢查表名或設備上報。",
        "history_title": "📈 歷史趨勢",
        "slider_label": "顯示最近多少條記錄",
        "chart_temp": "溫度變化",
        "chart_ph": "pH 變化",
        "chart_turbidity": "濁度變化",
        "insufficient_data": "📭 歷史數據不足（當前 {count} 條），請等待更多採樣點。",
        "ai_title": "🤖 AI 助手 - {tank}",
        "ai_input_placeholder": "輸入你的問題…",
        "ai_thinking": "思考中…",
        "ai_analyzing": "分析數據中…",
        "ai_error": "抱歉，無法生成回答。",
        "clear_memory": "🧹 清空記憶",
        "clear_success": "已清空該 Tank 的所有聊天記憶",
        "switch_tank": "🔄 切換 Tank",
        "auto_refresh_label": "自動刷新數據（每10秒）",
        "no_api_key_warning": "未配置 {tank} 的 DeepSeek API Key，請在 Secrets 中添加 DEEPSEEK_{key}",
    },
    "en": {
        "app_title": "🌿 Algae Monitor",
        "tank_label": "Current Tank:",
        "temp": "Temperature",
        "ph": "pH",
        "turbidity": "Turbidity",
        "salinity": "Salinity",
        "temp_unit": "°C",
        "turbidity_unit": "NTU",
        "salinity_unit": "ppt",
        "timestamp_label": "Device uptime (ms):",
        "no_data_warning": "⚠️ No data available. Please check table name or device reporting.",
        "history_title": "📈 Historical Trends",
        "slider_label": "Number of recent records to display",
        "chart_temp": "Temperature Trend",
        "chart_ph": "pH Trend",
        "chart_turbidity": "Turbidity Trend",
        "insufficient_data": "📭 Insufficient historical data (currently {count} records). Please wait for more samples.",
        "ai_title": "🤖 AI Assistant - {tank}",
        "ai_input_placeholder": "Ask your question…",
        "ai_thinking": "Thinking…",
        "ai_analyzing": "Analyzing data…",
        "ai_error": "Sorry, unable to generate a response.",
        "clear_memory": "🧹 Clear Memory",
        "clear_success": "All chat memory for this Tank has been cleared.",
        "switch_tank": "🔄 Switch Tank",
        "auto_refresh_label": "Auto-refresh data (every 10s)",
        "no_api_key_warning": "DeepSeek API Key not configured for {tank}. Please add DEEPSEEK_{key} in Secrets.",
    }
}

def t(key, **kwargs):
    text = TRANSLATIONS[lang].get(key, key)
    if kwargs:
        return text.format(**kwargs)
    return text

# ---------- 语言切换（右上角，始终显示） ----------
col_title, col_lang = st.columns([3, 1])
with col_lang:
    selected_lang = st.selectbox(
        "Language / 語言",
        options=["zh", "en"],
        format_func=lambda x: "繁體中文" if x == "zh" else "English",
        index=0 if lang == "zh" else 1,
        key="lang_selector"
    )
    if selected_lang != lang:
        st.query_params.lang = selected_lang
        st.rerun()

# ---------- 封面页（如果 page 不是 monitor） ----------
if page != "monitor":
    # 读取当前 tank 参数（默认 ESP32_Tank_001）
    current_tank = st.query_params.get("tank", "ESP32_Tank_001")
    # 定义可选 tank 列表（可扩展）
    tank_options = ["ESP32_Tank_001", "ESP32_Tank_002"]  # 根据实际情况添加

    # 封面页样式
    st.markdown("""
        <style>
        .cover-container {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 70vh;
            text-align: center;
            padding: 20px;
        }
        .cover-title {
            font-size: 3.5rem;
            font-weight: bold;
            color: #1abc9c;
            margin-bottom: 0.5rem;
        }
        .cover-sub {
            font-size: 1.2rem;
            color: #555;
            margin-bottom: 1.5rem;
        }
        .cover-divider {
            width: 80px;
            height: 3px;
            background: #1abc9c;
            margin: 1rem auto;
        }
        .cover-select {
            margin: 1.5rem 0;
        }
        .cover-button {
            background-color: #1abc9c;
            color: white;
            padding: 12px 40px;
            border-radius: 30px;
            font-size: 1.2rem;
            border: none;
            cursor: pointer;
            transition: 0.3s;
            text-decoration: none;
            display: inline-block;
        }
        .cover-button:hover {
            background-color: #16a085;
            transform: scale(1.03);
        }
        </style>
    """, unsafe_allow_html=True)

    # 封面内容（固定双语）
    st.markdown("""
        <div class="cover-container">
            <div class="cover-title">🌿 藻類監測系統 Algae Monitor</div>
            <div style="font-size: 1.1rem; color: #777; margin-bottom: 0.3rem;">
                智慧監測 ｜ 即時分析 ｜ 數據驅動
            </div>
            <div style="font-size: 1.0rem; color: #999; margin-bottom: 0.3rem;">
                Intelligent Monitoring | Real-time Analysis | Data-driven
            </div>
            <div class="cover-divider"></div>
            <div style="font-size: 1.0rem; color: #444; margin: 0.5rem 0;">
                選擇培養罐 / Select Tank
            </div>
    """, unsafe_allow_html=True)

    # Tank 选择器（放置在封面中央）
    selected_tank = st.selectbox(
        label="",  # 隐藏标签
        options=tank_options,
        index=tank_options.index(current_tank) if current_tank in tank_options else 0,
        key="cover_tank_selector",
        label_visibility="collapsed"
    )

    # 进入按钮
    if st.button("🚀 進入監控 Enter Monitor", use_container_width=False):
        # 更新 URL 参数
        st.query_params.tank = selected_tank
        st.query_params.lang = lang
        st.query_params.page = "monitor"
        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)

    # 停止执行，不显示监控内容
    st.stop()

# ---------- 以下为监控页（仅当 page == "monitor" 时执行） ----------

# ---------- 自动刷新（侧边栏） ----------
auto_refresh = st.sidebar.checkbox(t("auto_refresh_label"), value=True)
if auto_refresh:
    st_autorefresh(interval=10000, key="data_refresh")

tank_id = st.query_params.get("tank", "ESP32_Tank_001")

# ---------- 工具函数（传感器数据） ----------
def convert_decimals(obj):
    if isinstance(obj, list):
        return [convert_decimals(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: convert_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, Decimal):
        return float(obj)
    return obj

def get_latest(device_id):
    resp = sensor_table.query(
        KeyConditionExpression=Key('device_id').eq(device_id),
        Limit=1,
        ScanIndexForward=False
    )
    items = resp.get('Items', [])
    return convert_decimals(items[0]) if items else None

def get_last_n(device_id, n=500):
    resp = sensor_table.query(
        KeyConditionExpression=Key('device_id').eq(device_id),
        Limit=n,
        ScanIndexForward=False
    )
    items = resp.get('Items', [])
    items.reverse()
    return convert_decimals(items)

# ---------- AI 核心功能（Function Calling） ----------
def query_sensor_data(tank_id, metric, stats='avg', limit=500):
    items = get_last_n(tank_id, n=limit)
    if not items:
        return "没有可用的历史数据。"
    df = pd.DataFrame(items)
    if metric not in df.columns:
        return f"错误：指标 '{metric}' 不存在。可用指标：temperature, ph, turbidity_ntu"
    data = df[metric].dropna()
    if len(data) == 0:
        return f"指标 '{metric}' 无有效数值。"
    if stats == 'avg':
        return f"{metric} 的平均值为 {data.mean():.2f}"
    elif stats == 'min':
        return f"{metric} 的最小值为 {data.min():.2f}"
    elif stats == 'max':
        return f"{metric} 的最大值为 {data.max():.2f}"
    elif stats == 'trend':
        half = len(data) // 2
        if half < 2:
            return "数据点太少，无法判断趋势。"
        early = data[:half].mean()
        late = data[half:].mean()
        direction = "上升" if late > early else "下降" if late < early else "平稳"
        return f"{metric} 整体趋势 {direction}，前半段平均 {early:.2f}，后半段平均 {late:.2f}"
    else:
        return f"不支持的统计方式 '{stats}'，请使用 avg, min, max, trend"

def call_deepseek_with_tools(messages, api_key, tools):
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": 0.7,
        "max_tokens": 800
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        if response.status_code != 200:
            return f"❌ API 错误：{response.status_code} - {response.text}", None
        data = response.json()
        choice = data['choices'][0]
        msg = choice['message']
        if 'tool_calls' in msg and msg['tool_calls']:
            return None, msg['tool_calls']
        else:
            return msg['content'], None
    except Exception as e:
        return f"❌ 请求异常：{e}", None

# ---------- AI 辅助函数 ----------
def get_deepseek_api_key(tank_id):
    key_name = f"DEEPSEEK_{tank_id.replace('-', '_')}"
    return st.secrets.get(key_name)

def save_message(thread_id, role, content):
    timestamp = int(time.time() * 1000)
    chat_table.put_item(
        Item={
            'thread_id': thread_id,
            'timestamp': timestamp,
            'role': role,
            'content': content
        }
    )

def load_history(thread_id, limit=50):
    resp = chat_table.query(
        KeyConditionExpression=Key('thread_id').eq(thread_id),
        Limit=limit,
        ScanIndexForward=True
    )
    items = resp.get('Items', [])
    items.sort(key=lambda x: x['timestamp'])
    return items

def clear_history(thread_id):
    resp = chat_table.query(
        KeyConditionExpression=Key('thread_id').eq(thread_id)
    )
    with chat_table.batch_writer() as batch:
        for item in resp['Items']:
            batch.delete_item(
                Key={'thread_id': item['thread_id'], 'timestamp': item['timestamp']}
            )

# ---------- 监控主界面 ----------
st.title(t("app_title"))
st.caption(f"{t('tank_label')} **{tank_id}**")

latest = get_latest(tank_id)
if latest:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(t("temp"), f"{latest.get('temperature', 'N/A')} {t('temp_unit')}")
    c2.metric(t("ph"), f"{latest.get('ph', 'N/A')}")
    c3.metric(t("turbidity"), f"{latest.get('turbidity_ntu', 'N/A')} {t('turbidity_unit')}")
    c4.metric(t("salinity"), f"{latest.get('salinity', 'N/A')} {t('salinity_unit')}")
    st.caption(f"{t('timestamp_label')} {latest.get('timestamp', 'N/A')} ms")
else:
    st.warning(t("no_data_warning"))

st.markdown("---")

# ---------- 历史趋势 ----------
st.subheader(t("history_title"))
n_points = st.slider(t("slider_label"), min_value=10, max_value=500, value=100, step=10)
history = get_last_n(tank_id, n=n_points)
if len(history) > 1:
    df = pd.DataFrame(history)
    df['timestamp'] = pd.to_numeric(df['timestamp'])
    df = df.sort_values('timestamp')
    for col in ['temperature', 'ph', 'turbidity_ntu']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    st.markdown(f"**{t('chart_temp')}**")
    st.line_chart(df.set_index('timestamp')['temperature'])
    st.markdown(f"**{t('chart_ph')}**")
    st.line_chart(df.set_index('timestamp')['ph'])
    st.markdown(f"**{t('chart_turbidity')}**")
    st.line_chart(df.set_index('timestamp')['turbidity_ntu'])
else:
    st.info(t("insufficient_data", count=len(history)))

st.markdown("---")

# ---------- AI 聊天助手 ----------
st.subheader(t("ai_title", tank=tank_id))

api_key = get_deepseek_api_key(tank_id)
if not api_key:
    st.warning(t("no_api_key_warning", tank=tank_id, key=tank_id.replace('-', '_')))
else:
    history_messages = load_history(tank_id, limit=50)
    
    chat_container = st.container()
    with chat_container:
        for msg in history_messages:
            role = msg['role']
            content = msg['content']
            with st.chat_message(role):
                st.markdown(content)

    user_input = st.chat_input(t("ai_input_placeholder"))
    if user_input:
        with st.chat_message("user"):
            st.markdown(user_input)
        save_message(tank_id, "user", user_input)

        messages = [
            {"role": "system", "content": "你是一位藻类养殖专家，你能调用工具查询历史传感器数据，根据数据回答问题。当用户询问温度、pH、浊度等历史趋势或统计时，使用 query_sensor_data 工具。"}
        ]
        for msg in history_messages[-20:]:
            messages.append({"role": msg['role'], "content": msg['content']})
        messages.append({"role": "user", "content": user_input})

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "query_sensor_data",
                    "description": "查询指定藻类培养罐（Tank）的历史传感器数据，返回统计摘要（平均值、最小值、最大值或趋势）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "tank_id": {"type": "string", "description": "培养罐的唯一标识，例如 ESP32_Tank_001"},
                            "metric": {"type": "string", "enum": ["temperature", "ph", "turbidity_ntu"], "description": "要查询的指标名称"},
                            "stats": {"type": "string", "enum": ["avg", "min", "max", "trend"], "description": "统计方式：avg, min, max, trend"}
                        },
                        "required": ["tank_id", "metric"]
                    }
                }
            }
        ]

        with st.spinner(t("ai_thinking")):
            reply, tool_calls = call_deepseek_with_tools(messages, api_key, tools)

        if tool_calls:
            tool_results = []
            for tc in tool_calls:
                fn = tc['function']
                args = json.loads(fn['arguments'])
                if 'tank_id' not in args:
                    args['tank_id'] = tank_id
                result = query_sensor_data(**args)
                tool_results.append({
                    "tool_call_id": tc['id'],
                    "role": "tool",
                    "content": result
                })
            messages.append({"role": "assistant", "content": None, "tool_calls": tool_calls})
            messages.extend(tool_results)
            with st.spinner(t("ai_analyzing")):
                final_reply, _ = call_deepseek_with_tools(messages, api_key, tools)
                reply = final_reply if final_reply else t("ai_error")

        with st.chat_message("assistant"):
            st.markdown(reply)
        save_message(tank_id, "assistant", reply)
        st.rerun()

    if st.button(t("clear_memory")):
        clear_history(tank_id)
        st.success(t("clear_success"))
        st.rerun()

# ---------- 设备切换 ----------
tank_list = ["ESP32_Tank_001", "ESP32_Tank_002"]  # 可扩展
selected = st.selectbox(t("switch_tank"), tank_list,
                        index=tank_list.index(tank_id) if tank_id in tank_list else 0)
if selected != tank_id:
    st.query_params.tank = selected
    st.rerun()