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
from streamlit_autorefresh import st_autorefresh  # 新增自动刷新组件

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

# ---------- 自动刷新（每10秒，仅在未进行聊天输入时有效） ----------
# 注意：自动刷新会重新运行整个脚本，但不会丢失聊天历史（存在DynamoDB）
# 如果用户正在输入，刷新会中断输入，因此建议在演示时使用，或提供开关
auto_refresh = st.sidebar.checkbox("自动刷新数据（每10秒）", value=True)
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

# ---------- 主界面 ----------
st.title("🌿 Algae Box Monitor")
st.caption(f"当前监控 Tank: **{tank_id}**")

latest = get_latest(tank_id)
if latest:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🌡️ 温度", f"{latest.get('temperature', 'N/A')} °C")
    c2.metric("🧪 pH", f"{latest.get('ph', 'N/A')}")
    c3.metric("💧 浊度", f"{latest.get('turbidity_ntu', 'N/A')} NTU")
    c4.metric("🧂 盐度", f"{latest.get('salinity', 'N/A')} ppt")
    st.caption(f"⏱️ 设备运行毫秒数：{latest.get('timestamp', 'N/A')} ms")
else:
    st.warning("⚠️ 暂无数据，请检查表名或设备上报。")

st.markdown("---")

# ---------- 历史趋势 ----------
st.subheader("📈 历史趋势")
n_points = st.slider("显示最近多少条记录", min_value=10, max_value=500, value=100, step=10)
history = get_last_n(tank_id, n=n_points)
if len(history) > 1:
    df = pd.DataFrame(history)
    df['timestamp'] = pd.to_numeric(df['timestamp'])
    df = df.sort_values('timestamp')
    for col in ['temperature', 'ph', 'turbidity_ntu']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    st.markdown("**温度变化**")
    st.line_chart(df.set_index('timestamp')['temperature'])
    st.markdown("**pH 变化**")
    st.line_chart(df.set_index('timestamp')['ph'])
    st.markdown("**浊度变化**")
    st.line_chart(df.set_index('timestamp')['turbidity_ntu'])
else:
    st.info(f"📭 历史数据不足（当前 {len(history)} 条），请等待更多采样点。")

st.markdown("---")

# ---------- AI 聊天助手 ----------
st.subheader(f"🤖 AI 助手 - {tank_id}")

api_key = get_deepseek_api_key(tank_id)
if not api_key:
    st.warning(f"未配置 {tank_id} 的 DeepSeek API Key，请在 Secrets 中添加 DEEPSEEK_{tank_id.replace('-', '_')}")
else:
    history_messages = load_history(tank_id, limit=50)
    
    chat_container = st.container()
    with chat_container:
        for msg in history_messages:
            role = msg['role']
            content = msg['content']
            with st.chat_message(role):
                st.markdown(content)

    user_input = st.chat_input("输入你的问题…")
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

        with st.spinner("思考中…"):
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
            with st.spinner("分析数据中…"):
                final_reply, _ = call_deepseek_with_tools(messages, api_key, tools)
                reply = final_reply if final_reply else "抱歉，无法生成回答。"

        with st.chat_message("assistant"):
            st.markdown(reply)
        save_message(tank_id, "assistant", reply)
        st.rerun()

    if st.button("🧹 清空记忆"):
        clear_history(tank_id)
        st.success("已清空该 Tank 的所有聊天记忆")
        st.rerun()

# ---------- 设备切换 ----------
tank_list = ["ESP32_Tank_001"]
selected = st.selectbox("🔄 切换 Tank", tank_list,
                        index=tank_list.index(tank_id) if tank_id in tank_list else 0)
if selected != tank_id:
    st.query_params.tank = selected
    st.rerun()
    #Hello