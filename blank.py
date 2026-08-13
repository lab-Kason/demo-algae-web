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

# ---------- 从 Secrets 读取凭证 ----------
AWS_ACCESS_KEY_ID = st.secrets["AWS_ACCESS_KEY_ID"]
AWS_SECRET_ACCESS_KEY = st.secrets["AWS_SECRET_ACCESS_KEY"]
AWS_DEFAULT_REGION = st.secrets["AWS_DEFAULT_REGION"]

os.environ['AWS_ACCESS_KEY_ID'] = AWS_ACCESS_KEY_ID
os.environ['AWS_SECRET_ACCESS_KEY'] = AWS_SECRET_ACCESS_KEY
os.environ['AWS_DEFAULT_REGION'] = AWS_DEFAULT_REGION

# ---------- AWS 资源 ----------
REGION = AWS_DEFAULT_REGION
SENSOR_TABLE = "TankSensorData"      # 传感器数据表
CHAT_TABLE = "ChatHistory"           # 聊天记录表（新建）

dynamodb = boto3.resource('dynamodb', region_name=REGION)
sensor_table = dynamodb.Table(SENSOR_TABLE)
chat_table = dynamodb.Table(CHAT_TABLE)

# ---------- 页面配置 ----------
st.set_page_config(page_title="Algae Box Monitor", layout="wide")
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

def get_last_n(device_id, n=100):
    resp = sensor_table.query(
        KeyConditionExpression=Key('device_id').eq(device_id),
        Limit=n,
        ScanIndexForward=False
    )
    items = resp.get('Items', [])
    items.reverse()
    return convert_decimals(items)

# ---------- AI 相关函数 ----------
def get_deepseek_api_key(tank_id):
    """根据 tank_id 返回对应的 DeepSeek API Key"""
    # 在 secrets 中预置 TANK_001, TANK_002 ...
    key_name = f"DEEPSEEK_{tank_id.replace('-', '_')}"  # 将 - 替换为 _
    return st.secrets.get(key_name)

def save_message(thread_id, role, content):
    """将一条消息存入 ChatHistory 表"""
    timestamp = int(time.time() * 1000)  # 毫秒
    chat_table.put_item(
        Item={
            'thread_id': thread_id,
            'timestamp': timestamp,
            'role': role,
            'content': content
        }
    )

def load_history(thread_id, limit=20):
    """加载最近 limit 条消息（按时间升序）"""
    resp = chat_table.query(
        KeyConditionExpression=Key('thread_id').eq(thread_id),
        Limit=limit,
        ScanIndexForward=True  # 升序（旧->新）
    )
    items = resp.get('Items', [])
    # 按时间戳排序（确保顺序）
    items.sort(key=lambda x: x['timestamp'])
    return items

def clear_history(thread_id):
    """删除该 thread 的所有聊天记录"""
    # 先扫描所有消息
    resp = chat_table.query(
        KeyConditionExpression=Key('thread_id').eq(thread_id)
    )
    with chat_table.batch_writer() as batch:
        for item in resp['Items']:
            batch.delete_item(
                Key={'thread_id': item['thread_id'], 'timestamp': item['timestamp']}
            )

def call_deepseek(messages, api_key):
    """调用 DeepSeek API（兼容 OpenAI）"""
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 500
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    if response.status_code == 200:
        return response.json()['choices'][0]['message']['content']
    else:
        return f"❌ AI 调用失败：{response.status_code} - {response.text}"

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

# ---------- AI 聊天助手（每个 Tank 独立） ----------
st.subheader(f"🤖 AI 助手 - {tank_id}")

# 获取该 Tank 的 API Key
api_key = get_deepseek_api_key(tank_id)
if not api_key:
    st.warning(f"未配置 {tank_id} 的 DeepSeek API Key，请在 Secrets 中添加 DEEPSEEK_{tank_id.replace('-', '_')}")
else:
    # 加载历史消息
    history_messages = load_history(tank_id, limit=50)
    
    # 显示聊天历史
    chat_container = st.container()
    with chat_container:
        for msg in history_messages:
            role = msg['role']
            content = msg['content']
            with st.chat_message(role):
                st.markdown(content)

    # 输入框
    user_input = st.chat_input("输入你的问题…")
    if user_input:
        # 显示用户消息
        with st.chat_message("user"):
            st.markdown(user_input)
        # 保存用户消息
        save_message(tank_id, "user", user_input)

        # 构建消息列表（系统提示 + 历史记录 + 当前问题）
        messages = [
            {"role": "system", "content": "你是一位藻类养殖专家，负责分析传感器数据并提供建议。"}
        ]
        # 添加历史记录（最多保留最近 20 条，节省 token）
        for msg in history_messages[-20:]:
            messages.append({"role": msg['role'], "content": msg['content']})
        # 添加当前问题
        messages.append({"role": "user", "content": user_input})

        # 调用 AI
        with st.spinner("思考中…"):
            reply = call_deepseek(messages, api_key)

        # 显示回复
        with st.chat_message("assistant"):
            st.markdown(reply)
        # 保存回复
        save_message(tank_id, "assistant", reply)

        # 刷新页面（显示新消息）
        st.rerun()

    # 清空记忆按钮
    if st.button("🧹 清空记忆"):
        clear_history(tank_id)
        st.success("已清空该 Tank 的所有聊天记忆")
        st.rerun()

# ---------- 设备切换 ----------
tank_list = ["ESP32_Tank_001"]   # 可扩展
selected = st.selectbox("🔄 切换 Tank", tank_list,
                        index=tank_list.index(tank_id) if tank_id in tank_list else 0)
if selected != tank_id:
    st.query_params.tank = selected
    st.rerun()